package notifications

import (
	"fmt"
	"net/smtp"
)

// SendOrderConfirmation emails the customer a receipt once an order is placed.
func SendOrderConfirmation(smtpHost, to string, orderID int) error {
	msg := []byte(fmt.Sprintf("Subject: Your order #%d\r\n\r\nThanks for shopping with Acme.", orderID))
	return smtp.SendMail(smtpHost+":25", nil, "orders@acme.example", []string{to}, msg)
}
