import type { Sql } from "postgres";

type Client = Sql<Record<string, never>>;

export type Overview = {
  customers: string;
  balancesHeld: string;
  ordersDelivered: string;
  revenue: string;
  cost: string;
  profit: string;
  depositsCredited: string;
  usdtCredited: string;
  binanceCredited: string;
  telebirrCredited: string;
  abyssiniaCredited: string;
};

/**
 * The shop-wide numbers, computed in SQL rather than pulled into JS and
 * reduced — unlike the per-page search totals (see orders.ts's own note),
 * this is the WHOLE table, not a bounded search result, so summing it in
 * JS would mean fetching every row just to add them up.
 */
export async function overview(sql: Client): Promise<Overview> {
  const [[money], [balances], [dep]] = await Promise.all([
    sql<{ orders: string; revenue: string; cost: string }[]>`
      SELECT count(*)                                          AS orders,
             coalesce(sum(price_snapshot), 0)                   AS revenue,
             coalesce(sum(zentra_price_snapshot), 0)             AS cost
        FROM orders WHERE status = 'delivered'
    `,
    sql<{ customers: string; held: string }[]>`
      SELECT count(*) AS customers, coalesce(sum(balance_usd), 0) AS held FROM users
    `,
    sql<{ total: string; usdt: string; binance: string; telebirr: string; abyssinia: string }[]>`
      SELECT coalesce(sum(amount_credited), 0)                                       AS total,
             coalesce(sum(amount_credited) FILTER (WHERE method = 'usdt'), 0)         AS usdt,
             coalesce(sum(amount_credited) FILTER (WHERE method = 'binancepay'), 0)   AS binance,
             coalesce(sum(amount_credited) FILTER (WHERE method = 'telebirr'), 0)     AS telebirr,
             coalesce(sum(amount_credited) FILTER (WHERE method = 'abyssinia'), 0)    AS abyssinia
        FROM deposits WHERE status = 'credited'
    `,
  ]);

  const revenue = Number(money?.revenue ?? 0);
  const cost = Number(money?.cost ?? 0);

  return {
    customers: balances?.customers ?? "0",
    balancesHeld: balances?.held ?? "0",
    ordersDelivered: money?.orders ?? "0",
    revenue: money?.revenue ?? "0",
    cost: money?.cost ?? "0",
    profit: (revenue - cost).toFixed(4),
    depositsCredited: dep?.total ?? "0",
    usdtCredited: dep?.usdt ?? "0",
    binanceCredited: dep?.binance ?? "0",
    telebirrCredited: dep?.telebirr ?? "0",
    abyssiniaCredited: dep?.abyssinia ?? "0",
  };
}
