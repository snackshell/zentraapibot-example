"""Config.load() and the properties that gate a payment rail.

No database, no network. Run:  python -m tests.test_config
"""

from __future__ import annotations

import os

REQUIRED = {
    "BOT_TOKEN": "123456789:AAEtestTokenForOfflineTestsOnly12345678",
    "ADMIN_IDS": "1,2",
    "ZENTRA_API_KEY": "zen_live_" + "a" * 40,
    "DATABASE_URL": "postgresql://user:pass@localhost/db",
}


def load_with(**overrides):
    saved = {k: os.environ.get(k) for k in list(REQUIRED) + list(overrides)}
    try:
        for key, value in REQUIRED.items():
            os.environ[key] = value
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        # Reload fresh each time — Config.load() reads os.environ directly,
        # so this is not a caching concern, just import hygiene.
        from bot.config import Config
        return Config.load()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    cfg = load_with()
    assert cfg.admin_ids == (1, 2)
    ok("comma-separated admin ids parse into a tuple of ints")

    cfg = load_with(ADMIN_IDS="1, notanumber, 3")
    assert cfg.admin_ids == (1, 3)
    ok("a non-numeric entry in ADMIN_IDS is dropped, not fatal")

    # ---- the USDT rail's enable condition -----------------------------------------------------

    print("\nWhen the USDT rail is considered configured")

    cfg = load_with()
    assert cfg.bsc_rpc_enabled is False
    ok("with nothing set, the rail is off")

    cfg = load_with(BSC_HTTP_URL="https://rpc.example", BSC_PAYMENT_ADDRESS="0xabc")
    assert cfg.bsc_rpc_enabled is True
    ok("BSC_HTTP_URL and BSC_PAYMENT_ADDRESS alone are enough — no WebSocket required")

    cfg = load_with(BSC_HTTP_URL="https://rpc.example")
    assert cfg.bsc_rpc_enabled is False
    ok("an HTTP endpoint with no payment address is still off")

    cfg = load_with(BSC_PAYMENT_ADDRESS="0xabc")
    assert cfg.bsc_rpc_enabled is False
    ok("a payment address with no HTTP endpoint is still off")

    # BSC_WSS_URL being present or absent must never change the answer —
    # this is the exact regression a "helpful" tidy-up could reintroduce.
    with_wss = load_with(BSC_HTTP_URL="https://rpc.example",
                         BSC_PAYMENT_ADDRESS="0xabc", BSC_WSS_URL="wss://rpc.example")
    without_wss = load_with(BSC_HTTP_URL="https://rpc.example",
                            BSC_PAYMENT_ADDRESS="0xabc", BSC_WSS_URL=None)
    assert with_wss.bsc_rpc_enabled == without_wss.bsc_rpc_enabled is True
    ok("BSC_WSS_URL makes no difference either way — it is read but not required")

    # ---- the Binance Pay rail's enable condition -----------------------------------------------------

    print("\nWhen the Binance Pay rail is considered configured")

    cfg = load_with()
    assert cfg.binance_pay_enabled is False
    ok("with nothing set, the rail is off")

    cfg = load_with(BINANCE_UID="732609210", BINANCE_API_KEY="key",
                    BINANCE_API_SECRET="secret")
    assert cfg.binance_pay_enabled is True
    ok("a UID, key and secret together turn it on — no merchant id anywhere")

    for missing in ("BINANCE_UID", "BINANCE_API_KEY", "BINANCE_API_SECRET"):
        overrides = {"BINANCE_UID": "732609210", "BINANCE_API_KEY": "key",
                    "BINANCE_API_SECRET": "secret", missing: None}
        cfg = load_with(**overrides)
        assert cfg.binance_pay_enabled is False, missing
    ok("any one of the three missing leaves the rail off")

    cfg = load_with()
    assert cfg.binance_api_base == "https://api.binance.com"
    ok("the API base defaults to Binance's own production host")

    # ---- Telebirr / Bank of Abyssinia's enable condition -----------------------------

    print("\nWhen Telebirr / Abyssinia are considered configured")

    cfg = load_with()
    assert cfg.telebirr_configured is False
    assert cfg.abyssinia_configured is False
    ok("with no receiving account set, neither rail is configured")

    cfg = load_with(TELEBIRR_NUMBER="0912345678")
    assert cfg.telebirr_configured is True
    assert cfg.abyssinia_configured is False
    ok("a receiving number alone is enough to configure Telebirr")

    cfg = load_with(ABYSSINIA_ACCOUNT="1000123456789")
    assert cfg.abyssinia_configured is True
    assert cfg.telebirr_configured is False
    ok("Abyssinia configures independently of Telebirr")

    # ---- verification needs NO configuration of its own ----------------------

    print("\nVerification is hosted by default")

    cfg = load_with()
    assert cfg.self_hosted_verify is False
    ok("with nothing set, the deployment is not self-hosting — Zentra verifies")

    cfg = load_with(TELEBIRR_VERIFY_URL="http://127.0.0.1:3001")
    assert cfg.self_hosted_verify is False
    ok("a self-host URL with no key does NOT switch modes — it would be an open endpoint")

    cfg = load_with(TELEBIRR_VERIFY_URL="http://127.0.0.1:3001",
                    TELEBIRR_VERIFY_KEY="own-key")
    assert cfg.self_hosted_verify is True
    ok("a URL and a key together point at the deployment's own instance")

    # ---- screenshot reading is opt-in, on the reseller's own key -------------

    print("\nScreenshot reading")

    cfg = load_with()
    assert cfg.receipt_scan_enabled is False
    assert cfg.openrouter_model == ""
    ok("off unless the reseller brings their own OpenRouter key")

    cfg = load_with(OPENROUTER_API_KEY="sk-or-v1-test")
    assert cfg.receipt_scan_enabled is True
    ok("a key alone turns it on — the model has a sensible default")

    cfg = load_with(OPENROUTER_API_KEY="sk-or-v1-test",
                    OPENROUTER_MODEL="qwen/qwen-2.5-vl-7b-instruct")
    assert cfg.openrouter_model == "qwen/qwen-2.5-vl-7b-instruct"
    ok("and the model is overridable, so cost can be traded against accuracy")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    main()
