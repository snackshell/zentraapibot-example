"""Verification's transport: where it points, how it authenticates, and how
every HTTP outcome maps to an honest, distinct exception.

httpx.MockTransport stands in for the service — no real network.
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

ZENTRA_BASE = "https://api.zentradigital.shop"
ZENTRA_KEY = "zen_live_" + "a" * 40

OK_TELEBIRR = {
    "success": True, "provider": "telebirr",
    "data": {"receiptNo": "ABCD123456"},
}


def hosted(**kw) -> Verifier:
    """A reseller who configured nothing but their Zentra key — the
    default, and what almost every deployment looks like."""
    return Verifier(zentra_base_url=ZENTRA_BASE, zentra_api_key=ZENTRA_KEY, **kw)


def with_transport(client: Verifier, handler) -> None:
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


def responder(status: int, body: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body if body is not None else {})
    return handler


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    # ---- helpers, no network -------------------------------------------------

    print("\ndetect() / normalise() / money()")

    assert detect("ABCD123456") == "telebirr"
    assert detect("FT24AB12CD34") == "abyssinia"
    assert detect("ft24ab12cd34") == "abyssinia"
    assert detect("not a reference") is None and detect("") is None
    ok("a reference's shape alone says which provider it belongs to")

    assert normalise(" dhi9w300tb ") == "DHI9W300TB"
    ok("normalise() upper-cases and trims — one spelling gets stored")

    assert mask("ABCD123456") == "AB******56" and mask("AB") == "**"
    ok("mask() shows enough to match a log line, never enough to replay")

    assert money("1,234.56") == Decimal("1234.56")
    assert money("1234.56 Birr") == Decimal("1234.56")
    assert money(None) is None and money("nope") is None and money(True) is None
    ok("money() reads a provider's formatting and refuses what it cannot")

    # ---- which mode am I in --------------------------------------------------

    print("\nHosted by default, self-hosted only when asked")

    v = hosted()
    assert v.configured is True
    assert v.self_hosted is False
    ok("a Zentra API key alone is enough — nothing else to configure")

    assert "Zentra" in v.describe and ZENTRA_KEY not in v.describe
    ok("describe() names the mode for a startup log and never leaks the key")

    half = hosted(self_host_url="http://127.0.0.1:3001")
    assert half.self_hosted is False
    ok("a self-host URL with no key is NOT self-hosted — it would be an open endpoint")

    own = hosted(self_host_url="http://127.0.0.1:3001", self_host_key="own-key")
    assert own.self_hosted is True
    ok("a URL and a key together switch to the deployment's own instance")

    blank = Verifier()
    assert blank.configured is False
    try:
        await blank.verify("ABCD123456")
        raise AssertionError("expected Unavailable")
    except Unavailable as exc:
        assert exc.operator is True
    ok("with nothing configured at all, verifying fails closed as operator=True")

    # ---- request assembly, both modes ---------------------------------------

    print("\nWhere each mode points, and how it authenticates")

    url, headers, payload = hosted()._request_for("telebirr", "ABCD123456", None)
    assert url == f"{ZENTRA_BASE}/v1/verify/telebirr", url
    assert headers == {"Authorization": f"Bearer {ZENTRA_KEY}"}
    assert payload == {"reference": "ABCD123456"}
    ok("hosted: /v1/verify/telebirr with the SAME Zentra key the bot buys with")

    url, headers, payload = hosted()._request_for("abyssinia", "FT24AB12CD34", "56789")
    assert url == f"{ZENTRA_BASE}/v1/verify/abyssinia", url
    assert payload == {"reference": "FT24AB12CD34", "suffix": "56789"}
    ok("hosted: a path per provider, and Abyssinia carries its suffix")

    url, headers, payload = own._request_for("telebirr", "ABCD123456", None)
    assert url == "http://127.0.0.1:3001/verify", url
    assert headers == {"x-api-key": "own-key"}
    assert "Authorization" not in headers
    ok("self-hosted: LocalPaymentVerify's own /verify and x-api-key shape")
    ok("and the Zentra key is never sent to somebody else's server")

    # ---- the wire ------------------------------------------------------------

    print("\nA real round trip")

    seen: dict = {}

    def echo(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=OK_TELEBIRR)

    client = hosted()
    with_transport(client, echo)
    answer = await client.verify("ABCD123456")
    assert answer == {"provider": "telebirr", "data": {"receiptNo": "ABCD123456"}}
    assert seen["url"].endswith("/v1/verify/telebirr")
    assert seen["auth"] == f"Bearer {ZENTRA_KEY}"
    ok("a successful check returns just the provider's own data block")

    # ---- bad shapes never reach the network ----------------------------------

    print("\nBad shapes are refused before any request is made")

    never = hosted()

    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not have made a network call")

    with_transport(never, explode)

    for bad, why in [("not a reference", "unrecognisable"), ("FT24AB12CD34", "no suffix")]:
        try:
            await never.verify(bad)
            raise AssertionError(f"expected BadRequest for {why}")
        except BadRequest:
            pass
    ok("an unrecognisable reference, and Abyssinia with no suffix, never hit the network")

    # ---- status mapping ------------------------------------------------------

    print("\nEvery status maps to its own honest exception")

    cases = [
        (401, Unavailable, True),   # the Zentra key is wrong or revoked
        (403, Unavailable, True),   # the key exists but may not verify
        (429, Unavailable, True),   # quota — NOT "your payment is fake"
        (503, Unavailable, True),
        (500, Unavailable, True),
        (400, BadRequest, False),
        (404, NotFound, False),
        (422, NotFound, False),
        (502, NotFound, False),
    ]
    for status, expected, operator in cases:
        c = hosted()
        with_transport(c, responder(status, {"error": "detail"}))
        try:
            await c.verify("ABCD123456")
            raise AssertionError(f"expected {expected.__name__} for HTTP {status}")
        except expected as exc:
            assert exc.operator is operator, f"HTTP {status}: operator={exc.operator}"
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"HTTP {status} raised {type(exc).__name__}, wanted {expected.__name__}"
            ) from exc
    ok("401/403/429/503/500 are operator problems; 400/404/422/502 are not")

    # A quota is the one people get wrong: it must NOT read as "no payment",
    # because the honest outcome is "a human will look at this".
    c = hosted()
    with_transport(c, responder(429, {"error": "daily quota reached"}))
    try:
        await c.verify("ABCD123456")
        raise AssertionError("expected Unavailable")
    except Unavailable as exc:
        assert "reviewed" in str(exc), str(exc)
    ok("a quota refusal tells the customer their receipt is saved for review")

    # ---- 200s that are not really answers ------------------------------------

    print("\nA 200 that is not an answer is still refused")

    for body, expected in [
        ({"success": False}, NotFound),
        ({"success": True, "provider": "telebirr"}, Unavailable),        # no data
        ({"success": True, "provider": "abyssinia", "data": {"a": 1}}, Unavailable),
    ]:
        c = hosted()
        with_transport(c, responder(200, body))
        try:
            await c.verify("ABCD123456")
            raise AssertionError(f"expected {expected.__name__} for {body}")
        except expected:
            pass
    ok("success=false, a missing data block, and a provider mismatch are all refused")

    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>captive portal</html>")

    c = hosted()
    with_transport(c, not_json)
    try:
        await c.verify("ABCD123456")
        raise AssertionError("expected Unavailable")
    except Unavailable:
        pass
    ok("a non-JSON body is refused, never parsed as an answer")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
