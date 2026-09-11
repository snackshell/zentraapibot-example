"""localpay.check() — the receipt rules, with no network and no database.

Every case here is a way money could reach the wrong person if the rule it
tests were relaxed. Run:  python -m tests.test_localpay
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bot.localpay import Refused, check, tail_matches


@dataclass
class FakeConfig:
    telebirr_number: str = "0912345678"
    telebirr_name: str = "Zentra Reseller"
    abyssinia_account: str = "1000123456789"
    abyssinia_name: str = "Zentra Reseller"


def telebirr_answer(**overrides) -> dict:
    data = {
        "receiptNo": "ABCD123456",
        "transactionStatus": "Completed",
        "totalPaidAmount": "202.00",
        "settledAmount": "200.00",
        "creditedPartyName": "Zentra Reseller",
        "creditedPartyAccountNo": "0912345678",
        "paymentDate": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
    data.update(overrides)
    return {"provider": "telebirr", "data": data}


def deposit(amount_expected="200", created_at=None) -> dict:
    return {
        "amount_expected": amount_expected,
        "created_at": created_at or datetime.now(timezone.utc),
    }


def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    def refused(fn, *, support=None, operator=None) -> Refused:
        try:
            fn()
        except Refused as exc:
            if support is not None:
                assert exc.support is support, f"support={exc.support}, wanted {support}"
            if operator is not None:
                assert exc.operator is operator, f"operator={exc.operator}, wanted {operator}"
            return exc
        raise AssertionError("expected Refused, nothing was raised")

    cfg = FakeConfig()

    # ---- the happy path ------------------------------------------------------

    print("\nA genuine, matching receipt")

    receipt = check("ABCD123456", telebirr_answer(), deposit(), cfg)
    assert receipt.credit == Decimal("200.00")
    ok("the SETTLED amount is credited, not the total the payer was charged")

    # ---- the reference must match what was checked ----------------------------

    print("\nThe reference")

    refused(lambda: check("ZZZZ999999", telebirr_answer(), deposit(), cfg), support=True)
    ok("a receipt answering for a different reference than we asked about is refused")

    # ---- completion ------------------------------------------------------------

    print("\nCompletion")

    for status in ("Pending", "Processing", "Failed", "Reversed", ""):
        refused(lambda s=status: check(
            "ABCD123456", telebirr_answer(transactionStatus=s), deposit(), cfg))
    ok("anything other than a recognised 'completed' word is refused")

    # ---- the receiver must be US ------------------------------------------------

    print("\nWho was paid")

    refused(lambda: check(
        "ABCD123456",
        telebirr_answer(creditedPartyAccountNo="0999999999", creditedPartyName="Someone Else"),
        deposit(), cfg,
    ))
    ok("a receipt paid to a different account is refused, however genuine it is")

    refused(lambda: check(
        "ABCD123456", telebirr_answer(creditedPartyAccountNo="", creditedPartyName=""),
        deposit(), cfg,
    ), support=True)
    ok("a receipt naming no receiver at all is refused, not assumed to be us")

    no_account_configured = FakeConfig(telebirr_number="")
    refused(lambda: check(
        "ABCD123456", telebirr_answer(), deposit(), no_account_configured,
    ), operator=True)
    ok("with no receiving account configured, every receipt is refused as a misconfiguration")

    # A country-code prefix on either side is still the same account.
    receipt = check(
        "ABCD123456", telebirr_answer(creditedPartyAccountNo="251912345678"), deposit(), cfg)
    assert receipt.receiver_account == "251912345678"
    ok("a receiver account written with a country code still matches the configured number")

    # ---- masked accounts: the normal case on Telebirr ----------------------------

    print("\nA masked receiver account, which is what Telebirr actually returns")

    masked_cfg = FakeConfig(telebirr_number="0996720207")

    # Telebirr redacts the middle: "2519****0207". Before this was handled,
    # same_account() saw the asterisks, refused to guess, and EVERY genuine
    # receipt on the rail was rejected. It has to be accepted — but only
    # with the account name corroborating the four visible digits.
    receipt = check("ABCD123456",
                    telebirr_answer(creditedPartyAccountNo="2519****0207"),
                    deposit(), masked_cfg)
    assert receipt.credit == Decimal("200.00")
    ok("a masked account whose visible digits AND name agree is credited")

    refused(lambda: check(
        "ABCD123456",
        telebirr_answer(creditedPartyAccountNo="2519****0207",
                        creditedPartyName="Somebody Else"),
        deposit(), masked_cfg))
    ok("the same masked account with a different name is refused — four digits "
       "collide once in ten thousand, so the name is doing real work")

    refused(lambda: check(
        "ABCD123456", telebirr_answer(creditedPartyAccountNo="2519****9999"),
        deposit(), masked_cfg))
    ok("a masked account whose visible digits differ is refused")

    # With no configured name there is nothing to corroborate against, so
    # accepting four digits alone would be accepting a coin flip. Refused as
    # the operator's problem, not the customer's.
    refused(lambda: check(
        "ABCD123456", telebirr_answer(creditedPartyAccountNo="2519****0207"),
        deposit(), FakeConfig(telebirr_number="0996720207", telebirr_name="")),
        operator=True)
    ok("a masked account with NO configured name is refused as a misconfiguration")

    # The collision this is all defending against, stated as a fact:
    assert tail_matches("0911110207", "2519****0207") is True
    ok("(and a stranger's account sharing four digits DOES pass the tail test "
       "alone — which is exactly why it never stands alone)")

    # ---- the amount must be real ------------------------------------------------

    print("\nThe amount")

    refused(lambda: check(
        "ABCD123456", telebirr_answer(settledAmount="", totalPaidAmount=""), deposit(), cfg,
    ), support=True)
    ok("a receipt with no readable amount at all is refused for a person to look at")

    refused(lambda: check(
        "ABCD123456", telebirr_answer(settledAmount="0", totalPaidAmount="0"), deposit(), cfg,
    ), support=True)
    ok("a zero or negative amount is refused")

    refused(lambda: check(
        "ABCD123456", telebirr_answer(settledAmount="500000", totalPaidAmount="500000"),
        deposit(amount_expected="200"), cfg,
    ), support=True)
    ok("a payment wildly larger than what was requested is refused for a person to check")

    # An overpayment inside the ceiling is still credited in full — there is
    # no tolerance band, but there is no punishment for a generous customer.
    receipt = check(
        "ABCD123456", telebirr_answer(settledAmount="450", totalPaidAmount="452"),
        deposit(amount_expected="200"), cfg,
    )
    assert receipt.credit == Decimal("450")
    ok("an overpayment inside the ceiling is credited for the real amount received")

    # ---- freshness ---------------------------------------------------------------

    print("\nFreshness")

    stale_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    refused(lambda: check(
        "ABCD123456", telebirr_answer(paymentDate=stale_date),
        deposit(created_at=datetime.now(timezone.utc)), cfg,
    ), support=True)
    ok("a receipt dated long before the request even existed is refused as too old")

    future_date = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    refused(lambda: check(
        "ABCD123456", telebirr_answer(paymentDate=future_date), deposit(), cfg,
    ), support=True)
    ok("a receipt dated in the future is refused rather than trusted")

    # An unreadable date does not itself refuse the receipt — see the
    # docstring on Receipt.paid_at_utc for why.
    receipt = check(
        "ABCD123456", telebirr_answer(paymentDate="not a date"), deposit(), cfg)
    assert receipt.credit == Decimal("200.00")
    ok("an unparseable payment date is not treated as suspicious on its own")

    # ---- Bank of Abyssinia's own shape ---------------------------------------------

    print("\nBank of Abyssinia's own field names")

    aby_answer = {
        "provider": "abyssinia",
        "data": {
            "reference": "FT24AB12CD34",
            "success": True,
            "amount": "300",
            "receiver": "Zentra Reseller",
            "receiverAccount": "1000123456789",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
    receipt = check("FT24AB12CD34", aby_answer, deposit(amount_expected="300"), cfg)
    assert receipt.credit == Decimal("300")
    ok("Abyssinia's success=true with no separate status field is read as completed")

    bad_aby = dict(aby_answer)
    bad_aby["data"] = dict(aby_answer["data"])
    bad_aby["data"]["success"] = False
    refused(lambda: check("FT24AB12CD34", bad_aby, deposit(), cfg))
    ok("success=false on Abyssinia's own shape is refused the same as any incomplete payment")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    main()
