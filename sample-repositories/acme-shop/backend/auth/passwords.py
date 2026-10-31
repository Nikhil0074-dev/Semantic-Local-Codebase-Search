import hashlib
import os
import hmac


def hash_password(plain: str, salt: bytes | None = None) -> str:
    """Derive a salted PBKDF2 digest so plain-text passwords are never stored."""
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, 200_000)
    return salt.hex() + "$" + digest.hex()


def check_password(plain: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$")
    candidate = hashlib.pbkdf2_hmac("sha256", plain.encode(), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(candidate.hex(), digest_hex)
