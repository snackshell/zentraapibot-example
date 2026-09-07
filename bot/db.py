"""Async PostgreSQL access for YOUR customers, YOUR wallet ledger, YOUR
orders. This never talks to Zentra — that is zentra_api.py's job.

The connection and the two guards below (adjust_balance, claim_order) are
carried from ZentraShopBot with the reasoning intact, because the reasoning
does not get weaker just because this is a starter kit — a race a customer
can trigger by tapping twice is a race that WILL happen in production the
first busy day this bot has.
"""

from __future__ import annotations

import logging
import random
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)

Row = dict[str, Any]


class Db:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10):
        if not dsn:
            raise RuntimeError(
                "DATABASE_URL is not set. See README.md for how to create "
                "one on Supabase's free tier."
            )
        self._dsn = dsn
        # open=False so constructing Db never blocks or raises at import
        # time; connect() opens it explicitly during startup, where a
        # failure can be logged and the process can exit cleanly instead of
        # hanging on an unawaited coroutine.
        self._pool = AsyncConnectionPool(
            dsn, min_size=min_size, max_size=max_size,
            kwargs={"row_factory": dict_row}, open=False,
        )

    async def connect(self) -> None:
        await self._pool.open(wait=True, timeout=30)
        async with self._pool.connection() as conn:
            exists = await conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'users'"
            )
            if await exists.fetchone() is None:
                raise RuntimeError(
                    "The 'users' table does not exist. Apply the migrations "
                    "in supabase/migrations/ before starting the bot."
                )

    async def close(self) -> None:
        await self._pool.close()

    async def execute(self, sql: str, params: tuple = ()) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return cur.rowcount

    async def fetchone(self, sql: str, params: tuple = ()) -> Row | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[Row]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchall()

    async def insert(self, sql: str, params: tuple = ()) -> int:
        """An INSERT ... RETURNING id, returning just the id."""
        row = await self.fetchone(sql, params)
        return row["id"]

    # ---- customers -----------------------------------------------------------

    async def ensure_user(self, telegram_id: int, username: str | None) -> Row:
        """Fetch this customer, creating the row on their first /start.

        ON CONFLICT DO UPDATE rather than DO NOTHING: a username changes,
        and the row should say who somebody is NOW, not who they were the
        day they first opened the bot.
        """
        row = await self.fetchone(
            "INSERT INTO users (telegram_id, username) VALUES (%s, %s) "
            "ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username "
            "RETURNING *",
            (telegram_id, username),
        )
        assert row is not None
        return row

    async def user_by_telegram_id(self, telegram_id: int) -> Row | None:
        return await self.fetchone(
            "SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))

    async def user_by_id(self, user_id: int) -> Row | None:
        return await self.fetchone("SELECT * FROM users WHERE id = %s", (user_id,))

    async def adjust_balance(
        self, user_id: int, amount: Decimal, kind: str, ref: str | None = None
    ) -> bool:
        """Move a customer's balance and journal it, atomically.

        Returns False if a debit would overdraw. THE GUARD LIVES IN THE
        UPDATE'S OWN WHERE CLAUSE — `balance_usd + %s >= 0` — not in a SELECT
        beforehand. That is what makes two purchases racing on the last of a
        balance resolve to exactly one winner: the database serialises the
        two UPDATEs, and the second one's WHERE clause sees the first one's
        result. A "check the balance, then update" pair of statements cannot
        do this — both checks can pass before either update lands.
        """
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "UPDATE users SET balance_usd = balance_usd + %s "
                    "WHERE id = %s AND balance_usd + %s >= 0",
                    (amount, user_id, amount),
                )
                if cur.rowcount == 0:
                    return False
                await conn.execute(
                    "INSERT INTO wallet_txns (user_id, amount_usd, kind, ref) "
                    "VALUES (%s, %s, %s, %s)",
                    (user_id, amount, kind, ref),
                )
        return True

    async def balance(self, user_id: int) -> Decimal:
        row = await self.fetchone(
            "SELECT balance_usd FROM users WHERE id = %s", (user_id,))
        return Decimal(row["balance_usd"]) if row else Decimal(0)

    async def wallet_history(self, user_id: int, limit: int = 20) -> list[Row]:
        return await self.fetchall(
            "SELECT * FROM wallet_txns WHERE user_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        )

    # ---- orders -----------------------------------------------------------

    async def create_pending_order(
        self, *, user_id: int, zentra_product_id: str, product_name: str,
        quantity: int, price_snapshot: Decimal, idempotency_key: str,
        zentra_price_snapshot: Decimal | None = None,
    ) -> Row:
        """A row that exists from the moment money moves, before Zentra is
        ever called. An order that only got written down AFTER a successful
        Zentra call would leave a debited customer with no order to show
        for it if the process died in between — exactly the failure mode
        idempotency_key's UNIQUE constraint exists to make retryable rather
        than silent.

        `zentra_price_snapshot` is what Zentra will charge YOUR wallet for
        this order — known before the call, since it is just the product's
        own listed price times the quantity. Recorded here rather than
        after delivery so it exists even for an order that fails: the
        admin dashboard's profit figure should not go blank on the one
        order that needed explaining.
        """
        return await self.fetchone(
            "INSERT INTO orders (user_id, zentra_product_id, product_name, "
            "quantity, price_snapshot, zentra_price_snapshot, idempotency_key, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending') RETURNING *",
            (user_id, str(zentra_product_id), product_name, quantity,
             price_snapshot, zentra_price_snapshot, idempotency_key),
        )

    async def mark_order_delivered(
        self, order_id: int, *, zentra_order_id: str, zentra_reference: str,
        delivered_payload: str | None,
    ) -> None:
        await self.execute(
            "UPDATE orders SET status = 'delivered', zentra_order_id = %s, "
            "zentra_reference = %s, delivered_payload = %s, completed_at = now() "
            "WHERE id = %s",
            (zentra_order_id, zentra_reference, delivered_payload, order_id),
        )

    async def mark_order_unresolved(self, order_id: int, *, error: str) -> None:
        await self.execute(
            "UPDATE orders SET status = 'unresolved', error = %s WHERE id = %s",
            (error, order_id),
        )

    async def mark_order_failed(self, order_id: int, *, error: str) -> None:
        await self.execute(
            "UPDATE orders SET status = 'failed', error = %s WHERE id = %s",
            (error, order_id),
        )

    async def order_by_idempotency_key(self, key: str) -> Row | None:
        return await self.fetchone(
            "SELECT * FROM orders WHERE idempotency_key = %s", (key,))

    async def user_orders(self, user_id: int, limit: int = 50) -> list[Row]:
        return await self.fetchall(
            "SELECT * FROM orders WHERE user_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        )

    async def order_by_id(self, order_id: int) -> Row | None:
        return await self.fetchone("SELECT * FROM orders WHERE id = %s", (order_id,))

    # ---- settings -----------------------------------------------------------

    async def live_settings(self) -> list[Row]:
        return await self.fetchall("SELECT * FROM settings ORDER BY sort_order")

    async def setting_row(self, key: str) -> Row | None:
        return await self.fetchone("SELECT * FROM settings WHERE key = %s", (key,))

    async def write_setting(self, key: str, value: str, *, updated_by: int | None) -> None:
        """Change one setting and journal the change, in one transaction —
        so there is no moment where the value changed and nothing recorded
        who did it or what it used to be."""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                row = await (await conn.execute(
                    "SELECT value FROM settings WHERE key = %s", (key,)
                )).fetchone()
                old_value = row["value"] if row else None
                await conn.execute(
                    "UPDATE settings SET value = %s, updated_at = now(), "
                    "updated_by = %s WHERE key = %s",
                    (value, updated_by, key),
                )
                await conn.execute(
                    "INSERT INTO settings_history (key, old_value, new_value, changed_by) "
                    "VALUES (%s, %s, %s, %s)",
                    (key, old_value, value, updated_by),
                )

    # ---- USDT deposits (Phase 2) -------------------------------------------
    #
    # HOW A DEPOSIT IS IDENTIFIED: by its exact amount, tail included — see
    # the header comment on the migration for why. Every method below exists
    # to keep that amount honest: unique while awaiting, reserved for a
    # while after it stops being awaiting, and matched exactly once.

    async def allocate_deposit(
        self, *, user_id: int, base_amount: Decimal, window_minutes: int,
        cooldown_minutes: int, method: str = "usdt", tail_min: int = 1,
        tail_max: int = 99, tail_decimals: int = 4,
    ) -> Row:
        """Reserve a unique amount for this customer on this rail, and
        return it.

        THE DATABASE DECIDES WHICH AMOUNT IS FREE, NOT A SELECT. Each
        candidate tail is simply attempted as an INSERT; the partial unique
        index on (method, amount_expected) WHERE status='awaiting' answers
        "is this amount free right now, for THIS rail?" at the only moment
        that question matters — a SELECT taken a moment earlier could
        already be stale by the time this INSERT lands, and two customers
        would then be told to send the exact same figure.

        SCOPED BY METHOD: a $20 USDT request and a $20 Binance Pay request
        are matched by two entirely different workers reading two entirely
        different systems, so they cannot be confused for one another —
        there is no reason to make the two rails compete for the same
        amount-space, and every rail gets the full tail range to itself.

        The WHERE NOT EXISTS clause is the other half: it refuses a tail
        still cooling down from a request that expired but might yet be
        paid, on the SAME rail. That check has its own tiny race — two
        INSERTs evaluating it at once could both pass — but if they do,
        both attempt to become 'awaiting' with the same (method, amount),
        and the TRUE unique index catches that collision regardless,
        converting one into a retry.

        Tails are shuffled, not tried in order, so two callers starting at
        the same instant diverge immediately instead of colliding down the
        same range together.
        """
        tails = list(range(tail_min, tail_max + 1))
        random.shuffle(tails)
        scale = Decimal(10) ** tail_decimals
        step = Decimal(1).scaleb(-tail_decimals)

        for tail in tails:
            exact = (base_amount + Decimal(tail) / scale).quantize(step)
            credited = exact.quantize(Decimal("0.01"))

            try:
                row = await self.fetchone(
                    "INSERT INTO deposits (user_id, method, amount_expected, "
                    "amount_credited, expires_at, cooldown_until) "
                    "SELECT %s, %s, %s, %s, "
                    "       now() + make_interval(mins => %s), "
                    "       now() + make_interval(mins => %s) "
                    "WHERE NOT EXISTS ("
                    "  SELECT 1 FROM deposits "
                    "   WHERE method = %s AND amount_expected = %s "
                    "     AND cooldown_until > now())"
                    "RETURNING *",
                    (user_id, method, exact, credited, window_minutes,
                     window_minutes + cooldown_minutes, method, exact),
                )
            except psycopg.errors.UniqueViolation:
                continue  # another deposit holds this amount, right now

            if row is not None:
                return row
            # NOT EXISTS refused it: the amount is still cooling down.

        raise RuntimeError(
            "Could not allocate a unique deposit amount — every increment is "
            "in use or cooling down. Try again shortly."
        )

    async def deposit_by_id(self, deposit_id: int) -> Row | None:
        return await self.fetchone("SELECT * FROM deposits WHERE id = %s", (deposit_id,))

    async def open_deposit_for_amount(self, amount: Decimal, *, method: str = "usdt") -> Row | None:
        """The live deposit on this rail expecting exactly this amount, if any.

        `status IN ('awaiting', 'expired')` on purpose: a late payment on an
        expired request is still real money owed to that customer, not
        found money for whoever asks for the same figure next — that is the
        entire point of the cooldown reservation.
        """
        return await self.fetchone(
            "SELECT * FROM deposits WHERE method = %s AND amount_expected = %s "
            "  AND status IN ('awaiting', 'expired') AND tx_hash IS NULL",
            (method, amount),
        )

    async def credit_deposit(
        self, deposit_id: int, *, tx_hash: str, amount_credited: Decimal | None = None,
    ) -> Row | None:
        """Claim a transaction hash and credit the wallet, in ONE transaction.

        Returns the credited deposit, or None if this call did not do the
        crediting — either because another call already claimed this exact
        deposit, or because the tx_hash has already been used to credit a
        DIFFERENT deposit (the UNIQUE index on tx_hash raises, caught below).

        THE CLAIM, THE BALANCE AND THE LEDGER COMMIT TOGETHER OR NOT AT ALL.
        A crash between crediting the deposit and crediting the wallet would
        otherwise leave a deposit marked paid with nobody actually paid —
        money lost, silently, with a log line claiming success.

        `amount_credited`, when given, OVERWRITES the figure stored on the
        deposit rather than trusting it. USDT and Binance Pay know exactly
        what they will credit the moment the request is opened — the exact
        amount IS the fingerprint — so they leave this as None and the
        value set at allocation time stands. Telebirr and Bank of Abyssinia
        do not: the reference identifies the payment, and what it turns
        out to be worth is only known once a receipt is read (or an admin
        reads one by hand), which can be this call's very first look at
        that figure. `tx_hash` is reused as this rail's replay key too —
        see migration 0005's comment on the column for why that is a
        rename this project chooses not to make.
        """
        async with self._pool.connection() as conn:
            try:
                async with conn.transaction():
                    if amount_credited is not None:
                        cur = await conn.execute(
                            "UPDATE deposits SET status = 'credited', tx_hash = %s, "
                            "amount_credited = %s, credited_at = now() "
                            "WHERE id = %s AND status IN ('awaiting', 'expired') "
                            "  AND tx_hash IS NULL "
                            "RETURNING *",
                            (tx_hash, amount_credited, deposit_id),
                        )
                    else:
                        cur = await conn.execute(
                            "UPDATE deposits SET status = 'credited', tx_hash = %s, "
                            "credited_at = now() "
                            "WHERE id = %s AND status IN ('awaiting', 'expired') "
                            "  AND tx_hash IS NULL "
                            "RETURNING *",
                            (tx_hash, deposit_id),
                        )
                    deposit = await cur.fetchone()
                    if deposit is None:
                        return None

                    await conn.execute(
                        "UPDATE users SET balance_usd = balance_usd + %s WHERE id = %s",
                        (deposit["amount_credited"], deposit["user_id"]),
                    )
                    await conn.execute(
                        "INSERT INTO wallet_txns (user_id, amount_usd, kind, ref) "
                        "VALUES (%s, %s, %s, %s)",
                        (deposit["user_id"], deposit["amount_credited"], "topup",
                         f"deposit:{deposit['id']}"),
                    )
                    return deposit
            except psycopg.errors.UniqueViolation:
                # This tx_hash already credited a different deposit — seen
                # twice by a watcher's overlapping poll (crypto rails), or
                # the same receipt submitted to two different requests and
                # both attempting to settle (local rails). Either way, this
                # is the no-op that makes the second attempt safe.
                log.info("tx_hash %s was already used to credit a deposit.", tx_hash)
                return None

    async def expire_deposits(self) -> int:
        """Close awaiting deposits whose window has passed.

        Their amount stays reserved — cooldown_until is untouched — so a
        late payment can still find and credit them via
        open_deposit_for_amount() above.
        """
        return await self.execute(
            "UPDATE deposits SET status = 'expired' "
            "WHERE status = 'awaiting' AND expires_at < now()"
        )

    async def user_deposits(self, user_id: int, limit: int = 20) -> list[Row]:
        return await self.fetchall(
            "SELECT * FROM deposits WHERE user_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        )

    # ---- Telebirr / Bank of Abyssinia deposits (Phase 5) -------------------
    #
    # These rails identify a payment by its REFERENCE, not its amount — see
    # localpay.py's own header for why. So there is no allocator here: a
    # request is just opened, and `amount_expected` is informational (what
    # the customer said they intended to send, in ETB) rather than a
    # fingerprint two requests could collide over. credit_deposit() above
    # is still what actually moves money, exactly as it is for USDT and
    # Binance Pay — only how a deposit gets THERE differs.

    async def open_local_deposit(
        self, *, user_id: int, method: str, amount_etb: Decimal, window_minutes: int,
    ) -> Row:
        """Open a Telebirr or Abyssinia request. `amount_credited` starts at
        0 and is overwritten by credit_deposit()'s own override the moment
        a real figure is known — never displayed or used before then."""
        return await self.fetchone(
            "INSERT INTO deposits (user_id, method, amount_expected, "
            "amount_credited, expires_at, cooldown_until) "
            "VALUES (%s, %s, %s, 0, now() + make_interval(mins => %s), "
            "        now() + make_interval(mins => %s)) RETURNING *",
            (user_id, method, amount_etb, window_minutes, window_minutes),
        )

    async def submit_local_reference(
        self, deposit_id: int, *, reference: str, suffix: str | None = None,
    ) -> Row | None:
        """Attach the receipt reference a customer typed to their open
        request, so it exists for a human to review even before — or
        instead of — automatic verification. Returns None if the request
        is no longer open to receive one (already credited or rejected),
        so a stale retry cannot overwrite a resolved request's record."""
        return await self.fetchone(
            "UPDATE deposits SET reference = %s, suffix = %s "
            "WHERE id = %s AND status IN ('awaiting', 'expired') AND tx_hash IS NULL "
            "RETURNING *",
            (reference, suffix, deposit_id),
        )

    async def reject_local_deposit(self, deposit_id: int, *, note: str) -> Row | None:
        """An admin's clean 'no' — the counterpart to crediting by hand.

        Guarded the same shape as every other resolution on this table:
        the WHERE clause is what makes a deposit resolve exactly once,
        whichever of credit_deposit() or this gets there first.
        """
        return await self.fetchone(
            "UPDATE deposits SET status = 'rejected', note = %s "
            "WHERE id = %s AND status IN ('awaiting', 'expired') AND tx_hash IS NULL "
            "RETURNING *",
            (note, deposit_id),
        )

    # ---- internal bookkeeping (not a business setting) ---------------------

    async def get_state(self, key: str, default: str | None = None) -> str | None:
        row = await self.fetchone("SELECT value FROM bot_state WHERE key = %s", (key,))
        return row["value"] if row else default

    async def set_state(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO bot_state (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, value),
        )
