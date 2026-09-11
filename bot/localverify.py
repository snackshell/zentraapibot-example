"""Checking a Telebirr or Bank of Abyssinia receipt against the provider
itself, so a receipt is trusted only after being fetched — never merely
because a customer typed it, and never because a screenshot looked right.

WHY THIS IS NOT SOMETHING YOU HOST. Telebirr's own receipt lookup is
reachable only from an Ethiopian IP address. That is the entire reason this
is a service rather than a library: a bot on a VPS in Frankfurt cannot ask
Telebirr anything, no matter how the code is written. So verification goes
through Zentra, which runs that lookup from inside Ethiopia on your behalf.

YOU DO NOT CONFIGURE ANYTHING FOR THIS. No URL, no separate key. The
request is authenticated with the SAME Zentra API key this bot already uses
to buy products — one credential, one place to revoke it. Set your Telebirr
number and name in .env and the rail works.

Bank of Abyssinia has no such geographic restriction. It goes through the
same endpoint for consistency, but a self-hoster can run that half
anywhere in the world (see below); Telebirr genuinely cannot move.

IF YOU WANT TO RUN YOUR OWN. Set TELEBIRR_VERIFY_URL and
TELEBIRR_VERIFY_KEY and this client talks to your own
[LocalPaymentVerify](https://github.com/snackshell/localpaymentverify)
instance instead, in its own `x-api-key` shape, and Zentra is not involved.
Worth doing if you already have an Ethiopian server, want the lower
latency, or simply do not want a dependency. Leave both blank and Zentra
handles it.

WHAT THIS IS AND IS NOT. It is a transport. It asks one question — "what
does the provider say this reference is?" — and hands back the answer.
Every decision that follows (whether WE were paid, whether the amount is
real, whether it has been used before) lives in localpay.py and the
database, not here. A verifier that both fetches and decides is a verifier
whose bugs are indistinguishable from its policy.

FAILING CLOSED. Every failure mode below is a distinct exception with its
own honest message: a timeout is not "no payment", a rejected key is not
"not found". Collapsing those is how "we could not reach the verifier"
turns into "your payment does not exist" for somebody holding a receipt —
and the bot's answer to that difference matters, because an unreachable
verifier must leave the request for a human, not refuse the customer.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

log = logging.getLogger(__name__)

TELEBIRR = "telebirr"
ABYSSINIA = "abyssinia"

# Telebirr: ten alphanumerics. Abyssinia: FT plus ten more, and a five-digit
# account suffix the customer reads off their own statement.
TELEBIRR_REFERENCE = re.compile(r"^[A-Za-z0-9]{10}$")
ABYSSINIA_REFERENCE = re.compile(r"^FT[A-Za-z0-9]{10}$", re.I)
ABYSSINIA_SUFFIX = re.compile(r"^\d{5}$")


class VerifierError(Exception):
    """Something went wrong asking. The message is safe to show a customer.

    `operator` marks the ones that are the deployment's own problem — a
    rejected key, a quota run dry, the service unreachable — rather than
    the customer's; those are worth a person looking at, a reference that
    does not exist is not.
    """

    def __init__(self, message: str, *, operator: bool = False):
        super().__init__(message)
        self.operator = operator


class Unavailable(VerifierError):
    """Verification could not be reached, or says it is not ready."""


class Timeout(VerifierError):
    """It was reached and did not answer in time."""


class NotFound(VerifierError):
    """The provider has no such completed transaction."""


class BadRequest(VerifierError):
    """The reference is not a shape this verifier accepts."""


def mask(value: Any) -> str:
    """A reference, recognisable in a log line and useless in a leak."""
    text = str(value or "")
    if len(text) <= 4:
        return "*" * len(text)
    return f"{text[:2]}{'*' * (len(text) - 4)}{text[-2:]}"


def detect(reference: str) -> str | None:
    """Which provider a reference belongs to, by shape alone — the same
    rule the service applies, so this bot and it agree on what a reference
    IS before either goes near a network."""
    reference = (reference or "").strip()
    if ABYSSINIA_REFERENCE.match(reference):
        return ABYSSINIA
    if TELEBIRR_REFERENCE.match(reference):
        return TELEBIRR
    return None


def normalise(reference: str) -> str:
    """The one spelling of a reference that gets stored or compared.
    Upper-cased and trimmed — "dhi9w300tb" and "DHI9W300TB" are one
    receipt, and treating them as two would be a replay guard anyone could
    step around by holding shift."""
    return (reference or "").strip().upper()


def money(raw: Any) -> Decimal | None:
    """A provider's own amount as an exact Decimal, or None if it is not
    one. These are read off a provider's own page, so they can arrive as
    "1,234.56" or "1234.56 Birr" — never a float, which has already lost
    the precision a payment figure cannot afford to lose."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        return Decimal(str(raw))

    text = str(raw).strip()
    if not text:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    if not re.match(r"^-?\d+(\.\d+)?$", cleaned or ""):
        return None
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ArithmeticError):
        return None


