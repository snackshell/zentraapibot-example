"use client";

import { useActionState } from "react";
import { approve, reject, type ResolveFormState } from "./actions";
import type { PendingLocalPayment } from "@/lib/localpayments";

const initialState: ResolveFormState = null;

export default function ReviewRow({ payment }: { payment: PendingLocalPayment }) {
  const [approveState, approveAction, approving] = useActionState(approve, initialState);
  const [rejectState, rejectAction, rejecting] = useActionState(reject, initialState);
  const state = approveState ?? rejectState;

  return (
    <tr>
      <td className="mono">#{payment.id}</td>
      <td>{payment.username ? `@${payment.username}` : <span className="mono">{payment.telegram_id}</span>}</td>
      <td>{payment.method === "telebirr" ? "Telebirr" : "Bank of Abyssinia"}</td>
      <td className="mono">
        {payment.reference ?? <span style={{ color: "var(--text-dim)" }}>not submitted</span>}
        {payment.suffix ? <span className="mono" style={{ color: "var(--text-dim)" }}> · {payment.suffix}</span> : null}
      </td>
      <td className="mono">{Number(payment.amount_expected).toLocaleString()} ETB requested</td>
      <td className="mono">{new Date(payment.created_at).toLocaleString()}</td>
      <td>
        {state && (
          <div className={`notice ${state.ok ? "notice-ok" : "notice-error"}`} style={{ marginBottom: 6 }}>
            {state.message}
          </div>
        )}
        <form action={approveAction} className="inline" style={{ marginBottom: 6 }}>
          <input type="hidden" name="depositId" value={payment.id} />
          <input type="text" name="amountUsd" placeholder="USDT received" inputMode="decimal" required style={{ width: 110 }} />
          <input type="text" name="note" placeholder="Note (optional)" style={{ width: 140 }} />
          <button type="submit" disabled={approving || !payment.reference}>
            {approving ? "…" : "Credit"}
          </button>
        </form>
        <form action={rejectAction} className="inline">
          <input type="hidden" name="depositId" value={payment.id} />
          <input type="text" name="note" placeholder="Reason for rejecting" required style={{ width: 200 }} />
          <button type="submit" className="secondary" disabled={rejecting}>
            {rejecting ? "…" : "Reject"}
          </button>
        </form>
      </td>
    </tr>
  );
}
