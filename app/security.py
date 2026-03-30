from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 390_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), ITERATIONS)
    encoded_digest = base64.b64encode(digest).decode("utf-8")
    return f"{ALGORITHM}${ITERATIONS}${salt}${encoded_digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, raw_iterations, salt, stored_digest = password_hash.split("$", 3)
    except ValueError:
        return False

    if algorithm != ALGORITHM:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        int(raw_iterations),
    )
    candidate = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(candidate, stored_digest)
