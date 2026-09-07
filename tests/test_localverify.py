"""The LocalPaymentVerify client: shape detection, request assembly, and
mapping every HTTP outcome to a distinct, honest exception.

httpx.MockTransport stands in for the verifier service — no real network.
Run:  python -m tests.test_localverify
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx

from bot.localverify import (
    BadRequest,
    NotFound,
    Unavailable,
    Verifier,
    detect,
    mask,
    money,
    normalise,
)


def with_transport(client: Verifier, handler) -> None:
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://fake-verifier.example")


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    # ---- shape detection, no network at all --------------------------------

    print("\ndetect() and normalise()")

    assert detect("ABCD123456") == "telebirr"
    assert detect("FT24AB12CD34") == "abyssinia"
    assert detect("ft24ab12cd34") == "abyssinia"
    assert detect("not a reference") is None
    assert detect("") is None
    ok("a reference's shape alone says which provider it belongs to")

    assert normalise(" dhi9w300tb ") == "DHI9W300TB"
    ok("normalise() upper-cases and trims — the one spelling that gets stored")

    assert mask("ABCD123456") == "AB******56"
    assert mask("AB") == "**"
    ok("mask() shows just enough to match a log line, never enough to replay")

    print("\nmoney()")

    assert money("1,234.56") == Decimal("1234.56")
    assert money("1234.56 Birr") == Decimal("1234.56")
    assert money(0.1) == Decimal("0.1")
    assert money(None) is None
    assert money("not a number") is None
    assert money(True) is None  # bool is an int subclass; must not become 1
    ok("money() reads a provider's own formatting, and refuses what it cannot")

    # ---- request assembly ---------------------------------------------------

    print("\nverify() request assembly")

    seen = {}

    def echo_handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["header"] = request.headers.get("x-api-key")
        seen["body"] = request.content
        return httpx.Response(200, json={
            "success": True, "provider": "telebirr",
            "data": {"receiptNo": "ABCD123456"},
        })

    client = Verifier("https://fake-verifier.example", "secret-key")
    with_transport(client, echo_handler)

    await client.verify("ABCD123456")
    assert seen["path"] == "/verify"
    assert seen["header"] == "secret-key"
    ok("the API key travels as x-api-key, never in the URL or the body")

    def suffix_handler(request: httpx.Request) -> httpx.Response:
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "success": True, "provider": "abyssinia",
            "data": {"reference": "FT24AB12CD34", "success": True},
        })

    client2 = Verifier("https://fake-verifier.example", "secret-key")
    with_transport(client2, suffix_handler)
    await client2.verify("FT24AB12CD34", "56789")
    assert seen["body"] == {"reference": "FT24AB12CD34", "suffix": "56789"}
    ok("an Abyssinia reference sends its suffix alongside it")

    # ---- shape rejected before any network call ----------------------------

    print("\nBad shapes never reach the network")

    never_called = Verifier("https://fake-verifier.example", "secret-key")

    def fail_if_called(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not have made a network call")

    with_transport(never_called, fail_if_called)

    try:
        await never_called.verify("not a real reference")
        raise AssertionError("expected BadRequest")
    except BadRequest:
        pass
    ok("an unrecognisable reference is refused before any request is made")

    try:
        await never_called.verify("FT24AB12CD34")  # abyssinia, no suffix
        raise AssertionError("expected BadRequest")
    except BadRequest:
        pass
    ok("an Abyssinia reference with no suffix is refused the same way")

    # ---- HTTP status mapping -------------------------------------------------

    print("\nEvery status maps to its own honest exception")

    def status_handler(status: int, body: dict | None = None):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=body or {})
        return handler

    cases = [
        (401, {"error": "bad key"}, Unavailable),
        (503, {"error": "not configured"}, Unavailable),
        (400, {"error": "bad shape"}, BadRequest),
        (404, {"error": "no such transaction"}, NotFound),
        (422, {"error": "could not confirm"}, NotFound),
        (502, {"error": "upstream failed"}, NotFound),
        (500, {"error": "boom"}, Unavailable),
    ]
    for status, body, expected in cases:
        client = Verifier("https://fake-verifier.example", "secret-key")
        with_transport(client, status_handler(status, body))
        try:
            await client.verify("ABCD123456")
            raise AssertionError(f"expected {expected.__name__} for HTTP {status}")
        except expected:
            pass
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"HTTP {status} raised {type(exc).__name__}, expected {expected.__name__}"
            ) from exc
    ok("401/503/400/404/422/502/500 each map to their own exception, not a generic one")

    # ---- success shapes that are not actually usable -------------------------

    print("\nA 200 that is not really an answer is still refused")

    client = Verifier("https://fake-verifier.example", "secret-key")
    with_transport(client, status_handler(200, {"success": False}))
    try:
        await client.verify("ABCD123456")
        raise AssertionError("expected NotFound")
    except NotFound:
        pass
    ok("HTTP 200 with success=false is not treated as a real answer")

    client = Verifier("https://fake-verifier.example", "secret-key")
    with_transport(client, status_handler(200, {"success": True, "provider": "telebirr"}))
    try:
        await client.verify("ABCD123456")
        raise AssertionError("expected Unavailable")
    except Unavailable:
        pass
    ok("success with no data block is an operator problem, not 'not found'")

    client = Verifier("https://fake-verifier.example", "secret-key")
    with_transport(client, status_handler(
        200, {"success": True, "provider": "abyssinia", "data": {"reference": "x"}}))
    try:
        await client.verify("ABCD123456")  # a telebirr-shaped reference
        raise AssertionError("expected Unavailable")
    except Unavailable:
        pass
    ok("the verifier reporting a different provider than we detected is refused, not trusted")

    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    client = Verifier("https://fake-verifier.example", "secret-key")
    with_transport(client, not_json)
    try:
        await client.verify("ABCD123456")
        raise AssertionError("expected Unavailable")
    except Unavailable:
        pass
    ok("a non-JSON body (a proxy error page) is refused, never parsed as an answer")

    # ---- an unconfigured client fails closed, before any network call --------

    print("\nAn unconfigured client")

    blank = Verifier("", "")
    assert blank.configured is False
    try:
        await blank.verify("ABCD123456")
        raise AssertionError("expected Unavailable")
    except Unavailable as exc:
        assert exc.operator is True
    ok("no base URL or key configured raises operator=True, not a network attempt")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
