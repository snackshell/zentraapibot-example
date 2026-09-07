import type { Sql } from "postgres";

/**
 * Telebirr and Bank of Abyssinia — the manual review queue every request
 * on these rails passes through, whether or not automatic verification
 * ever gets to it first.
 *
 * THE SAME GUARD AS EVERY OTHER RAIL. approveLocalPayment() reaches
 * bot/db.py's own credit_deposit() logic — the conditional UPDATE that
 * makes crediting a deposit twice, from any two callers, a no-op rather
 * than a double credit. This is not a parallel implementation of that
 * guard; it is the identical WHERE-clause shape, written once here because
 * this app speaks SQL directly rather than importing Python.
 */

type Client = Sql<Record<string, never>>;

export type PendingLocalPayment = {
  id: string;
  method: string;
  amount_expected: string;
  reference: string | null;
  suffix: string | null;
  created_at: string;
  username: string | null;
  telegram_id: string;
};

export async function pendingLocalPayments(sql: Client): Promise<PendingLocalPayment[]> {
  return sql<PendingLocalPayment[]>`
    SELECT d.id, d.method, d.amount_expected, d.reference, d.suffix, d.created_at,
           u.username, u.telegram_id
      FROM deposits d
      JOIN users u ON u.id = d.user_id
     WHERE d.method IN ('telebirr', 'abyssinia')
       AND d.status IN ('awaiting', 'expired')
     ORDER BY d.created_at ASC
  `;
}

export type ResolveResult =
  | { ok: true; newBalance: string }
  | { ok: false; message: string };

export async function approveLocalPayment(
  sql: Client,
  { depositId, amountUsd, note }: { depositId: number; amountUsd: string; note: string },
): Promise<ResolveResult> {
  const [deposit] = await sql<{ id: string; reference: string | null; user_id: string }[]>`
    SELECT id, reference, user_id FROM deposits WHERE id = ${depositId}
  `;
  if (!deposit) return { ok: false, message: "No such request." };
  if (!deposit.reference) {
    return { ok: false, message: "This request has no reference yet — nothing to credit against." };
  }

  const parsed = Number(amountUsd);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return { ok: false, message: "Enter the amount actually received, greater than zero." };
  }

  return sql.begin(async (tx) => {
    // THE GUARD: resolves at most once, and tx_hash's own UNIQUE index
    // (shared with every other rail) refuses the same reference crediting
    // a SECOND deposit even if two pending rows somehow share one by
    // mistake — see bot/db.py's credit_deposit() for the identical logic
    // this mirrors.
    const credited = await tx<{ id: string; user_id: string; amount_credited: string }[]>`
      UPDATE deposits
         SET status = 'credited', tx_hash = ${deposit.reference}, note = ${note || null},
             amount_credited = ${amountUsd}::numeric, credited_at = now()
       WHERE id = ${depositId} AND status IN ('awaiting', 'expired') AND tx_hash IS NULL
      RETURNING id, user_id, amount_credited
    `;
    if (credited.length === 0) {
      return { ok: false, message: "That request was already resolved by someone else." };
    }
    const row = credited[0];

    const updated = await tx<{ balance_usd: string }[]>`
      UPDATE users SET balance_usd = balance_usd + ${row.amount_credited}::numeric
       WHERE id = ${row.user_id}
      RETURNING balance_usd
    `;
    await tx`
      INSERT INTO wallet_txns (user_id, amount_usd, kind, ref)
      VALUES (${row.user_id}, ${row.amount_credited}::numeric, 'topup', ${`deposit:${row.id}`})
    `;

    return { ok: true, newBalance: updated[0].balance_usd };
  });
}

export async function rejectLocalPayment(
  sql: Client,
  { depositId, note }: { depositId: number; note: string },
): Promise<{ ok: boolean; message: string }> {
  if (!note.trim()) {
    return { ok: false, message: "A reason is required, so a customer asking why can be told." };
  }
  const [rejected] = await sql<{ id: string }[]>`
    UPDATE deposits SET status = 'rejected', note = ${note}
     WHERE id = ${depositId} AND status IN ('awaiting', 'expired') AND tx_hash IS NULL
    RETURNING id
  `;
  if (!rejected) {
    return { ok: false, message: "That request was already resolved by someone else." };
  }
  return { ok: true, message: "Rejected." };
}
