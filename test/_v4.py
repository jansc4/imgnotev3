"""Wspólne dla testów formatu v4: szybkie parametry Argon2id (produkcyjne ~400 ms
na każdy nowy vault zamieniłyby testy w minuty) i klucz na hasło testowe.
Import tego modułu PODMIENIA vault_key.DEFAULT_PARAMS — tylko w procesie testu."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backendV4 import vault_key  # noqa: E402
from backendV4.vault_key import KdfParams, KeyRing, VaultKey  # noqa: E402

FAST = KdfParams(memory_kib=1024, iterations=1, lanes=1)
vault_key.DEFAULT_PARAMS = FAST
PASSWORD = "haslo123"


def new_key(password: str = PASSWORD) -> VaultKey:
    """Nowy vault (losowy vault_salt) z szybkimi parametrami."""
    return VaultKey.new(KeyRing(password), FAST)
