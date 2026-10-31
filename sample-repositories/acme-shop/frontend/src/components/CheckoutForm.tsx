import { useState } from "react";
import { apiFetch } from "../api/httpClient";

interface Props { orderId: number; totalCents: number; }

export function CheckoutForm({ orderId, totalCents }: Props) {
  const [status, setStatus] = useState<"idle" | "paying" | "failed" | "paid">("idle");

  async function submitPayment(cardToken: string) {
    setStatus("paying");
    const res = await apiFetch(`/orders/${orderId}/pay`, {
      method: "POST",
      body: JSON.stringify({ cardToken, amount: totalCents }),
    });
    setStatus(res.ok ? "paid" : "failed");
  }

  return (
    <form onSubmit={(e) => { e.preventDefault(); void submitPayment("tok_demo"); }}>
      <button disabled={status === "paying"}>Pay {(totalCents / 100).toFixed(2)}</button>
      {status === "failed" && <p role="alert">Payment was declined. Please try another card.</p>}
    </form>
  );
}
