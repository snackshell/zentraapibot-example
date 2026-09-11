"""Reading a reference off a screenshot — and the rule that makes it safe
to offer at all: the reading is never evidence.

httpx.MockTransport stands in for OpenRouter. No key, no network, no cost.
Run:  python -m tests.test_receiptscan
"""

from __future__ import annotations

import asyncio
import json

import httpx

from bot.receiptscan import MAX_IMAGE_BYTES, ReceiptScanner, ScanError

IMAGE = b"\xff\xd8\xff\xe0 not really a jpeg, but bytes are bytes"


def scanner(**kw) -> ReceiptScanner:
    return ReceiptScanner("sk-or-v1-test", **kw)


def with_transport(client: ReceiptScanner, handler) -> None:
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


def replies(content: str, status: int = 200):
    """A handler answering the way OpenRouter's chat-completions does."""
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "nope"}})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}]})
    return handler


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    async def scan_fails(client, image=IMAGE, *, operator=None) -> ScanError:
        try:
            await client.read(image)
        except ScanError as exc:
            if operator is not None:
                assert exc.operator is operator, f"operator={exc.operator}"
            return exc
        raise AssertionError("expected ScanError, nothing was raised")

    # ---- off unless a key is brought ----------------------------------------

    print("\nOff unless the reseller brings a key")

    blank = ReceiptScanner("")
    assert blank.configured is False
    await scan_fails(blank, operator=True)
    ok("with no OpenRouter key, scanning fails closed and never calls anything")

    assert scanner().configured is True
    assert "gemini" in scanner().model
    assert scanner(model="qwen/qwen-2.5-vl-7b-instruct").model.startswith("qwen/")
    ok("a key turns it on; the model has a default and is overridable")

    # ---- the happy path ------------------------------------------------------

    print("\nReading a reference")

    seen: dict = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content":
            '{"provider": "telebirr", "reference": "ABCD123456"}'}}]})

    client = scanner()
    with_transport(client, capture)
    found = await client.read(IMAGE)
    assert found == {"provider": "telebirr", "reference": "ABCD123456"}
    ok("a clean answer yields exactly the provider and the reference")

    assert seen["auth"] == "Bearer sk-or-v1-test"
    assert seen["body"]["temperature"] == 0
    ok("the reseller's own key is sent, at temperature 0 — transcription, not invention")

    # ---- THE RULE: the reading is never evidence ------------------------------

    print("\nThe reading is never evidence")

    # The model is asked for two fields, but a model will happily volunteer
    # an amount, a receiver and a 'verified' flag. Every one of those must be
    # gone from what this module returns — if any survived, a Photoshopped
    # screenshot could start deciding money.
    chatty = ('{"provider": "telebirr", "reference": "ABCD123456", '
              '"amount": "999999", "receiver": "Zentra Reseller", '
              '"receiverAccount": "0912345678", "status": "Completed", '
              '"verified": true, "date": "2026-01-01 10:00:00"}')
    client = scanner()
    with_transport(client, replies(chatty))
    found = await client.read(IMAGE)
    assert set(found.keys()) == {"provider", "reference"}, found
    ok("an amount, receiver, status, date and even a 'verified: true' the model "
       "volunteered are ALL discarded — only provider and reference survive")

    for leaked in ("amount", "receiver", "receiverAccount", "status", "verified", "date"):
        assert leaked not in found
    ok("nothing a screenshot claims about the money can reach the credit path")

    # ---- a model that cannot read, or reads nonsense --------------------------

    print("\nWhen the reading is not good enough to act on")

    client = scanner()
    with_transport(client, replies('{"provider": null, "reference": null}'))
    await scan_fails(client)
    ok("a model admitting it cannot read the picture is a clean refusal")

    client = scanner()
    with_transport(client, replies('{"provider": "telebirr", "reference": "TOO-SHORT"}'))
    await scan_fails(client)
    ok("a reference that matches no provider's shape is refused, not passed on")

    # The model claims Telebirr; the reference is unmistakably Abyssinia's
    # shape. A model that cannot get that right is not one whose reading of
    # the digits should be believed either.
    client = scanner()
    with_transport(client, replies('{"provider": "telebirr", "reference": "FT24AB12CD34"}'))
    await scan_fails(client)
    ok("a claimed provider disagreeing with the reference's own shape is refused")

    # But when the model omits the provider entirely, the shape decides —
    # that is re-derivation, not trust.
    client = scanner()
    with_transport(client, replies('{"reference": "FT24AB12CD34"}'))
    found = await client.read(IMAGE)
    assert found == {"provider": "abyssinia", "reference": "FT24AB12CD34"}
    ok("with no claimed provider, the reference's shape decides — detect() is the authority")

    # ---- real-world sloppiness -----------------------------------------------

    print("\nThe mess models actually return")

    client = scanner()
    with_transport(client, replies('```json\n{"provider": "telebirr", "reference": "ABCD123456"}\n```'))
    found = await client.read(IMAGE)
    assert found["reference"] == "ABCD123456"
    ok("a ```json fence is stripped rather than failing the customer over it")

    client = scanner()
    with_transport(client, replies("The receipt number is ABCD123456, paid to ..."))
    found = await client.read(IMAGE)
    assert found == {"provider": "telebirr", "reference": "ABCD123456"}
    ok("prose instead of JSON still yields a reference — and still goes through detect()")

    client = scanner()
    with_transport(client, replies("I'm sorry, I can't help with that."))
    await scan_fails(client)
    ok("a refusal or an unreadable reply is a clean ScanError, never a guess")

    # ---- transport failures --------------------------------------------------

    print("\nWhen OpenRouter itself says no")

    for status, operator in [(401, True), (402, True), (429, True), (500, True)]:
        client = scanner()
        with_transport(client, replies("", status=status))
        exc = await scan_fails(client, operator=operator)
        assert "text instead" in str(exc), str(exc)
    ok("a rejected key, no credit, rate limiting and a 5xx all say 'type it instead'")

    def malformed(request: httpx.Request) -> httpx.Response:
        # A 200 whose top-level shape is not what OpenRouter documents.
        return httpx.Response(200, json={"choices": "not a list"})

    client = scanner()
    with_transport(client, malformed)
    await scan_fails(client, operator=True)
    ok("a response shape we do not understand is refused, not indexed into blindly")

    # ---- image sanity --------------------------------------------------------

    print("\nThe image itself")

    client = scanner()
    with_transport(client, replies(""))
    await scan_fails(client, b"")
    ok("an empty image is refused before any paid call is made")

    await scan_fails(client, b"x" * (MAX_IMAGE_BYTES + 1))
    ok("an oversized image is refused locally, not uploaded to a paid API first")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
