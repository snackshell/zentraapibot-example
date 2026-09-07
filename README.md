# Zentra API Starter — Bot + Admin Dashboard

A complete, working example of reselling through the
[Zentra Reseller API](https://zentradigital.shop/api/docs): a Telegram bot
that sells Zentra's catalogue to your own customers, and an admin dashboard
to run it — your markup, your payment rails, your customers, all under your
control.

**You keep the difference between what your customers pay and what Zentra
charges your API key.** Zentra delivers instantly; you decide the price and
how your own customers pay you.

## What this is, honestly

This is being built in phases, each one a complete slice that runs and is
tested on its own — not a stub with `# TODO` where the money would go.

| Phase | What it adds | Status |
|---|---|---|
| **1** | The bot itself: catalogue, wallet, buying, the Zentra API client, the database, the settings system | ✅ **done** |
| **2** | USDT (BEP-20) — automatic, watched on-chain by polling a single RPC endpoint | ✅ **done** |
| **3** | Binance Pay — automatic, read from the operator's own account (no merchant integration) | ✅ **done** |
| **4** | The admin dashboard — overview, settings, orders, customers, deposits, credit by hand | ✅ **done** |
| **5** | Telebirr + Bank of Abyssinia — manual by default, automatic with LocalPaymentVerify | ✅ **done** |

**Right now, with just Phase 1**, the bot runs completely: customers browse
the real Zentra catalogue at your markup, and an admin credits a balance by
hand — a raw `UPDATE users SET balance_usd = ...`, or the dashboard's
"Credit by hand" page from Phase 4 onward. That is not a placeholder — it
is the exact fallback every payment rail below keeps forever, in this
project and in ZentraShopBot itself, because a payment that does not match
anything automatic should never mean a customer simply loses their money.

## Why a starter kit, not a one-click fork

The real Zentra shop this is modelled on runs a VPS, a chain listener, a
Binance merchant account, a separate payment-verification service, and
~15,000 lines of code refined against real customers over months. Handing
you all of that as day-one requirements would mean most people who want to
resell never get through setup. This gives you a running bot today, and
each rail as a self-contained addition you can adopt when you are ready for
what it requires — never before.

## Quickstart (Phase 1)

```bash
cd bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # fill in BOT_TOKEN, ZENTRA_API_KEY, DATABASE_URL
```

**Get a Zentra API key**: open [@ZentraShopBot](https://t.me/ZentraShopBot),
Menu → API Link → Create API key. Full reference at
[zentradigital.shop/api/docs](https://zentradigital.shop/api/docs).

**Get a database**: a free [Supabase](https://supabase.com) project works.
Create one, then apply the migration:

```bash
psql "$DATABASE_URL" -f supabase/migrations/0001_initial_schema.sql
```

(Use the **session pooler** connection string, port 5432 — see
`bot/db.py`'s own comment if you are used to the opposite advice for
serverless. This process is long-running, not serverless, and the session
pooler is what stays warm.)

**Run it:**

```bash
python -m bot.bot
```

Open your bot in Telegram, `/start`, tap Shop. To let a customer actually
buy something without a payment rail turned on, credit them by hand:

```sql
UPDATE users SET balance_usd = balance_usd + 10.00 WHERE telegram_id = 123456789;
INSERT INTO wallet_txns (user_id, amount_usd, kind) VALUES (
  (SELECT id FROM users WHERE telegram_id = 123456789), 10.00, 'admin_credit');
```

### Turning on USDT (BEP-20) top-ups — Phase 2

1. Apply the second migration: `psql "$DATABASE_URL" -f supabase/migrations/0002_usdt_deposits.sql`
2. Get a public BSC receiving address — a wallet you hold, never an
   exchange deposit address (see `docs/GUIDE.md` §9 for why).
3. Get one HTTP JSON-RPC endpoint for BNB Smart Chain — a free tier from
   dRPC, Ankr, or PublicNode all work.
4. Set `BSC_PAYMENT_ADDRESS` and `BSC_HTTP_URL` in `.env`, then restart the bot.
5. Turn the rail on: flip "USDT (BEP-20) top-ups" to Yes on the dashboard's
   Settings page, or `UPDATE settings SET value = 'yes' WHERE key = 'usdt_enabled';`
   directly.

Both the `.env` values and that setting have to be true together — either
one missing and the top-up screen simply isn't offered. `docs/GUIDE.md` §9
covers how the watcher recognises a payment (the amount itself is the
fingerprint), the confirmation delay, and why this starter polls a single
endpoint instead of running ZentraShopBot's own multi-provider WebSocket
listener.

### Turning on Binance Pay top-ups — Phase 3

**Not a merchant integration** — it reads your own personal Binance
account's Pay history, the same way ZentraShopBot itself does this.

1. Apply the third migration: `psql "$DATABASE_URL" -f supabase/migrations/0003_binance_pay.sql`
2. Binance app → Profile → API Management → create a key with **read-only**
   permission. Never enable withdrawals or trading on it.
3. Find your Binance ID (a UID, near the top of your Profile page) — this
   is what customers send to.
4. Set `BINANCE_UID`, `BINANCE_API_KEY` and `BINANCE_API_SECRET` in `.env`,
   then restart the bot.
5. Turn the rail on: flip "Binance Pay top-ups" to Yes on the dashboard's
   Settings page, or `UPDATE settings SET value = 'yes' WHERE key = 'binance_pay_enabled';`
   directly.

Same rule as USDT: both the `.env` credentials and that setting have to be
true together. `docs/GUIDE.md` §10 covers the exact-amount matching (the
same fingerprint trick as USDT, at a different precision), why one sweep
covers every open request in a single API call, and the two-signal check
that decides whether a transaction is really a payment IN before anything
is credited.

### Turning on the admin dashboard — Phase 4

A separate Next.js app in `dashboard/`, run and deployed on its own — it
reads and writes the exact same database as the bot, so nothing here needs
its own copy of anything.

```bash
psql "$DATABASE_URL" -f supabase/migrations/0004_admin_dashboard.sql
cd dashboard
npm install
cp .env.example .env.local   # DATABASE_URL (session pooler) + ADMIN_PASSWORD
npm run dev
```

Open `http://localhost:3000`, sign in with `ADMIN_PASSWORD`. Seven pages:
**Overview** (revenue, cost, profit, balances held, at a glance), **Orders**
and **Customers** (searchable, with the same numbers the bot itself
computed at the time — nothing here re-derives a price after the fact),
**Deposits** (every top-up request on every rail, and whether it was
credited), **Local Payments** (Phase 5's review queue for Telebirr/Abyssinia
requests waiting on a person), **Credit by hand** (the fallback above, from
a form instead of raw SQL, still guarded by the same conditional-`UPDATE`
rule as every other balance change in this project), and **Settings** (the
markup and rail toggles above, editable without a restart or a database
console).

One password, not a per-admin account system — see
`dashboard/src/lib/session.ts` for why that is deliberate: rotating
`ADMIN_PASSWORD` signs every existing session out at once, on every device.
Deploying it (Vercel or anywhere else that runs Next.js) needs the same two
environment variables set in that platform's project settings — nothing
about the app changes between `npm run dev` and a real deploy.

`docs/GUIDE.md` §11 covers the dashboard in full: the session-cookie design,
why it connects through the session pooler like the bot does, and the
same-guard-as-the-bot principle behind "Credit by hand" and every write
this app makes.

### Turning on Telebirr / Bank of Abyssinia — Phase 5

**Manual by default.** With nothing more than a receiving account
configured, both rails work exactly like "Credit by hand" always has: a
customer sends a receipt reference, it waits in the dashboard's **Local
Payments** page, and an admin credits or rejects it. Automatic
verification is an upgrade on top of that, not a requirement to start.

```bash
psql "$DATABASE_URL" -f supabase/migrations/0005_local_payments.sql
```

1. Set `TELEBIRR_NUMBER`/`TELEBIRR_NAME` and/or `ABYSSINIA_ACCOUNT`/
   `ABYSSINIA_NAME` in `.env`, then restart the bot. A rail with no account
   set is not offered at all — see `bot/config.py`'s
   `telebirr_configured`/`abyssinia_configured`.
2. Turn the rail(s) on: flip "Telebirr top-ups" / "Bank of Abyssinia
   top-ups" to Yes on the dashboard's Settings page, or
   `UPDATE settings SET value = 'yes' WHERE key = 'telebirr_enabled';`
   directly (and the same for `abyssinia_enabled`).
3. Check `usdt_to_etb` on the Settings page is close to the real rate — it
   is what converts a birr receipt into the USDT this wallet actually
   holds, and only ever affects that conversion, never product pricing.

**To add automatic verification**, run a
[LocalPaymentVerify](https://github.com/snackshell/localpaymentverify)
instance (typically on the same box, loopback-only — it holds provider
credentials this bot never sees), set `LOCAL_VERIFY_URL` and
`LOCAL_VERIFY_API_KEY` in `.env`, and leave `telebirr_verify_enabled` /
`abyssinia_verify_enabled` at their shipped default (Yes) on the Settings
page. Turn either off — or simply don't run the verifier — to keep that
one rail on manual review while everything else stays automatic.

`docs/GUIDE.md` §12 covers the full design: why this rail matches by
*reference* rather than amount, the receiver-account check that is the
whole difference between a real payment and a stranger's genuine receipt
pasted into your bot, and how a submitted reference reaches exactly the
same credit path — and the same exactly-once guarantee — as every other
rail.

## Architecture

```
bot/
  zentra_api.py   the ONLY file that talks to Zentra — auth, errors, money as Decimal
  bot.py          the Telegram bot; purchase() moves money out, deposits move it in
  db.py           your own customers, wallet ledger, orders, deposits — never Zentra's data
  chain/          Phase 2: USDT (BEP-20) — abi.py decodes, rpc.py asks the chain,
                  watcher.py polls and credits
  binance_pay.py  Phase 3: Binance Pay — reads the operator's own account,
                  no merchant integration
  localverify.py  Phase 5: the LocalPaymentVerify client — fetches a Telebirr/
                  Abyssinia receipt, decides nothing about it
  localpay.py     Phase 5: every rule a fetched receipt has to pass before
                  it becomes money — reference, receiver, amount, freshness
  settings.py     the runtime overlay: markup and rail toggles, editable without a restart
  pricing.py      your markup, applied once, in one place
  config.py       secrets and infrastructure, read once from .env
  money.py        Decimal rounding and display — the only place either happens

supabase/migrations/   your schema, applied in order, same convention as ZentraShopBot
dashboard/              Phase 4: the admin dashboard — a separate Next.js app
  src/lib/              searches and writes, kept apart from pages so they test
                        against a real database with no request in sight
  src/app/              one route per page: overview, orders, customers,
                        deposits, local-payments, credit, settings, plus the
                        login/session API
docs/                   the full guide — auth, idempotency, every payment rail
tests/                  a real PostgreSQL test suite; see "Testing" below
```

**One rule worth understanding before you change anything**: `zentra_api.py`
is the only file in this repository that makes a network request to Zentra.
Every screen, every dashboard number, every payment rail eventually reduces
to a call made there. If you find yourself calling `httpx` directly from
`bot.py` for something Zentra-related, that call belongs in `zentra_api.py`
instead.

## Money, handled the way it has to be

- Every amount is `Decimal`, constructed from a **string** — never a float.
  `Decimal(0.1)` has already lost precision before Decimal ever sees it.
- A balance changes only through `wallet_txns`, in the same transaction as
  the balance update — the two can never disagree.
- A debit is guarded in the `UPDATE`'s own `WHERE` clause
  (`bot/db.py:adjust_balance`), not by a `SELECT` beforehand. Two customers
  racing to spend the last of a balance must resolve to exactly one winner,
  and only the database serialising the two `UPDATE`s guarantees that — a
  check-then-write pair of statements cannot. This is not a hypothetical:
  the test suite proves it by literally breaking the guard and watching the
  database's own `CHECK` constraint catch the resulting negative balance as
  an unhandled crash, then confirms the real code returns a clean refusal
  instead.
- **`X-Idempotency-Key` is sent on every order.** Retry the exact same
  request — after a timeout, after your process restarts mid-purchase —
  with the same key, and Zentra returns the original order rather than
  placing a second one. `bot.py`'s `purchase()` makes this key once, before
  Zentra is ever called, and stores it with the order before the request is
  sent.
- **The `unresolved` case is not a refund case.** If Zentra's own call to
  its supplier dies in transit, your wallet may already have been charged —
  refunding your customer on top of that is how a customer gets free stock.
  `purchase()` holds the order for a human instead. This is the single most
  important branch in the whole codebase; it has its own test in
  `tests/test_purchase.py`, verified by sabotaging each direction and
  watching it fail.
- **A deposit's amount is unique while it can still be paid — decided by the
  database, not a `SELECT`.** BEP-20 transfers carry no memo field, so the
  exact amount (a tiny decimal tail added on top of what a customer asked
  for) is the only thing identifying which deposit a payment belongs to.
  `allocate_deposit()` attempts each candidate as an `INSERT` and lets a
  partial `UNIQUE` index decide whether it collided — proven in
  `tests/test_deposits.py` by firing several concurrent requests at once and
  checking every one gets a genuinely distinct figure.
- **An expired deposit's amount stays reserved.** A customer's exchange
  withdrawal can take longer than the request stayed open; if the exact
  figure were freed the moment it expired, a late payment could credit
  whoever is issued that same amount next. The cooldown closes that gap,
  and the test suite proves it by sabotaging the check and watching a second
  customer get issued an amount still cooling down.
- **A transaction hash credits at most one deposit, ever** — a `UNIQUE`
  index, not application logic remembering. The same on-chain event
  arriving twice (a restart, an overlapping poll window) is a no-op the
  second time, not a second credit.
- **A deposit's amount is unique PER RAIL, not globally.** A $20 USDT
  request and a $20 Binance Pay request are matched by two entirely
  different workers reading two entirely different systems, so there is no
  reason to make them compete for the same figure — each rail's allocator
  is scoped by `method` in the same index that makes the amount unique.
  Proved by literally dropping that scoping and watching two rails
  genuinely collide over one figure, then restoring it.
- **A Binance Pay transaction is trusted only when two independent signals
  agree it is incoming: the sign of the amount, and whether the receiver
  id is the operator's own account.** The one time they might disagree — a
  transaction type nobody anticipated — the payment is refused rather than
  guessed at, exactly as ZentraShopBot's own matching does.
- **Telebirr and Bank of Abyssinia identify a payment by its RECEIPT
  REFERENCE, not its amount** — the same `tx_hash` column and the same
  `credit_deposit()` function every other rail credits through, so
  crediting the same reference twice, from any two callers (the bot's own
  automatic check, an admin's manual approval, both at once), is the exact
  no-op it already was for a duplicated on-chain event or a re-swept
  Binance Pay transfer.
- **A Telebirr/Abyssinia receipt is trusted only after its RECEIVER is
  checked against the configured account** — a Telebirr receipt is a
  public web page, and this is the entire difference between a real
  payment and a stranger's genuine receipt for someone else pasted into
  your bot. `tests/test_localpay.py` proves the refusal for a receipt paid
  to any other account, however real that receipt is.

## Testing

```bash
cd bot && pip install -r requirements-dev.txt
export TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/some_empty_db
cd ..
python -m tests.test_money
python -m tests.test_pricing
python -m tests.test_config
python -m tests.test_zentra_api
python -m tests.test_chain_abi
python -m tests.test_chain_rpc
python -m tests.test_binance_pay
python -m tests.test_binance_client
python -m tests.test_db
python -m tests.test_settings
python -m tests.test_deposits
python -m tests.test_watcher
python -m tests.test_binance_sweep
python -m tests.test_purchase
python -m tests.test_topup_flow
python -m tests.test_local_deposits
python -m tests.test_localverify
python -m tests.test_localpay
python -m tests.test_lint
```

`TEST_DATABASE_URL` must point at an **empty, disposable** database — the
fixture drops every table in the public schema before each run. A URL
containing `supabase.co` or `supabase.com` is refused outright, so a
misconfigured environment variable cannot destroy a real project.

Every test that can be proven wrong, is: money-handling tests were
verified during development by deliberately breaking the guard they check,
confirming the test fails on the right assertion, and restoring it — the
same discipline ZentraShopBot itself is held to.

### The dashboard's own tests

Apply migrations 0001-0004 to the same disposable database above, then:

```bash
cd dashboard
npm install
export TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/some_empty_db
npm test
```

`npm test` runs `node --test` directly against the `.ts` sources (Node's
own TypeScript stripping — no build step, no test framework beyond what
Node ships) across `tests/auth.test.mjs`, `tests/retry.test.mjs`,
`tests/settings.test.mjs`, `tests/credit.test.mjs`, `tests/customers.test.mjs`,
`tests/orders.test.mjs`, `tests/deposits.test.mjs` and
`tests/localpayments.test.mjs`. The same `supabase.co`/`supabase.com`
refusal as the bot's own fixture applies here too — every test file that
touches the database checks for it before doing anything else. `npm run
typecheck` and `npm run build` both need to pass clean as well; a page
that only "looks right" in the editor is not done until Next.js has
actually compiled it.

## License

MIT — see `LICENSE`. Build on this, resell with it, change anything.
