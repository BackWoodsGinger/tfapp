"""
Server-side encryption for message bodies at rest (Fernet symmetric).

This protects database dumps and backups from reading plaintext. It is NOT
end-to-end encryption: anyone with the Django SECRET_KEY (or derived key) and
app code can decrypt. True E2E would require client-side crypto and key exchange.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    raw = getattr(settings, "SECRET_KEY", "") or "dev-insecure"
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_message_body(plaintext: str) -> str:
    if plaintext is None:
        plaintext = ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_message_body(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return "[Unable to decrypt message]"
