"""Reading a receipt reference off a screenshot, so a customer does not
have to type ten characters correctly.

THE READING IS NOT EVIDENCE AND CANNOT CREDIT ANYTHING. This is the most
important sentence in the file. A vision model looking at a picture is
optical character recognition, not verification — a screenshot proves
nothing about whether money moved, and anyone can produce a convincing one
in a minute. So this module extracts exactly two things, the PROVIDER and
the REFERENCE, and throws away everything else the model happened to read.

The amount, the receiver, the date and the status are DISCARDED ON PURPOSE.
Every one of those facts has to come from the provider itself, through
localverify.py, and then pass localpay.py's own checks. Keeping them here
would invite using them, and the first time somebody used them would be the
first time a Photoshopped screenshot bought real goods. What comes out of
this module is fed through the ordinary verification path exactly as though
the customer had typed it.

YOU BRING YOUR OWN MODEL AND KEY. Vision calls cost money per image, so
they are billed to whoever incurs them: set OPENROUTER_API_KEY and (if you
want something other than the default) OPENROUTER_MODEL in .env. Leave
OPENROUTER_API_KEY blank and this feature is simply off — customers type
the reference, which has always worked and costs nothing. OpenRouter is
used because one key reaches many vendors' vision models, so picking a
cheaper or better one later is a one-line change rather than a rewrite.
"""

from __future__ import annotations

import base64
import json
import logging
import re

import httpx

from .localverify import detect, mask

log = logging.getLogger(__name__)

# Big enough for any phone screenshot, small enough that a mis-sent video
# frame or a scanned PDF page does not get uploaded to a paid API by
# accident.
MAX_IMAGE_BYTES = 8_000_000

# Asks for two fields and nothing else. Deliberately short: a longer prompt
# inviting the model to describe the payment would produce fields this
# module then has to be trusted to ignore.
PROMPT = (
    "This image is an Ethiopian payment receipt, from Telebirr or Bank of "
    "Abyssinia. Reply with ONLY a JSON object, no prose, no code fences:\n"
    '{"provider": "telebirr" or "abyssinia", "reference": "<the receipt '
    'or transaction reference exactly as printed>"}\n'
    "A Telebirr reference is 10 letters and digits. A Bank of Abyssinia one "
    'starts with FT and is 12 characters. If you cannot read a reference '
    'confidently, reply {"provider": null, "reference": null}.'
)


class ScanError(Exception):
    """The image could not be turned into a reference. Always recoverable —
    the customer types the reference instead, which is where they started,
    not a dead end.

    `operator` marks the deployment's own problem (a rejected key, no
    credit left) rather than an unreadable picture.
    """

    def __init__(self, message: str, *, operator: bool = False):
        super().__init__(message)
        self.operator = operator


class ReceiptScanner:
    """One OpenRouter client, shared across requests."""

    def __init__(self, api_key: str, *, model: str = "",
                 base_url: str = "https://openrouter.ai/api/v1",
                 timeout: float = 45.0):
        self._key = api_key or ""
        # A small, cheap, widely-available vision model. Overridable
        # precisely so a reseller can trade cost against accuracy without
        # touching code.
        self._model = model or "google/gemini-2.0-flash-001"
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self._key)

    @property
    def model(self) -> str:
        return self._model

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def read(self, image: bytes, mime: str = "image/jpeg") -> dict:
        """Return {"provider": ..., "reference": ...} — and nothing else.

        Raises ScanError for every failure, because every failure has the
        same safe answer: ask the customer to type it.
        """
        if not self.configured:
            raise ScanError(
                "Receipt reading is not set up.", operator=True)
        if not image:
            raise ScanError("That image was empty. Send the receipt again.")
        if len(image) > MAX_IMAGE_BYTES:
            raise ScanError(
                "That image is too large. Send a screenshot rather than a "
                "full-resolution photo.")

        data_url = f"data:{mime or 'image/jpeg'};base64,{base64.b64encode(image).decode()}"
        payload = {
            "model": self._model,
            # No creativity wanted: this is transcription, and a model
            # inventing a plausible-looking reference is worse than one
            # admitting it cannot read the picture.
            "temperature": 0,
            "max_tokens": 200,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
        }

        try:
            response = await self._http().post(
                f"{self._base}/chat/completions", json=payload,
                headers={"Authorization": f"Bearer {self._key}"},
            )
        except httpx.TimeoutException as exc:
            raise ScanError(
                "Reading that receipt took too long. Send the reference as "
                "text instead."
            ) from exc
        except httpx.HTTPError as exc:
            raise ScanError(
                "Receipt reading is unavailable right now. Send the "
                "reference as text instead.", operator=True,
            ) from exc

        if response.status_code in (401, 403):
            log.error("OpenRouter rejected the API key (HTTP %s).", response.status_code)
            raise ScanError(
                "Receipt reading is misconfigured. Send the reference as "
                "text instead.", operator=True)
        if response.status_code == 402:
            log.error("OpenRouter reports no remaining credit.")
            raise ScanError(
                "Receipt reading has run out of credit. Send the reference "
                "as text instead.", operator=True)
        if response.status_code == 429:
            raise ScanError(
                "Receipt reading is busy. Send the reference as text instead.",
                operator=True)
        if response.status_code != 200:
            log.error("OpenRouter returned HTTP %s.", response.status_code)
            raise ScanError(
                "Receipt reading failed. Send the reference as text instead.",
                operator=True)

        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            log.error("OpenRouter returned a shape we do not understand.")
            raise ScanError(
                "Receipt reading returned something unreadable. Send the "
                "reference as text instead.", operator=True) from None

        return self._extract(text)

    def _extract(self, text: str) -> dict:
        """Pull the two fields out of the model's reply, and TRUST NEITHER.

        The shape is re-derived from the reference itself with detect(). If
        the model's claimed provider disagrees with what the reference
        actually looks like, the reading is not good enough to act on — a
        model that cannot get the provider right from a reference it just
        read is not a model whose reference should be believed either.

        Kept apart from read() so the whole of this reasoning is testable
        with no network and no API key — see tests/test_receiptscan.py.
        """
        raw = str(text or "").strip()
        # Models wrap JSON in ```json fences no matter how firmly asked not
        # to. Stripping them is kinder than failing the customer over it.
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()

        try:
            parsed = json.loads(raw)
        except ValueError:
            # Last resort: find a bare reference in prose. Still passes
            # through detect() below, so this loosens the reading, never
            # the verification.
            found = re.search(r"\b(FT[A-Za-z0-9]{10}|[A-Za-z0-9]{10})\b", raw)
            if not found:
                raise ScanError(
                    "I could not read a reference from that image. Send it "
                    "as text instead — it is on your receipt.") from None
            parsed = {"reference": found.group(1), "provider": None}

        if not isinstance(parsed, dict):
            raise ScanError(
                "I could not read a reference from that image. Send it as "
                "text instead.")

        reference = str(parsed.get("reference") or "").strip()
        claimed = str(parsed.get("provider") or "").strip().lower()

        by_shape = detect(reference)
        if by_shape is None or (claimed and claimed != by_shape):
            log.info("Extraction gave provider=%s reference=%s, which do not agree",
                     claimed or "?", mask(reference))
            raise ScanError(
                "I could not read a valid reference from that image. Send "
                "it as text instead.")

        log.info("Read a %s reference from an image: %s", by_shape, mask(reference))
        # ONLY these two keys. Whatever else the model said is gone.
        return {"provider": by_shape, "reference": reference}
