-- Phase 5: Telebirr and Bank of Abyssinia — manual by default, automatic
-- with LocalPaymentVerify.
--
-- THESE TWO RAILS DO NOT MATCH BY AMOUNT, unlike USDT and Binance Pay. A
-- Telebirr or Abyssinia transfer carries no field a bot could read a
-- unique tail out of the way BEP-20 and Binance Pay do — but the customer
-- can hand back something better: the provider's OWN receipt reference,
-- which already identifies the payment uniquely by itself. So these rails
-- join `deposits` (the same table, the same method-scoped shape) without
-- needing the amount-uniqueness that shape was built around — see the
-- rescoped index below.
--
-- MANUAL BY DEFAULT. Without LOCAL_VERIFY_URL configured, or with
-- verification turned off, a submitted reference simply waits in
-- `deposits` with status='awaiting' and a reference attached, for an
-- admin to credit or reject from the dashboard's Local Payments page —
-- exactly the fallback every rail in this project keeps forever.
--
-- tx_hash IS REUSED as the replay key for these rails too, not renamed.
-- The column already generalised past its name in migration 0003 (a
-- Binance transactionId is not a chain hash either); a normalised, upper-
-- cased Telebirr or Abyssinia reference fits the same shape: a UNIQUE
-- string that identifies one external event, and that credits at most one
-- deposit, ever, via db.py's credit_deposit() — the same function every
-- other rail already uses, unmodified in its own guarantee.

ALTER TABLE deposits ADD COLUMN reference TEXT;
ALTER TABLE deposits ADD COLUMN suffix TEXT;
ALTER TABLE deposits ADD COLUMN note TEXT;

COMMENT ON COLUMN deposits.reference IS
    'The receipt reference a customer submitted, normalised upper-case —
    Telebirr and Abyssinia only. Set the moment they submit it, so it
    exists for a human to review even before automatic verification runs,
    or when it never does. NOT the replay guard itself — see tx_hash,
    which is only ever set at the moment a deposit actually credits.';

COMMENT ON COLUMN deposits.suffix IS
    'The last 5 digits of the payer''s own account, which Bank of
    Abyssinia''s verification needs alongside the reference. Telebirr and
    the crypto rails never set this.';

COMMENT ON COLUMN deposits.note IS
    'Free text left by an admin resolving a Telebirr/Abyssinia request by
    hand — a rejection reason, or a note on why a figure was credited that
    did not match what automatic verification would have used.';

COMMENT ON COLUMN deposits.tx_hash IS
    'The replay key from whichever payment method credited this deposit —
    a 0x-prefixed chain transaction hash for method=usdt, a Binance
    transactionId for method=binancepay, or a normalised Telebirr/Abyssinia
    reference for those two rails. One column, one meaning across every
    rail: it is set only at the moment of crediting, and a UNIQUE index on
    it is what makes crediting the same external event twice a no-op.';

ALTER TABLE deposits DROP CONSTRAINT deposits_method_known;
ALTER TABLE deposits ADD CONSTRAINT deposits_method_known
    CHECK (method IN ('usdt', 'binancepay', 'telebirr', 'abyssinia'));

ALTER TABLE deposits DROP CONSTRAINT deposits_status_known;
ALTER TABLE deposits ADD CONSTRAINT deposits_status_known
    CHECK (status IN ('awaiting', 'expired', 'credited', 'rejected'));

-- THE AMOUNT-UNIQUENESS INDEX, RESCOPED TO THE RAILS THAT ACTUALLY NEED IT.
-- Two customers both intending to send exactly 1,000 ETB via Telebirr must
-- NOT collide the way two USDT requests for the same tail would — there is
-- no fingerprint here for them to collide over, and refusing the second
-- customer a top-up screen over a coincidence would be a bug, not a
-- safety guard. usdt and binancepay keep the exact behaviour they always
-- had; telebirr and abyssinia are simply outside this index's WHERE
-- clause now, the same way they always were outside its CHECK reasoning.
DROP INDEX idx_deposits_amount_awaiting;
CREATE UNIQUE INDEX idx_deposits_amount_awaiting
    ON deposits (method, amount_expected)
    WHERE status = 'awaiting' AND method IN ('usdt', 'binancepay');

CREATE INDEX idx_deposits_reference ON deposits (method, reference)
    WHERE reference IS NOT NULL;

INSERT INTO settings
    (key, default_value, value_type, min_value, max_value, category, label, help, sort_order)
VALUES
-- Off by default, and stays off until an account is configured — see
-- bot/config.py's telebirr_configured/abyssinia_configured: the bot
-- refuses to offer a rail whose receiver it cannot check a receipt
-- against.
('telebirr_enabled', 'no', 'bool', NULL, NULL, 'payments',
 'Telebirr top-ups',
 'Show the Telebirr rail. Needs TELEBIRR_NUMBER set in .env — verification '
 'compares a receipt''s receiver against that number, and without it '
 'there is nothing to compare a stranger''s valid receipt against.', 500),

('abyssinia_enabled', 'no', 'bool', NULL, NULL, 'payments',
 'Bank of Abyssinia top-ups',
 'Show the Bank of Abyssinia rail. Needs ABYSSINIA_ACCOUNT set in .env, '
 'the same reasoning as Telebirr above.', 510),

-- Separate from whether the rail is OFFERED, so a LocalPaymentVerify
-- outage drops a rail back to manual review without hiding it from
-- customers — exactly ZentraShopBot's own reasoning for this split.
('telebirr_verify_enabled', 'yes', 'bool', NULL, NULL, 'payments',
 'Verify Telebirr automatically',
 'Check a submitted reference against Telebirr itself through '
 'LOCAL_VERIFY_URL and credit automatically when it passes. Turn this off '
 '— or leave LOCAL_VERIFY_URL unset — to review every Telebirr top-up by '
 'hand instead; it never credits anything on its own.', 520),

('abyssinia_verify_enabled', 'yes', 'bool', NULL, NULL, 'payments',
 'Verify Bank of Abyssinia automatically',
 'The same for Bank of Abyssinia. Automatic verification always needs the '
 'five-digit account suffix the customer supplies alongside the '
 'reference.', 530),

('usdt_to_etb', '160', 'decimal', 10, 2000, 'payments',
 'USDT → ETB rate',
 'What one USDT is worth in birr, used only to DISPLAY Telebirr/Abyssinia '
 'amounts and to convert a verified birr receipt into the USDT this '
 'wallet actually holds. Keep this close to the real rate — it never '
 'touches product pricing, which stays in USDT throughout.', 540);
