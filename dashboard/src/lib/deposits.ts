import type { Sql } from "postgres";

type Client = Sql<Record<string, never>>;

export type DepositRow = {
  id: string;
  method: string;
  status: string;
  amount_expected: string;
  amount_credited: string;
  tx_hash: string | null;
  reference: string | null;
  suffix: string | null;
  note: string | null;
  created_at: string;
  expires_at: string;
  credited_at: string | null;
  username: string | null;
  telegram_id: string;
};

/**
 * Every top-up request, on every rail, in one list — this is where "did
 * my customer's payment actually arrive" gets answered without opening a
 * database console. Telebirr/Abyssinia requests show a `reference` before
 * they show a `tx_hash` — see deposits.reference's own column comment:
 * it is set the moment a customer submits a receipt, well before (or
 * instead of) the deposit ever resolving.
 */
export async function searchDeposits(sql: Client, q = "", method = ""): Promise<DepositRow[]> {
  const term = q.trim().replace(/^@/, "");
  const digits = /^\d+$/.test(term);

  const match = term
    ? sql`AND (u.username ILIKE ${`%${term}%`}
               OR d.tx_hash ILIKE ${`%${term}%`}
               OR d.reference ILIKE ${`%${term}%`}
               OR (${digits} AND (u.telegram_id::text = ${term} OR d.id::text = ${term})))`
    : sql``;
  const methodFilter = method ? sql`AND d.method = ${method}` : sql``;

  return sql<DepositRow[]>`
    SELECT d.id, d.method, d.status, d.amount_expected, d.amount_credited,
           d.tx_hash, d.reference, d.suffix, d.note,
           d.created_at, d.expires_at, d.credited_at,
           u.username, u.telegram_id
      FROM deposits d
      JOIN users u ON u.id = d.user_id
     WHERE TRUE ${match} ${methodFilter}
     ORDER BY d.created_at DESC
     LIMIT 200
  `;
}
