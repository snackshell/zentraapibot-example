/**
 * Telebirr / Bank of Abyssinia — the manual review queue, and the same
 * exactly-once guard every other rail's crediting path shares.
 *
 * Run:  TEST_DATABASE_URL=postgresql://… node --test tests/localpayments.test.mjs
 */

import assert from "node:assert/strict";
import test from "node:test";
import postgres from "postgres";
import { approveLocalPayment, pendingLocalPayments, rejectLocalPayment } from "../src/lib/localpayments.ts";

const url = process.env.TEST_DATABASE_URL;

if (!url) {
  test("local payments", { skip: "TEST_DATABASE_URL is not set" }, () => {});
} else {
  if (url.includes("supabase.co") || url.includes("supabase.com")) {
    throw new Error("TEST_DATABASE_URL points at a hosted Supabase project.");
  }
  const sql = postgres(url, {
    max: 6, prepare: false,
    types: { numeric: { to: 1700, from: [1700], serialize: (x) => x, parse: (x) => x } },
  });

  const HANA = 890501;
  const IYOB = 890502;
  const JIMA = 890503;
  let hanaId, iyobId, jimaId;

  const openDeposit = async (userId, method, reference) => {
    const [row] = await sql`
      INSERT INTO deposits (user_id, method, amount_expected, amount_credited,
                             reference, expires_at, cooldown_until)
      VALUES (${userId}, ${method}, 500, 0, ${reference},
              now() + interval '1 hour', now() + interval '1 hour')
      RETURNING id
    `;
    return row.id;
  };

  test("seed", async () => {
    await sql`
      INSERT INTO users (telegram_id, username) VALUES
        (${HANA}, 'hana_pay'), (${IYOB}, 'iyob_pay'), (${JIMA}, 'jima_pay')
      ON CONFLICT (telegram_id) DO NOTHING
    `;
    [{ id: hanaId }] = await sql`SELECT id FROM users WHERE telegram_id = ${HANA}`;
    [{ id: iyobId }] = await sql`SELECT id FROM users WHERE telegram_id = ${IYOB}`;
    [{ id: jimaId }] = await sql`SELECT id FROM users WHERE telegram_id = ${JIMA}`;
  });

  test("a pending request appears in the review queue", async () => {
    const depositId = await openDeposit(hanaId, "telebirr", "ABCD123456");
    const pending = await pendingLocalPayments(sql);
    assert.ok(pending.some((p) => p.id === String(depositId) && p.reference === "ABCD123456"));
  });

  test("approving credits the customer's wallet for the entered figure", async () => {
    const depositId = await openDeposit(iyobId, "telebirr", "EFGH567890");
    const result = await approveLocalPayment(sql, {
      depositId, amountUsd: "3.13", note: "matched receipt by hand",
    });
    assert.equal(result.ok, true, result.ok ? "" : result.message);
    assert.equal(result.newBalance, "3.1300");

    const [row] = await sql`SELECT balance_usd FROM users WHERE id = ${iyobId}`;
    assert.equal(row.balance_usd, "3.1300");

    const [deposit] = await sql`SELECT status, tx_hash, note FROM deposits WHERE id = ${depositId}`;
    assert.equal(deposit.status, "credited");
    assert.equal(deposit.tx_hash, "EFGH567890");
    assert.equal(deposit.note, "matched receipt by hand");
  });

  test("approving the same request twice credits the wallet only once", async () => {
    const depositId = await openDeposit(iyobId, "telebirr", "IJKL901234");
    const before = await sql`SELECT balance_usd FROM users WHERE id = ${iyobId}`;

    const first = await approveLocalPayment(sql, { depositId, amountUsd: "5.00", note: "ok" });
    const second = await approveLocalPayment(sql, { depositId, amountUsd: "5.00", note: "ok again" });

    assert.equal(first.ok, true);
    assert.equal(second.ok, false);
    assert.match(second.message, /already resolved/);

    const [after] = await sql`SELECT balance_usd FROM users WHERE id = ${iyobId}`;
    assert.equal(after.balance_usd, (Number(before[0].balance_usd) + 5).toFixed(4));
  });

  // THE RACE. Ten admins (or one admin tapping twice) approving the same
  // request at once. Only the guard in the UPDATE's own WHERE clause can
  // guarantee exactly one credit — a check-then-write pair cannot.
  test("ten simultaneous approvals of one request: exactly one credits", async () => {
    const depositId = await openDeposit(jimaId, "abyssinia", "FT24RACE0001");
    const before = await sql`SELECT balance_usd FROM users WHERE id = ${jimaId}`;

    const results = await Promise.all(
      Array.from({ length: 10 }, () =>
        approveLocalPayment(sql, { depositId, amountUsd: "10.00", note: "race" })),
    );
    const won = results.filter((r) => r.ok);
    assert.equal(won.length, 1, `expected exactly one winner, got ${won.length}`);

    const [after] = await sql`SELECT balance_usd FROM users WHERE id = ${jimaId}`;
    assert.equal(after.balance_usd, (Number(before[0].balance_usd) + 10).toFixed(4));
  });

  test("rejecting requires a reason, and resolves the request without crediting anything", async () => {
    const depositId = await openDeposit(hanaId, "telebirr", "MNOP345678");
    const before = await sql`SELECT balance_usd FROM users WHERE id = ${hanaId}`;

    const empty = await rejectLocalPayment(sql, { depositId, note: "   " });
    assert.equal(empty.ok, false);

    const result = await rejectLocalPayment(sql, { depositId, note: "receipt did not match" });
    assert.equal(result.ok, true);

    const [deposit] = await sql`SELECT status, note FROM deposits WHERE id = ${depositId}`;
    assert.equal(deposit.status, "rejected");
    assert.equal(deposit.note, "receipt did not match");

    const [after] = await sql`SELECT balance_usd FROM users WHERE id = ${hanaId}`;
    assert.equal(after.balance_usd, before[0].balance_usd);
  });

  test("a rejected request cannot then be approved", async () => {
    const depositId = await openDeposit(hanaId, "telebirr", "QRST901234");
    await rejectLocalPayment(sql, { depositId, note: "no match" });
    const result = await approveLocalPayment(sql, { depositId, amountUsd: "5.00", note: "too late" });
    assert.equal(result.ok, false);
  });

  test("approving a request with no reference yet is refused", async () => {
    const [row] = await sql`
      INSERT INTO deposits (user_id, method, amount_expected, amount_credited,
                             expires_at, cooldown_until)
      VALUES (${hanaId}, 'telebirr', 500, 0, now() + interval '1 hour', now() + interval '1 hour')
      RETURNING id
    `;
    const result = await approveLocalPayment(sql, { depositId: row.id, amountUsd: "3.00", note: "x" });
    assert.equal(result.ok, false);
    assert.match(result.message, /no reference/);
  });

  test.after(async () => {
    await sql`DELETE FROM wallet_txns WHERE user_id IN
              (SELECT id FROM users WHERE telegram_id IN (${HANA}, ${IYOB}, ${JIMA}))`;
    await sql`DELETE FROM deposits WHERE user_id IN
              (SELECT id FROM users WHERE telegram_id IN (${HANA}, ${IYOB}, ${JIMA}))`;
    await sql`DELETE FROM users WHERE telegram_id IN (${HANA}, ${IYOB}, ${JIMA})`;
    await sql.end();
  });
}