class Verifier:
    """Asks about one receipt, through Zentra or through your own instance.

    THE TWO MODES DIFFER ONLY IN WHERE THEY POINT AND HOW THEY
    AUTHENTICATE. Both answer in the same shape — {"success": true,
    "provider": ..., "data": {...}} — so everything downstream is
    identical, and switching from Zentra's hosted verification to your own
    server changes nothing but two lines of .env.
    """

    def __init__(self, *, zentra_base_url: str = "", zentra_api_key: str = "",
                 self_host_url: str = "", self_host_key: str = "",
                 connect_timeout: float = 5.0, read_timeout: float = 65.0):
        self._zentra_base = (zentra_base_url or "").rstrip("/")
        self._zentra_key = zentra_api_key or ""
        self._own_base = (self_host_url or "").rstrip("/")
        self._own_key = self_host_key or ""
        self._timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self._client: httpx.AsyncClient | None = None

    @property
    def self_hosted(self) -> bool:
        """Whether this deployment runs its own verification. Both halves
        are required — a URL with no key would be an open endpoint, and
        silently falling back to Zentra when someone MEANT to self-host
        would send receipts somewhere they did not intend."""
        return bool(self._own_base and self._own_key)

    @property
    def configured(self) -> bool:
        """Whether a receipt can be checked at all. True for almost every
        deployment: having a Zentra API key is enough, and this bot cannot
        run without one."""
        return self.self_hosted or bool(self._zentra_base and self._zentra_key)

    @property
    def describe(self) -> str:
        """For one startup log line — never includes a key."""
        if self.self_hosted:
            return f"your own instance at {self._own_base}"
        if self.configured:
            return f"Zentra ({self._zentra_base}), with your Zentra API key"
        return "not available"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _request_for(self, provider: str, reference: str, suffix: str | None):
        """Where to ask, how to authenticate, and what to send.

        Kept apart from verify() so the two modes' assembly can be tested
        without a network at all — see tests/test_localverify.py.
        """
        payload: dict[str, str] = {"reference": reference.strip()}
        if provider == ABYSSINIA:
            payload["suffix"] = (suffix or "").strip()

        if self.self_hosted:
            # LocalPaymentVerify's own shape: one /verify for both
            # providers, authenticated with its own key.
            return f"{self._own_base}/verify", {"x-api-key": self._own_key}, payload

        # Zentra's API: a path per provider, authenticated with the SAME
        # key this bot buys products with. No second credential exists.
        return (
            f"{self._zentra_base}/v1/verify/{provider}",
            {"Authorization": f"Bearer {self._zentra_key}"},
            payload,
        )

    async def verify(self, reference: str, suffix: str | None = None) -> dict:
        """Ask about one reference. Returns the provider's `data` block.

        The key travels in a header built here and never reaches a log
        line, a URL, or an exception message.
        """
        if not self.configured:
            raise Unavailable(
                "Payment verification is not available.", operator=True)

        provider = detect(reference)
        if provider is None:
            raise BadRequest(
                "That is not a reference we recognise. A Telebirr receipt "
                "number is 10 letters and digits; a Bank of Abyssinia one "
                "starts with FT and is 12 characters."
            )
        if provider == ABYSSINIA and not ABYSSINIA_SUFFIX.match(suffix or ""):
            raise BadRequest(
                "Bank of Abyssinia also needs the last 5 digits of the "
                "account the money came from."
            )

        url, headers, payload = self._request_for(provider, reference, suffix)
        log.info("Verifying %s reference %s via %s", provider, mask(reference),
                 "own instance" if self.self_hosted else "Zentra")

        try:
            response = await self._http().post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise Timeout(
                "The payment check took too long. Your money is safe — try "
                "again in a moment."
            ) from exc
        except httpx.HTTPError as exc:
            raise Unavailable(
                "Payment verification is unavailable right now. Your money "
                "is safe — try again shortly.", operator=True,
            ) from exc

        return self._read(response, provider, reference)

    def _read(self, response: httpx.Response, provider: str, reference: str) -> dict:
        try:
            body = response.json()
        except ValueError:
            log.error("Verification returned non-JSON (HTTP %s, %d bytes)",
                      response.status_code, len(response.content or b""))
            raise Unavailable(
                "Payment verification returned something unreadable. Your "
                "money is safe — support has been told.", operator=True,
            ) from None

        if not isinstance(body, dict):
            raise Unavailable(
                "Payment verification returned an unexpected answer.",
                operator=True)

        detail = str(body.get("error") or body.get("message") or "")[:300]
        status = response.status_code

        if status in (401, 403):
            # Hosted: the Zentra API key is wrong, revoked, or not allowed
            # to verify. Self-hosted: the instance rejected its own key.
            log.error("Verification rejected our credentials (HTTP %s): %s",
                      status, detail)
            raise Unavailable(
                "Payment verification is misconfigured — check your Zentra "
                "API key. Support has been told; your money is safe.",
                operator=True)
        if status == 429:
            # A quota, not a payment problem. The request must land in the
            # review queue rather than telling a paying customer "no".
            log.error("Verification is rate limited: %s", detail)
            raise Unavailable(
                "Payment checks are busy right now. Your receipt has been "
                "saved and will be reviewed shortly.", operator=True)
        if status == 503:
            log.error("Verification is not ready: %s", detail)
            raise Unavailable(
                "Payment verification is not ready. Your money is safe — "
                "try again shortly.", operator=True)
        if status == 400:
            raise BadRequest(
                "That reference is not in a format we can check. Copy it "
                "again from your receipt.")
        if status == 404:
            raise NotFound(
                "We could not find that payment. Check the reference, and "
                "give it a minute if you have only just paid.")
        if status in (422, 502):
            log.info("Verification could not complete %s %s: %s",
                     provider, mask(reference), detail)
            raise NotFound(
                "That payment could not be confirmed. Check the reference "
                "is the one on your receipt, and that the payment completed.")
        if status >= 500:
            log.error("Verification failed on %s %s (HTTP %s): %s",
                      provider, mask(reference), status, detail)
            raise Unavailable(
                "Payment verification is having trouble. Your money is "
                "safe — try again shortly.", operator=True)
        if status != 200 or body.get("success") is not True:
            log.info("Verification said no for %s %s (HTTP %s): %s",
                     provider, mask(reference), status, detail)
            raise NotFound(
                "That payment could not be confirmed. Check the reference "
                "on your receipt.")

        data = body.get("data")
        if not isinstance(data, dict) or not data:
            log.error("Verification reported success with no data for %s %s",
                      provider, mask(reference))
            raise Unavailable(
                "Payment verification returned an incomplete answer. "
                "Support has been told.", operator=True)

        reported = str(body.get("provider") or "").strip().lower()
        if reported and reported != provider:
            log.error("Verification used %s for a reference we read as %s",
                      reported, provider)
            raise Unavailable(
                "Payment verification returned an unexpected result. "
                "Support has been told.", operator=True)

        return {"provider": provider, "data": data}
