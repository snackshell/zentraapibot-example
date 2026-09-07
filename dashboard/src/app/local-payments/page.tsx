import Shell from "@/components/Shell";
import { requireAdmin } from "@/lib/auth";
import { sql, withRetry } from "@/lib/db";
import { pendingLocalPayments } from "@/lib/localpayments";
import ReviewRow from "./ReviewRow";

export const dynamic = "force-dynamic";
export const maxDuration = 30; // keep in sync with lib/retry.ts MAX_DURATION

export default async function LocalPaymentsPage() {
  await requireAdmin();
  const pending = await withRetry(() => pendingLocalPayments(sql));

  return (
    <Shell>
      <div className="page-head">
        <div>
          <h1>Local Payments</h1>
          <p>
            Telebirr and Bank of Abyssinia requests waiting on a person — either
            because automatic verification is off, or because it looked at the
            receipt and could not confirm it on its own.
          </p>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>{pending.length} waiting for review</h2>
        </div>
        <div className="table-scroll">
          {pending.length === 0 ? (
            <div className="empty">Nothing waiting — every request has settled or been resolved.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Request</th>
                  <th>Customer</th>
                  <th>Rail</th>
                  <th>Reference</th>
                  <th>Requested</th>
                  <th>Opened</th>
                  <th>Resolve</th>
                </tr>
              </thead>
              <tbody>
                {pending.map((payment) => (
                  <ReviewRow key={payment.id} payment={payment} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </Shell>
  );
}
