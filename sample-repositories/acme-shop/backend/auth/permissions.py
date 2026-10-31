"""Authorization rules: who may do what to which record."""
from functools import wraps

ROLE_ACTIONS = {
    "admin": {"read", "update", "delete", "refund"},
    "support": {"read", "update"},
    "customer": {"read"},
}


class PermissionDenied(Exception):
    pass


def require_permission(user, action, resource=None):
    """Raise PermissionDenied unless the user's role allows the action."""
    allowed = ROLE_ACTIONS.get(user.get("role"), set())
    if action not in allowed:
        raise PermissionDenied(f"role {user.get('role')!r} may not {action}")
    if resource is not None and resource.get("owner_id") not in (user["id"], None) and user["role"] == "customer":
        raise PermissionDenied("customers can only touch their own records")


def can_update_record(user, record) -> bool:
    """Check that the caller is allowed to modify this record before any write happens."""
    try:
        require_permission(user, "update", record)
    except PermissionDenied:
        return False
    return not record.get("locked", False)


def requires_role(*roles):
    """Decorator for handlers that only certain roles may invoke."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(user, *args, **kwargs):
            if user.get("role") not in roles:
                raise PermissionDenied("insufficient role")
            return fn(user, *args, **kwargs)
        return wrapper
    return decorator
