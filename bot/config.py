"""Everything read from the environment, in one place, validated once.

Two kinds of setting live in this project, and they are kept apart
deliberately — the same split ZentraShopBot itself uses, and for the same
reason.

SECRETS AND INFRASTRUCTURE live here, in .env, read once at startup: your
bot token, your Zentra API key, the database URL, the payment provider
credentials. Nothing that could drain a wallet if a browser session leaked
is ever reachable from the admin dashboard.

BUSINESS SETTINGS — your markup, which rails are on, minimum top-up — live
in the `settings` table and are read at runtime by bot/settings.py. Changing
those is what the admin dashboard is FOR. This file does not touch them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _str(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _flag(name: str, default: bool = False) -> bool:
    """A yes/no setting. Anything but an explicit yes reads as no — failing
    to "off" is the right default for anything touching money."""
    raw = _str(name, "yes" if default else "no").lower()
    return raw in {"1", "true", "yes", "on"}


def _required(name: str, hint: str) -> str:
    value = _str(name)
    if not value:
        raise RuntimeError(f"{name} is not set. {hint}")
    return value


@dataclass(frozen=True)
class Config:
    # ---- your bot -----------------------------------------------------
    bot_token: str
    admin_ids: tuple[int, ...]

    # ---- Zentra -----------------------------------------------------
    zentra_api_key: str
    zentra_api_base_url: str

    # ---- your database -----------------------------------------------------
    database_url: str

    # ---- payment rails: USDT (BEP-20) -----------------------------------
    bsc_payment_address: str
    bsc_wss_url: str
    bsc_http_url: str
    usdt_contract_address: str

    # ---- payment rails: Binance Pay -----------------------------------
    #
    # NOT A MERCHANT INTEGRATION. Binance Pay's merchant API needs a
    # business account and an approval process — most resellers starting
    # out have neither. This rail instead reads YOUR OWN personal Binance
    # account's Pay history through a signed, read-only API key: a customer
    # sends an ordinary transfer to your Binance UID, and the bot confirms
    # it by asking Binance what your own account received. Exactly how
    # ZentraShopBot itself does this.
    binance_uid: str
    binance_api_key: str
    binance_api_secret: str
    binance_api_base: str

    # ---- payment rails: Telebirr / Bank of Abyssinia (manual + verified) ---
    # Verified means LocalPaymentVerify confirms the receipt automatically;
    # see docs/GUIDE.md §12. Without it these rails still work — an admin
    # approves each one by hand in the dashboard, which is where every
    # reseller should start.
    #
    # The receiving account is infrastructure, the same reasoning as
    # BSC_PAYMENT_ADDRESS and BINANCE_UID: it is who gets paid, not a
    # business rule, and verification compares a receipt's own receiver
    # against exactly this value — a fork of this repository must supply
    # its own, never inherit somebody else's from a shared default.
    telebirr_number: str
    telebirr_name: str
    abyssinia_account: str
    abyssinia_name: str
    local_verify_url: str
    local_verify_api_key: str

    @property
    def bsc_rpc_enabled(self) -> bool:
        """True when the USDT watcher has everything it needs to run.

        BSC_WSS_URL is NOT required here, deliberately — the Phase 2 watcher
        polls a single HTTP endpoint (see bot/chain/rpc.py's own docstring
        for why), and requiring a WebSocket URL nobody's code reads would be
        exactly the kind of unnecessary setup step that keeps a reseller
        from finishing configuration. The field exists in case a future
        phase moves to an event-driven listener; it costs nothing to leave
        unused today.
        """
        return bool(self.bsc_http_url and self.bsc_payment_address)

    @property
    def binance_pay_enabled(self) -> bool:
        return bool(self.binance_uid and self.binance_api_key and self.binance_api_secret)

    @property
    def local_verify_enabled(self) -> bool:
        return bool(self.local_verify_url and self.local_verify_api_key)

    @property
    def telebirr_configured(self) -> bool:
        """Whether the deployment can offer Telebirr at all — automatic or
        manual. Verification needs an account to compare a receipt against
        (see localpay.py); without one, the rail is not offered rather than
        offered against nothing."""
        return bool(self.telebirr_number)

    @property
    def abyssinia_configured(self) -> bool:
        return bool(self.abyssinia_account)

    @classmethod
    def load(cls) -> "Config":
        admin_raw = _str("ADMIN_IDS")
        admin_ids = tuple(
            int(part) for part in admin_raw.split(",") if part.strip().isdigit()
        )

        return cls(
            bot_token=_required("BOT_TOKEN", "Get one from @BotFather."),
            admin_ids=admin_ids,
            zentra_api_key=_required(
                "ZENTRA_API_KEY",
                "Get one from @ZentraShopBot — Menu → API Link → Create API key.",
            ),
            zentra_api_base_url=_str("ZENTRA_API_BASE_URL", "https://api.zentradigital.shop"),
            database_url=_required(
                "DATABASE_URL",
                "A PostgreSQL connection string — see README.md for Supabase setup.",
            ),
            bsc_payment_address=_str("BSC_PAYMENT_ADDRESS"),
            bsc_wss_url=_str("BSC_WSS_URL"),
            bsc_http_url=_str("BSC_HTTP_URL"),
            usdt_contract_address=_str(
                "USDT_CONTRACT_ADDRESS", "0x55d398326f99059fF775485246999027B3197955"),
            binance_uid=_str("BINANCE_UID"),
            binance_api_key=_str("BINANCE_API_KEY"),
            binance_api_secret=_str("BINANCE_API_SECRET"),
            binance_api_base=_str("BINANCE_API_BASE", "https://api.binance.com"),
            telebirr_number=_str("TELEBIRR_NUMBER"),
            telebirr_name=_str("TELEBIRR_NAME"),
            abyssinia_account=_str("ABYSSINIA_ACCOUNT"),
            abyssinia_name=_str("ABYSSINIA_NAME"),
            local_verify_url=_str("LOCAL_VERIFY_URL"),
            local_verify_api_key=_str("LOCAL_VERIFY_API_KEY"),
        )

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in self.admin_ids
