from cryptography.fernet import Fernet

from app.core.config import settings

_fernet = Fernet(settings.encryption_key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()
