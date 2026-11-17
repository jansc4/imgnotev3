"""
vault_key.py — klucze formatu v4 (FORMAT_V4.md, sekcja "Klucze").

master   = Argon2id(hasło, vault_salt, m, t, p)       — raz na sesję i vault_salt
file_key = HKDF-SHA256(master, file_salt, info)        — mikrosekundy na plik

Jedyny moduł współdzielony przez image_note_file.py i index_file.py w warstwie
kryptograficznej: sam KDF i cache kluczy. Koperty (układ sekcji, AES-GCM)
każdy z formatów ma dalej własną (celowa duplikacja z decyzji KISS).
"""
import os
import threading
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KDF_ARGON2ID = 1
SALT_LEN = 16
KEY_LEN = 32

INFO_NOTE = b"imgnote/v4/note"
INFO_INDEX = b"imgnote/v4/index"

# Limity parametrów czytanych z nagłówka (dane z dysku!) — podłożony plik nie
# może kazać policzyć Argon2id na 64 GB RAM. Dolna granica pamięci = minimum Argon2.
MAX_MEMORY_KIB = 1024 * 1024
MAX_ITERATIONS = 16
MAX_LANES = 16


@dataclass(frozen=True)
class KdfParams:
    memory_kib: int
    iterations: int
    lanes: int

    def validate(self) -> "KdfParams":
        if not (1 <= self.lanes <= MAX_LANES):
            raise ValueError(f"Nieprawidłowe parametry KDF: lanes={self.lanes}.")
        if not (1 <= self.iterations <= MAX_ITERATIONS):
            raise ValueError(f"Nieprawidłowe parametry KDF: iterations={self.iterations}.")
        if not (8 * self.lanes <= self.memory_kib <= MAX_MEMORY_KIB):
            raise ValueError(f"Nieprawidłowe parametry KDF: memory_kib={self.memory_kib}.")
        return self


# Decyzja Jana (2026-11-17): Argon2id, ~400 ms raz na logowanie na jego maszynie.
# Testy podmieniają DEFAULT_PARAMS na szybkie (test/_v4.py).
DEFAULT_PARAMS = KdfParams(memory_kib=256 * 1024, iterations=3, lanes=4)


class KeyRing:
    """Hasło sesji + cache kluczy głównych per (vault_salt, parametry). Jeden na
    sesję (Api), porzucany przy blokadzie. Python nie pozwala nadpisać str/bytes
    w miejscu — wipe() zwalnia referencje, nie gwarantuje wymazania RAM."""

    def __init__(self, password: str):
        if not password:
            raise ValueError("Hasło nie może być puste.")
        self._password = password.encode("utf-8")
        self._masters: dict[tuple, bytes] = {}
        self._lock = threading.Lock()

    def master(self, vault_salt: bytes, params: KdfParams) -> bytes:
        params.validate()
        if len(vault_salt) != SALT_LEN:
            raise ValueError("Nieprawidłowa długość vault_salt.")
        cache_key = (bytes(vault_salt), params)
        with self._lock:  # Argon2id liczony raz, także gdy kilka wątków pyta naraz
            master = self._masters.get(cache_key)
            if master is None:
                master = Argon2id(
                    salt=bytes(vault_salt), length=KEY_LEN, iterations=params.iterations,
                    lanes=params.lanes, memory_cost=params.memory_kib,
                ).derive(self._password)
                self._masters[cache_key] = master
            return master

    def wipe(self) -> None:
        with self._lock:
            self._masters.clear()
            self._password = b""


class VaultKey:
    """KeyRing + vault_salt/parametry KONKRETNEGO vaulta (używane do zapisu).
    Odczyt pliku z innym vault_salt (z nagłówka) też działa — przez ten sam KeyRing."""

    def __init__(self, keyring: KeyRing, vault_salt: bytes, params: KdfParams):
        params.validate()
        if len(vault_salt) != SALT_LEN:
            raise ValueError("Nieprawidłowa długość vault_salt.")
        self.keyring = keyring
        self.vault_salt = bytes(vault_salt)
        self.params = params

    @classmethod
    def new(cls, keyring: KeyRing, params: KdfParams | None = None) -> "VaultKey":
        return cls(keyring, os.urandom(SALT_LEN), params or DEFAULT_PARAMS)

    def file_key(self, file_salt: bytes, info: bytes,
                 vault_salt: bytes | None = None, params: KdfParams | None = None) -> bytes:
        """Klucz pliku. vault_salt/params z nagłówka czytanego pliku; domyślnie własne vaulta."""
        master = self.keyring.master(vault_salt or self.vault_salt, params or self.params)
        return HKDF(algorithm=hashes.SHA256(), length=KEY_LEN, salt=bytes(file_salt), info=info).derive(master)
