"""The client for LocalPaymentVerify — an optional local service that
checks a Telebirr or Bank of Abyssinia reference against the provider
itself, so a receipt is trusted only after being fetched, not merely typed.

WHAT THIS IS AND IS NOT. It is a transport. It asks one question — "what
does the provider say this reference is?" — and hands back the answer.
Every decision that follows (who it belongs to, whether the amount is
real, whether WE were paid, whether it has been used before) lives in
localpay.py and the database, not here. A verifier that both fetches and
decides is a verifier whose bugs are indistinguishable from its policy —
this project keeps them apart for the same reason ZentraShopBot's own
verifier.py does.

WHERE IT RUNS. This client talks to whatever LOCAL_VERIFY_URL points at —
typically a LocalPaymentVerify instance on the same box's loopback
interface (github.com/snackshell/localpaymentverify), so the service's own
provider credentials never reach this bot.

SCOPE REDUCTION FROM ZENTRASHOPBOT'S OWN CLIENT: no receipt-image reading
(`/verify-image`). A customer types the reference off their own receipt;
teaching a bot to read a photograph is a real feature ZentraShopBot ships,
but it is optical convenience, not a payment-safety requirement, and this
starter draws its line at what money-handling actually needs — the same
reasoning behind bot/chain/rpc.py polling one endpoint instead of running
a failover pool.

FAILING CLOSED. Every failure mode below is a distinct exception with its
own honest message: a timeout is not "no payment", a bad API key is not
"not found". Collapsing those is how "we could not reach the verifier"
turns into "your payment does not exist" for somebody holding a receipt.
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

    `operator` marks the ones that are the deployment's own problem — a bad
    API key, the service not running — rather than the customer's; those
    are worth a person looking at, a missing reference is not.
    """

    def __init__(self, message: str, *, operator: bool = False):
        super().__init__(message)
        self.operator = operator


class Unavailable(VerifierError):
    """The verifier could not be reached, or says it is not ready."""


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
    rule the verifier applies, so the bot and the service agree on what a
    reference IS before either goes near a network."""
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
    one. The verifier scrapes these off a provider's own page, so they can
    arrive as "1,234.56" or "1234.56 Birr" — never a float, which has
    already lost the precision a payment figure cannot afford to lose."""
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
    """A thin, shared HTTP client for one LocalPaymentVerify instance."""

    def __init__(self, base_url: str, api_key: str, *,
                 connect_timeout: float = 5.0, read_timeout: float = 65.0):
        self._base = (base_url or "").rstrip("/")
        self._key = api_key or ""
        self._timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self._base and self._key)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base, timeout=self._timeout,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def verify(self, reference: str, suffix: str | None = None) -> dict:
        """Ask the provider about one reference. Returns its `data` block.

        The API key travels in a header built here and never reaches a log
        line, a URL, or an exception message.
        """
        if not self.configured:
            raise Unavailable(
                "Payment verification is not configured.", operator=True)

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

        payload: dict[str, str] = {"reference": reference.strip()}
        if provider == ABYSSINIA:
            payload["suffix"] = (suffix or "").strip()

        log.info("Verifying %s reference %s", provider, mask(reference))

        try:
            response = await self._http().post(
                "/verify", json=payload, headers={"x-api-key": self._key},
            )
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
            log.error("Verifier returned non-JSON (HTTP %s, %d bytes)",
                      response.status_code, len(response.content or b""))
            raise Unavailable(
                "Payment verification returned something unreadable. Your "
                "money is safe — support has been told.", operator=True,
            ) from None

        if not isinstance(body, dict):
            raise Unavailable(
                "Payment verification returned an unexpected answer.",
                operator=True)

        detail = str(body.get("error") or "")[:300]
        status = response.status_code

        if status == 401:
            log.error("Verifier rejected our API key.")
            raise Unavailable(
                "Payment verification is misconfigured. Support has been "
                "told — your money is safe.", operator=True)
        if status == 503:
            log.error("Verifier is not configured: %s", detail)
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
            log.info("Verifier could not complete %s %s: %s",
                     provider, mask(reference), detail)
            raise NotFound(
                "That payment could not be confirmed. Check the reference "
                "is the one on your receipt, and that the payment completed.")
        if status >= 500:
            log.error("Verifier failed on %s %s (HTTP %s): %s",
                      provider, mask(reference), status, detail)
            raise Unavailable(
                "Payment verification is having trouble. Your money is "
                "safe — try again shortly.", operator=True)
        if status != 200 or body.get("success") is not True:
            log.info("Verifier said no for %s %s (HTTP %s): %s",
                     provider, mask(reference), status, detail)
            raise NotFound(
                "That payment could not be confirmed. Check the reference "
                "on your receipt.")

        data = body.get("data")
        if not isinstance(data, dict) or not data:
            log.error("Verifier reported success with no data for %s %s",
                      provider, mask(reference))
            raise Unavailable(
                "Payment verification returned an incomplete answer. "
                "Support has been told.", operator=True)

        reported = str(body.get("provider") or "").strip().lower()
        if reported and reported != provider:
            log.error("Verifier used %s for a reference we read as %s",
                      reported, provider)
            raise Unavailable(
                "Payment verification returned an unexpected result. "
                "Support has been told.", operator=True)

        return {"provider": provider, "data": data}
