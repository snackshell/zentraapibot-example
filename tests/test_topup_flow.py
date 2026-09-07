"""The /wallet -> top up -> deposit screen flow, through the real handlers.

Not the watcher itself (see test_watcher.py) — this is what a customer
actually sees and types, with a fake Telegram message/callback standing in
for aiogram's own objects.

Run:  TEST_DATABASE_URL=postgresql://… python -m tests.test_topup_flow
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal

os.environ.setdefault("BOT_TOKEN", "123456789:AAEtestTokenForOfflineTestsOnly12345678")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("ZENTRA_API_KEY", "zen_live_testtesttesttesttesttesttesttest01")

from tests.dbfixture import fresh_db, test_database_url  # noqa: E402

os.environ["DATABASE_URL"] = test_database_url()

from bot import bot as botmod  # noqa: E402
from bot.bot import (  # noqa: E402
    TopUp, topup_abyssinia_start, topup_binance_amount, topup_binance_start,
    topup_local_reference, topup_telebirr_amount, topup_telebirr_start,
    topup_usdt_amount, topup_usdt_start, wallet,
)


class FakeState:
    """Stands in for aiogram's FSMContext — just enough to track one state
    and the small bit of data the local-pay flow attaches to it (which
    deposit and which provider a reference reply belongs to)."""

    def __init__(self):
        self._state = None
        self._data: dict = {}

    async def set_state(self, state) -> None:
        self._state = state

    async def clear(self) -> None:
        self._state = None
        self._data = {}

    async def update_data(self, **kwargs) -> None:
        self._data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self._data)

    @property
    def state(self):
        return self._state


def _button_labels(markup) -> list[str]:
    if markup is None:
        return []
    return [b.text for row in markup.inline_keyboard for b in row]


class FakeMessage:
    def __init__(self, chat_id: int, text: str = ""):
        self.chat = type("C", (), {"id": chat_id})()
        self.text = text
        self.from_user = type("U", (), {"id": chat_id, "username": f"user{chat_id}"})()
        self.sent: list[str] = []
        self.edited: list[str] = []
        # Buttons live in reply_markup, not in the text — a screen showing
        # "Top up with USDT" is a button, never a string edit_text() was
        # called with, so checking .edited alone cannot see it.
        self.edited_markups: list = []

    async def answer(self, text, **kwargs):
        self.sent.append(text)

    async def edit_text(self, text, **kwargs):
        self.edited.append(text)
        self.edited_markups.append(kwargs.get("reply_markup"))


class FakeCall:
    def __init__(self, telegram_id: int):
        self.data = ""
        self.from_user = type("U", (), {"id": telegram_id, "username": f"user{telegram_id}"})()
        self.message = FakeMessage(telegram_id)
        self.alerts: list[str] = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        if text:
            self.alerts.append(text)


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    db = await fresh_db()
    from bot.settings import Settings
    botmod.db = db
    botmod.live = Settings(db)
    await botmod.live.refresh()

    # ---- the rail is off by default -----------------------------------------------------

    print("\nOff by default")

    call = FakeCall(5001)
    await wallet(call)
    labels = _button_labels(call.message.edited_markups[-1])
    assert not any("USDT" in label for label in labels), labels
    ok("with usdt_enabled at its shipped default (no), the wallet screen offers no USDT button")

    call2 = FakeCall(5001)
    await topup_usdt_start(call2, FakeState())
    assert call2.alerts and "not turned on" in call2.alerts[0]
    ok("and tapping the callback directly is refused with a clear reason, not a crash")

    # ---- turn it on (deployment side is faked; dashboard side is real) -----------------------------------------------------

    print("\nWith the rail live")

    await db.write_setting("usdt_enabled", "yes", updated_by=None)
    await botmod.live.refresh()
    # The deployment side (BSC_HTTP_URL, BSC_PAYMENT_ADDRESS in .env) is not
    # configurable from a test env var already loaded at import time, so the
    # module-level cfg is patched directly — this test is about the
    # SCREENS. bsc_rpc_enabled needs only these two; BSC_WSS_URL is reserved
    # for a future upgrade and is not read anywhere today.
    botmod.cfg = botmod.cfg.__class__(
        **{**botmod.cfg.__dict__, "bsc_http_url": "https://fake",
           "bsc_payment_address": "0x1111111111111111111111111111111111111111"})

    call3 = FakeCall(5002)
    await wallet(call3)
    labels = _button_labels(call3.message.edited_markups[-1])
    assert any("USDT" in label for label in labels), labels
    ok("with the setting on and the deployment configured, the button appears")

    state = FakeState()
    call4 = FakeCall(5002)
    await topup_usdt_start(call4, state)
    assert state.state == TopUp.usdt_amount
    ok("tapping it puts the conversation into the amount-entry state")

    # ---- entering an amount -----------------------------------------------------

    print("\nEntering an amount")

    bad = FakeMessage(5003, text="not a number")
    await topup_usdt_amount(bad, FakeState())
    assert "doesn't look like a number" in bad.sent[-1]
    ok("junk input gets a plain correction, not a crash")

    too_small = FakeMessage(5004, text="0.01")
    await topup_usdt_amount(too_small, FakeState())
    assert "smallest top-up" in too_small.sent[-1]
    ok("an amount below the minimum is refused with the actual minimum stated")

    good_state = FakeState()
    good = FakeMessage(5005, text="5")
    await topup_usdt_amount(good, good_state)
    assert good_state.state is None, "the state was not cleared after a successful allocation"
    ok("a valid amount clears the conversation state")

    reply = good.sent[-1]
    assert "Send exactly this much" in reply
    assert "0x1111111111111111111111111111111111111111" in reply
    ok("the reply shows the payment address and says to send the exact figure")

    deposits = await db.user_deposits((await db.user_by_telegram_id(5005))["id"])
    assert len(deposits) == 1
    exact = Decimal(deposits[0]["amount_expected"])
    assert Decimal("5.00") <= exact < Decimal("5.01")
    assert str(exact) in reply, "the screen's figure does not match the allocated deposit"
    ok("and the figure shown is the exact figure that was actually reserved")

    # A comma in the input (people paste "1,000") must not break parsing.
    comma_state = FakeState()
    comma = FakeMessage(5006, text="1,000")
    await topup_usdt_amount(comma, comma_state)
    assert "Send exactly this much" in comma.sent[-1]
    ok("a comma-formatted amount is accepted")

    # ---- Binance Pay: off, then on -----------------------------------------------------

    print("\nBinance Pay, off by default")

    call5 = FakeCall(5007)
    await wallet(call5)
    labels = _button_labels(call5.message.edited_markups[-1])
    assert not any("Binance Pay" in label for label in labels), labels
    ok("with binance_pay_enabled at its shipped default (no), no Binance Pay button appears")

    call6 = FakeCall(5007)
    await topup_binance_start(call6, FakeState())
    assert call6.alerts and "not turned on" in call6.alerts[0]
    ok("and tapping the callback directly is refused with a clear reason")

    print("\nBinance Pay, live")

    await db.write_setting("binance_pay_enabled", "yes", updated_by=None)
    await botmod.live.refresh()
    botmod.cfg = botmod.cfg.__class__(
        **{**botmod.cfg.__dict__, "binance_uid": "732609210",
           "binance_api_key": "key", "binance_api_secret": "secret"})

    call7 = FakeCall(5008)
    await wallet(call7)
    labels = _button_labels(call7.message.edited_markups[-1])
    assert any("Binance Pay" in label for label in labels), labels
    # And the USDT button, from earlier in this test, is STILL there —
    # turning one rail on must not turn the other off.
    assert any("USDT" in label for label in labels), labels
    ok("both rails' buttons appear together, independently of one another")

    binance_state = FakeState()
    binance_msg = FakeMessage(5008, text="9")
    await topup_binance_start(FakeCall(5008), binance_state)
    await topup_binance_amount(binance_msg, binance_state)
    reply = binance_msg.sent[-1]
    assert "Binance Pay" in reply and "732609210" in reply
    ok("the Binance Pay deposit screen shows the operator's own Binance ID")

    binance_deposits = [
        d for d in await db.user_deposits((await db.user_by_telegram_id(5008))["id"])
        if d["method"] == "binancepay"
    ]
    assert len(binance_deposits) == 1
    ok("and the deposit is recorded under method='binancepay'")

    # ---- Telebirr: off, then manual, then automatic -----------------------------

    print("\nTelebirr, off by default")

    call8 = FakeCall(5009)
    await wallet(call8)
    labels = _button_labels(call8.message.edited_markups[-1])
    assert not any("Telebirr" in label for label in labels), labels
    ok("with no receiving number configured, no Telebirr button appears")

    call9 = FakeCall(5009)
    await topup_telebirr_start(call9, FakeState())
    assert call9.alerts and "not turned on" in call9.alerts[0]
    ok("and tapping the callback directly is refused with a clear reason")

    call9b = FakeCall(5009)
    await topup_abyssinia_start(call9b, FakeState())
    assert call9b.alerts and "not turned on" in call9b.alerts[0]
    ok("Abyssinia refuses independently, with no account configured for it either")

    print("\nTelebirr, live — manual review (no verifier configured)")

    await db.write_setting("telebirr_enabled", "yes", updated_by=None)
    await botmod.live.refresh()
    botmod.cfg = botmod.cfg.__class__(
        **{**botmod.cfg.__dict__, "telebirr_number": "0912345678",
           "telebirr_name": "Zentra Reseller"})

    call10 = FakeCall(5009)
    await wallet(call10)
    labels = _button_labels(call10.message.edited_markups[-1])
    assert any("Telebirr" in label for label in labels), labels
    ok("with the setting on and a number configured, the button appears")

    tb_state = FakeState()
    await topup_telebirr_start(FakeCall(5009), tb_state)
    assert tb_state.state == TopUp.telebirr_amount
    amount_msg = FakeMessage(5009, text="500")
    await topup_telebirr_amount(amount_msg, tb_state)
    assert tb_state.state == TopUp.local_reference
    reply = amount_msg.sent[-1]
    assert "0912345678" in reply and "500 ETB" in reply
    ok("the amount screen shows the receiving number and the birr figure requested")

    ref_msg = FakeMessage(5009, text="ABCD123456")
    await topup_local_reference(ref_msg, tb_state)
    assert tb_state.state is None
    assert "Received" in ref_msg.sent[-1]
    ok("without a verifier configured, a submitted reference is acknowledged and left for review")

    tb_user = await db.user_by_telegram_id(5009)
    pending = [d for d in await db.user_deposits(tb_user["id"]) if d["method"] == "telebirr"]
    assert len(pending) == 1
    assert pending[0]["status"] == "awaiting"
    assert pending[0]["reference"] == "ABCD123456"
    ok("and the deposit itself is exactly what the dashboard's Local Payments page shows: "
       "awaiting, with the reference attached, nothing credited")

    print("\nTelebirr, live — automatic verification")

    class FakeVerifier:
        """Stands in for localverify.Verifier — no network, one canned answer."""

        def __init__(self, answer=None, error=None):
            self.answer = answer
            self.error = error
            self.calls: list[tuple] = []

        @property
        def configured(self) -> bool:
            return True

        async def verify(self, reference, suffix=None):
            self.calls.append((reference, suffix))
            if self.error is not None:
                raise self.error
            return self.answer

    await db.write_setting("telebirr_verify_enabled", "yes", updated_by=None)
    await botmod.live.refresh()

    matching_answer = {
        "provider": "telebirr",
        "data": {
            "receiptNo": "EFGH567890",
            "transactionStatus": "Completed",
            "totalPaidAmount": "505.00",
            "settledAmount": "500.00",
            "creditedPartyName": "Zentra Reseller",
            "creditedPartyAccountNo": "0912345678",
            "paymentDate": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
    botmod.local_verifier = FakeVerifier(answer=matching_answer)

    auto_state = FakeState()
    await topup_telebirr_start(FakeCall(5009), auto_state)
    await topup_telebirr_amount(FakeMessage(5009, text="500"), auto_state)
    auto_ref_msg = FakeMessage(5009, text="EFGH567890")
    await topup_local_reference(auto_ref_msg, auto_state)
    assert "Top-up received" in auto_ref_msg.sent[-1]
    ok("a genuine, matching receipt credits the wallet immediately, with no admin involved")

    balance_5009 = await db.balance(tb_user["id"])
    # 500 ETB / 160 = 3.125 USDT, rounded half-up to the cent by money.cents().
    assert balance_5009 == Decimal("3.13"), balance_5009
    ok("the credited figure is the receipt's own ETB amount, converted at usdt_to_etb")

    print("\nTelebirr, automatic verification — a receipt paid to someone else")

    botmod.local_verifier = FakeVerifier(answer={
        "provider": "telebirr",
        "data": {**matching_answer["data"], "receiptNo": "WRONG00001",
                 "creditedPartyAccountNo": "0999999999", "creditedPartyName": "Someone Else"},
    })
    refused_state = FakeState()
    await topup_telebirr_start(FakeCall(5009), refused_state)
    await topup_telebirr_amount(FakeMessage(5009, text="500"), refused_state)
    refused_msg = FakeMessage(5009, text="WRONG00001")
    await topup_local_reference(refused_msg, refused_state)
    assert "Received" in refused_msg.sent[-1]
    assert "different account" in refused_msg.sent[-1]
    ok("a receipt paid to a different account is NOT credited — it waits for a person, "
       "and the customer is told why in the same message")

    unchanged_balance = await db.balance(tb_user["id"])
    assert unchanged_balance == balance_5009, "a refused receipt must not move the balance"
    ok("and the wallet balance has not moved")

    await db.close()
    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
