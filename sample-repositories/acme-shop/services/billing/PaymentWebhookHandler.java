package com.acme.billing;

public class PaymentWebhookHandler {
    private final InvoiceService invoices;

    public PaymentWebhookHandler(InvoiceService invoices) {
        this.invoices = invoices;
    }

    /** Receives callbacks sent by the payment gateway and updates the matching invoice. */
    public void handleGatewayEvent(String eventType, java.util.Map<String, String> data) {
        if ("charge.succeeded".equals(eventType)) {
            invoices.markPaid(data.get("invoice_id"));
        }
    }
}
