import Shell from "@/components/Shell";
import { requireAdmin } from "@/lib/auth";
import { sql, withRetry } from "@/lib/db";
import { overview } from "@/lib/overview";

export const dynamic = "force-dynamic";
export const maxDuration = 30; // keep in sync with lib/retry.ts MAX_DURATION

function usd(value: string): string {
  return `$${Number(value).toFixed(2)}`;
}

export default async function OverviewPage() {
  await requireAdmin();
  const data = await withRetry(() => overview(sql));

  return (
    <Shell>
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p>Everything at a glance, across every rail.</p>
        </div>
      </div>

      <div className="cards">
        <div className="card">
          <div className="label">Customers</div>
          <div className="value mono">{data.customers}</div>
        </div>
        <div className="card">
          <div className="label">Balances held</div>
          <div className="value mono">{usd(data.balancesHeld)}</div>
        </div>
        <div className="card">
          <div className="label">Orders delivered</div>
          <div className="value mono">{data.ordersDelivered}</div>
        </div>
        <div className="card">
          <div className="label">Revenue</div>
          <div className="value mono">{usd(data.revenue)}</div>
        </div>
        <div className="card">
          <div className="label">Cost (paid to Zentra)</div>
          <div className="value mono">{usd(data.cost)}</div>
        </div>
        <div className="card">
          <div className="label">Profit</div>
          <div className="value mono">{usd(data.profit)}</div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>Deposits credited</h2>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Rail</th>
                <th>Total credited</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>USDT (BEP-20)</td>
                <td className="mono">{usd(data.usdtCredited)}</td>
              </tr>
              <tr>
                <td>Binance Pay</td>
                <td className="mono">{usd(data.binanceCredited)}</td>
              </tr>
              <tr>
                <td>Telebirr</td>
                <td className="mono">{usd(data.telebirrCredited)}</td>
              </tr>
              <tr>
                <td>Bank of Abyssinia</td>
                <td className="mono">{usd(data.abyssiniaCredited)}</td>
              </tr>
              <tr>
                <td><strong>All rails</strong></td>
                <td className="mono"><strong>{usd(data.depositsCredited)}</strong></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </Shell>
  );
}
