package com.acme.billing;

import java.util.Map;

public class InvoiceService {
    private final PaymentGatewayClient gateway;

    public InvoiceService(PaymentGatewayClient gateway) {
        this.gateway = gateway;
    }

    /** Build an invoice document for a finished order. */
    public Invoice generateInvoice(Order order) {
        if (order == null) {
            throw new IllegalArgumentException("order is required");
        }
        return new Invoice(order.getId(), order.getTotalCents(), "OPEN");
    }

    /** Called when the payment processor tells us a charge succeeded. */
    public void markPaid(String invoiceId) {
        Invoice invoice = Invoice.load(invoiceId);
        invoice.setStatus("PAID");
        invoice.save();
    }
}
