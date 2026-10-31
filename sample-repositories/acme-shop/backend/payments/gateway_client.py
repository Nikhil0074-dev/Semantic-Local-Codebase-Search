"""Client for the external card processor (Stripe-style REST API)."""
import json
import time
import urllib.request
import urllib.error

GATEWAY_BASE_URL = "https://api.payments.example.com/v1"


class GatewayTimeout(Exception):
    pass


class PaymentGatewayClient:
    def __init__(self, api_key: str, max_attempts: int = 3):
        self.api_key = api_key
        self.max_attempts = max_attempts

    def _post(self, path: str, payload: dict) -> dict:
        """POST with exponential backoff when the processor times out or returns 5xx."""
        data = json.dumps(payload).encode()
        for attempt in range(1, self.max_attempts + 1):
            req = urllib.request.Request(
                GATEWAY_BASE_URL + path, data=data, method="POST",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read())
            except (urllib.error.URLError, TimeoutError):
                if attempt == self.max_attempts:
                    raise GatewayTimeout(f"payment processor unreachable after {attempt} attempts")
                time.sleep(2 ** attempt * 0.1)

    def charge_card(self, amount_cents: int, currency: str, card_token: str, order_id: int) -> dict:
        """Create a charge for an order."""
        return self._post("/charges", {"amount": amount_cents, "currency": currency,
                                       "source": card_token, "metadata": {"order": order_id}})

    def refund(self, charge_id: str, amount_cents: int | None = None) -> dict:
        return self._post(f"/charges/{charge_id}/refund", {"amount": amount_cents})
