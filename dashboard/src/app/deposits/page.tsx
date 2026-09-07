import Shell from "@/components/Shell";
import { requireAdmin } from "@/lib/auth";
import { sql, withRetry } from "@/lib/db";
import { searchDeposits } from "@/lib/deposits";

export const dynamic = "force-dynamic";
export const maxDuration = 30; // keep in sync with lib/retry.ts MAX_DURATION

const METHOD_LABEL: Record<string, string> = {
  usdt: "USDT (BEP-20)",
  binancepay: "Binance Pay",
  telebirr: "Telebirr",
  abyssinia: "Bank of Abyssinia",
};

export default async function DepositsPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; method?: string }>;
}) {
  await requireAdmin();
  const { q = "", method = "" } = await searchParams;
  const rows = await withRetry(() => searchDeposits(sql, q, method));

  return (
    <Shell>
      <div className="page-head">
        <div>
          <h1>Deposits</h1>
          <p>{rows.length} shown across both rails{q ? ` — matching "${q}"` : ""}.</p>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <form className="inline" method="get">
            <input type="text" name="q" defaultValue={q} placeholder="Username, Telegram id, or tx hash" />
            <select name="method" defaultValue={method}>
              <option value="">Every rail</option>
              <option value="usdt">USDT (BEP-20)</option>
              <option value="binancepay">Binance Pay</option>
              <option value="telebirr">Telebirr</option>
              <option value="abyssinia">Bank of Abyssinia</option>
            </select>
            <button type="submit">Search</button>
          </form>
        </div>
        <div className="table-scroll">
          {rows.length === 0 ? (
            <div className="empty">No deposits match that search.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Deposit</th>
                  <th>Customer</th>
                  <th>Rail</th>
                  <th>Expected</th>
                  <th>Credited</th>
                  <th>Status</th>
                  <th>Reference</th>
                  <th>Requested</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td className="mono">#{row.id}</td>
                    <td>{row.username ? `@${row.username}` : <span className="mono">{row.telegram_id}</span>}</td>
                    <td>{METHOD_LABEL[row.method] ?? row.method}</td>
                    <td className="mono">{Number(row.amount_expected).toFixed(4)}</td>
                    <td className="mono">
                      {row.status === "credited" ? Number(row.amount_credited).toFixed(4) : "—"}
                    </td>
                    <td><span className={`pill pill-${row.status}`}>{row.status}</span></td>
                    <td className="mono">
                      {row.tx_hash
                        ? `${row.tx_hash.slice(0, 10)}…${row.tx_hash.slice(-6)}`
                        : row.reference
                          ? <span title="Submitted, not yet resolved — see Local Payments">{row.reference}</span>
                          : "—"}
                    </td>
                    <td className="mono">{new Date(row.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </Shell>
  );
}
