"""Session credential lifecycle for the Acme Shop API."""
import time
import hmac
import hashlib
import base64
import json

ACCESS_TTL_SECONDS = 900
REFRESH_TTL_SECONDS = 60 * 60 * 24 * 30


class TokenExpiredError(Exception):
    """Raised when a presented credential is past its expiry time."""


class TokenInvalidError(Exception):
    """Raised when a credential signature or structure cannot be trusted."""


class TokenStore:
    """Keeps long-lived renewal credentials so they can be revoked server-side."""

    def __init__(self, connection):
        self.connection = connection

    def save(self, user_id, renewal_token, expires_at):
        self.connection.execute(
            "INSERT INTO refresh_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
            (user_id, renewal_token, expires_at),
        )

    def revoke(self, renewal_token):
        self.connection.execute("DELETE FROM refresh_tokens WHERE token = ?", (renewal_token,))

    def lookup(self, renewal_token):
        return self.connection.execute(
            "SELECT user_id, expires_at FROM refresh_tokens WHERE token = ?", (renewal_token,)
        ).fetchone()


def _sign(payload: dict, secret: bytes) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def issue_access_token(user_id: int, secret: bytes) -> str:
    """Create a short-lived bearer credential for API calls."""
    now = int(time.time())
    return _sign({"sub": user_id, "iat": now, "exp": now + ACCESS_TTL_SECONDS}, secret)


def verify_token(token: str, secret: bytes) -> dict:
    """Check the signature and reject credentials whose expiry has passed."""
    try:
        body, sig = token.split(".")
    except ValueError as exc:
        raise TokenInvalidError("malformed credential") from exc
    expected = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise TokenInvalidError("bad signature")
    payload = json.loads(base64.urlsafe_b64decode(body.encode()))
    if payload["exp"] < time.time():
        raise TokenExpiredError("access credential has lapsed")
    return payload


def renew_credentials(renewal_token: str, store: TokenStore, secret: bytes) -> str:
    """Exchange a valid long-lived renewal credential for a brand new access credential.

    The old renewal credential is rotated so it can only be used once.
    """
    record = store.lookup(renewal_token)
    if record is None:
        raise TokenInvalidError("unknown renewal credential")
    user_id, expires_at = record
    if expires_at < time.time():
        store.revoke(renewal_token)
        raise TokenExpiredError("renewal credential has lapsed; user must sign in again")
    store.revoke(renewal_token)
    new_renewal = _sign({"sub": user_id, "kind": "renewal", "iat": int(time.time())}, secret)
    store.save(user_id, new_renewal, time.time() + REFRESH_TTL_SECONDS)
    return issue_access_token(user_id, secret)
