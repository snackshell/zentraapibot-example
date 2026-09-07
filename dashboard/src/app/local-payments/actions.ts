"use server";

import { revalidatePath } from "next/cache";
import { requireAdmin } from "@/lib/auth";
import { sql, withRetry } from "@/lib/db";
import { approveLocalPayment, rejectLocalPayment } from "@/lib/localpayments";

export type ResolveFormState = { ok: boolean; message: string; depositId: string } | null;

export async function approve(
  _prev: ResolveFormState,
  formData: FormData,
): Promise<ResolveFormState> {
  await requireAdmin();

  const depositId = Number(formData.get("depositId"));
  const amountUsd = String(formData.get("amountUsd") ?? "");
  const note = String(formData.get("note") ?? "");

  const result = await withRetry(() => approveLocalPayment(sql, { depositId, amountUsd, note }));
  revalidatePath("/local-payments");
  return {
    ok: result.ok,
    depositId: String(depositId),
    message: result.ok ? `Credited — new balance $${Number(result.newBalance).toFixed(2)}.` : result.message,
  };
}

export async function reject(
  _prev: ResolveFormState,
  formData: FormData,
): Promise<ResolveFormState> {
  await requireAdmin();

  const depositId = Number(formData.get("depositId"));
  const note = String(formData.get("note") ?? "");

  const result = await withRetry(() => rejectLocalPayment(sql, { depositId, note }));
  revalidatePath("/local-payments");
  return { ok: result.ok, depositId: String(depositId), message: result.message };
}
