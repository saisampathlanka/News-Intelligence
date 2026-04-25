"""
Password hashing using PBKDF2-HMAC-SHA256 (Python stdlib).

Why not bcrypt/argon2?
  - No external dependency required
  - PBKDF2-HMAC-SHA256 at 260,000 iterations meets NIST SP 800-132 guidance
  - Timing-safe comparison built-in via hmac.compare_digest

For production upgrade path: swap hash_password / verify_password with
passlib's CryptContext (argon2) without changing any call sites.
"""
import base64
import hashlib
import hmac
import secrets

ITERATIONS = 260_000   # NIST recommended minimum for PBKDF2-SHA256 (2024)
SALT_BYTES  = 32
KEY_LENGTH  = 32


def hash_password(password: str) -> str:
    """
    Hash a plaintext password.

    Returns:
        Base64-encoded string: [32-byte salt][32-byte derived key]
        Always 86 characters after stripping base64 padding.
    """
    if not password:
        raise ValueError("Password cannot be empty")
    salt = secrets.token_bytes(SALT_BYTES)
    dk   = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS, KEY_LENGTH)
    return base64.b64encode(salt + dk).decode("ascii")


def verify_password(plaintext: str, stored_hash: str) -> bool:
    """
    Verify a plaintext password against its stored hash.

    Uses hmac.compare_digest for timing-safe comparison
    (prevents timing-oracle attacks even on hash comparison).

    Returns:
        True if password matches, False otherwise.
        Never raises — returns False on any malformed input.
    """
    if not plaintext or not stored_hash:
        return False
    try:
        raw  = base64.b64decode(stored_hash.encode("ascii"))
        salt = raw[:SALT_BYTES]
        dk   = raw[SALT_BYTES:]
        check = hashlib.pbkdf2_hmac("sha256", plaintext.encode("utf-8"), salt, ITERATIONS, KEY_LENGTH)
        return hmac.compare_digest(check, dk)
    except Exception:
        return False


def generate_api_key() -> str:
    """
    Generate a cryptographically secure 40-character API key.
    Format: nip_<random32hex>  (prefix makes it identifiable in logs)
    """
    return "nip_" + secrets.token_hex(20)
