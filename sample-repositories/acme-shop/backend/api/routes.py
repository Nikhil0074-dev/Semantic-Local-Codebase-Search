"""HTTP handlers (framework-agnostic sketch)."""
from backend.auth.permissions import can_update_record, PermissionDenied
from backend.auth.token_service import renew_credentials, TokenExpiredError, TokenInvalidError
from backend.logging_utils import log_api_error
from backend.models.order_repository import find_order, update_order_record


def refresh_endpoint(request, store, secret):
    """POST /api/auth/refresh - trade a renewal credential for a new access credential."""
    try:
        return 200, {"access_token": renew_credentials(request["renewal_token"], store, secret)}
    except (TokenExpiredError, TokenInvalidError) as exc:
        log_api_error("/api/auth/refresh", 401, exc)
        return 401, {"error": "sign in again"}


def update_order(request, user, conn):
    """PUT /api/orders/<id> - modify an order if the caller is allowed to."""
    order = find_order(request["id"], conn)
    if order is None:
        return 404, {"error": "no such order"}
    if not can_update_record(user, dict(order)):
        return 403, {"error": "forbidden"}
    try:
        update_order_record(request["id"], request["fields"])
    except Exception as exc:
        log_api_error(f"/api/orders/{request['id']}", 500, exc)
        return 500, {"error": "update failed"}
    return 200, {"ok": True}
