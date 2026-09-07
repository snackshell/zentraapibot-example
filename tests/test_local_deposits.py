"""Telebirr / Bank of Abyssinia deposits: opening a request, attaching a
submitted reference, and the exactly-once credit guard they share with
every other rail through db.py's own credit_deposit().

Run:  TEST_DATABASE_URL=postgresql://… python -m tests.test_local_deposits
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from tests.dbfixture import fresh_db


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    db = await fresh_db()

    async def customer(telegram_id: int) -> dict:
        return await db.ensure_user(telegram_id, f"user{telegram_id}")

    # ---- opening a request -----------------------------------------------------

    print("\nOpening a Telebirr request")

    alice = await customer(8001)
    dep = await db.open_local_deposit(
        user_id=alice["id"], method="telebirr", amount_etb=Decimal("500"),
        window_minutes=60,
    )
    assert dep["status"] == "awaiting"
    assert dep["amount_expected"] == Decimal("500")
    assert dep["amount_credited"] == 0
    assert dep["reference"] is None
    ok("a request opens with the ETB figure requested and no reference yet")

    bob = await customer(8002)
    dep_same_amount = await db.open_local_deposit(
        user_id=bob["id"], method="telebirr", amount_etb=Decimal("500"), window_minutes=60,
    )
    assert dep_same_amount["id"] != dep["id"]
    ok("two customers requesting the identical ETB figure do not collide — "
       "this rail has no fingerprint to collide over")

    # ---- submitting a reference -----------------------------------------------------

    print("\nSubmitting a reference")

    updated = await db.submit_local_reference(dep["id"], reference="ABCD123456")
    assert updated is not None
    assert updated["reference"] == "ABCD123456"
    assert updated["status"] == "awaiting"
    ok("a submitted reference attaches to the request without resolving it")

    resubmitted = await db.submit_local_reference(dep["id"], reference="ZZZZ999999")
    assert resubmitted["reference"] == "ZZZZ999999"
    ok("resubmitting overwrites the reference — a customer correcting a typo is not stuck")

    # ---- crediting: the same exactly-once guard every rail shares -------------------

    print("\nCrediting")

    credited = await db.credit_deposit(dep["id"], tx_hash="ZZZZ999999", amount_credited=Decimal("3.05"))
    assert credited is not None
    assert credited["status"] == "credited"
    assert credited["amount_credited"] == Decimal("3.05")
    ok("credit_deposit()'s amount override sets the REAL figure a receipt turned out to be worth")

    balance = await db.balance(alice["id"])
    assert balance == Decimal("3.05")
    ok("the wallet actually holds what was credited")

    again = await db.credit_deposit(dep["id"], tx_hash="ZZZZ999999", amount_credited=Decimal("3.05"))
    assert again is None
    balance_after = await db.balance(alice["id"])
    assert balance_after == balance
    ok("crediting the same deposit twice is a no-op — the balance does not move again")

    # ---- the SAME reference can never credit a second deposit, either --------------

    print("\nOne reference, at most one credit — across DIFFERENT deposits")

    carol = await customer(8003)
    dep2 = await db.open_local_deposit(
        user_id=carol["id"], method="telebirr", amount_etb=Decimal("500"), window_minutes=60,
    )
    collision = await db.credit_deposit(dep2["id"], tx_hash="ZZZZ999999", amount_credited=Decimal("3.05"))
    assert collision is None
    assert await db.balance(carol["id"]) == 0
    ok("a reference already used to credit one deposit refuses to credit a different one")

    # ---- rejection -----------------------------------------------------------------

    print("\nRejecting")

    dave = await customer(8004)
    dep3 = await db.open_local_deposit(
        user_id=dave["id"], method="abyssinia", amount_etb=Decimal("800"), window_minutes=60,
    )
    await db.submit_local_reference(dep3["id"], reference="FT24AB12CD34", suffix="56789")
    rejected = await db.reject_local_deposit(dep3["id"], note="Receipt did not match our account")
    assert rejected is not None
    assert rejected["status"] == "rejected"
    ok("an admin's rejection is recorded with a note, and the deposit resolves")

    again_credit = await db.credit_deposit(dep3["id"], tx_hash="FT24AB12CD34", amount_credited=Decimal("5"))
    assert again_credit is None
    assert await db.balance(dave["id"]) == 0
    ok("a rejected request cannot later be credited — resolution is final either way")

    second_reject = await db.reject_local_deposit(dep3["id"], note="again")
    assert second_reject is None
    ok("rejecting an already-resolved request is a no-op, not a second resolution")

    # ---- submitting a reference after resolution is refused -------------------------

    print("\nA resolved request stops accepting references")

    late_submit = await db.submit_local_reference(dep3["id"], reference="FTNEWNEWNEW")
    assert late_submit is None
    ok("a reference submitted after rejection does not reopen the request")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
