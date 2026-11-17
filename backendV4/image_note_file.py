"""
image_note_file.py — format pliku .imgnote v3 (backendV4).

Zmiany względem v2 (backend/image_note_file.py):
- payload zyskuje pola `title` i `folder_path` — tytuł notatki i jej
  wirtualna ścieżka folderu przenoszą się do środka szyfrowanego payloadu.
  Nazwa fizycznego pliku na dysku przestaje cokolwiek znaczyć (patrz
  `new_filename()` niżej).
- VERSION: 2 -> 3.
- ten moduł NIE czyta formatu v2 — konwersja starych plików to osobny,
  jednorazowy skrypt migracyjny (używa starej klasy do odczytu, tej do
  zapisu), nie część tego modułu.

Warstwa kryptograficzna (nagłówek binarny, Scrypt, AES-GCM) bez zmian
względem v2 — zmienia się tylko zawartość zaszyfrowanego payloadu.
"""
import os
import struct
import json
import uuid
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import WrongPasswordError

TMP_SUFFIX = ".tmp"


def _atomic_write(path: str, chunks) -> None:
    # zapis do pliku tymczasowego + fsync + replace: crash nie zostawia uciętej notatki
    directory = os.path.dirname(os.path.abspath(path))
    tmp = os.path.join(directory, f".{uuid.uuid4().hex}{TMP_SUFFIX}")
    try:
        with open(tmp, "wb") as f:
            for chunk in chunks:
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


class ImageNoteFile:
    MAGIC = b'IMNG'
    VERSION = 3
    HEADER_FORMAT = '<4sBBBBBI'  # magic, version, flags, iv_len, salt_len, tag_len, enc_len
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

    def __init__(
        self,
        text: str = "",
        image_data: bytes = b"",
        tags=None,
        title: str = "",
        folder_path: str = "",
        encrypted: bool = False,
    ):
        self.text = text
        self.image_data = image_data
        self.tags = tags or []
        self.title = title
        self.folder_path = self._normalize_folder_path(folder_path)
        self.encrypted = encrypted

    # ---- generowanie nazwy pliku ------------------------------------

    @staticmethod
    def new_filename() -> str:
        """Losowa nazwa fizycznego pliku na dysku. W v2 nazwa pliku BYŁA
        tytułem notatki (wyciek treści widoczny w dowolnym eksploratorze
        plików bez znajomości hasła). W v3 nazwa nie niesie już żadnej
        informacji — tytuł żyje wyłącznie w zaszyfrowanym payloadzie."""
        return f"{uuid.uuid4().hex}.imgnote"

    # ---- normalizacja folder_path ------------------------------------

    @staticmethod
    def _normalize_folder_path(folder_path: str) -> str:
        """Wirtualna ścieżka zawsze w postaci 'A/B/C' — bez wiodącego i
        końcowego '/', z separatorami ujednoliconymi do '/' (w tym z '\\'
        na wypadek Windows). To pole nigdy nie trafia na dysk jako
        prawdziwa ścieżka, więc nie obowiązują go zasady systemu plików —
        ale musi być spójne, żeby dwa zapisy tej samej notatki nie
        wygenerowały dwóch różnych stringów dla tego samego folderu."""
        if not folder_path:
            return ""
        normalized = folder_path.replace("\\", "/").strip("/")
        # S8: bez segmentów "." i ".." — przy eksporcie folder_path staje się prawdziwą ścieżką
        parts = [p for p in normalized.split("/") if p and p not in (".", "..")]
        return "/".join(parts)

    # ---- derywacja klucza ------------------------------------------

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
        return kdf.derive(password.encode("utf-8"))

    # ---- (de)serializacja payloadu -----------------------------------

    def _serialize_payload(self) -> bytes:
        return json.dumps({
            "title": self.title,
            "folder_path": self.folder_path,
            "text": self.text,
            "tags": self.tags,
            "image": self.image_data.hex(),
        }).encode("utf-8")

    @classmethod
    def _deserialize_payload(cls, data: bytes):
        j = json.loads(data.decode("utf-8"))
        return (
            j.get("title", ""),
            j.get("folder_path", ""),
            j["text"],
            bytes.fromhex(j["image"]),
            j["tags"],
        )

    # ---- zapis --------------------------------------------------------

    def save_to_file(self, path: str, password: str | None = None):
        payload = self._serialize_payload()

        if password:
            salt = os.urandom(16)
            iv = os.urandom(12)
            key = self._derive_key(password, salt)
            aes = AESGCM(key)
            # aes.encrypt() zwraca: zaszyfrowane_dane + 16B_tag (łącznie)
            encrypted_with_tag = aes.encrypt(iv, payload, None)
            ciphertext = encrypted_with_tag[:-16]   # dane bez taga
            gcm_tag = encrypted_with_tag[-16:]      # 16B tag

            flags = 0x01
            # enc_len = długość samego ciphertextu (BEZ taga)
            header = struct.pack(
                self.HEADER_FORMAT,
                self.MAGIC, self.VERSION, flags,
                len(iv), len(salt), len(gcm_tag), len(ciphertext)
            )
            # układ: header | salt 16B | iv 12B | tag 16B | ciphertext
            _atomic_write(path, (header, salt, iv, gcm_tag, ciphertext))
        else:
            flags = 0x00
            header = struct.pack(
                self.HEADER_FORMAT,
                self.MAGIC, self.VERSION, flags, 0, 0, 0, len(payload)
            )
            _atomic_write(path, (header, payload))

    # ---- odczyt ---------------------------------------------------------

    @classmethod
    def load_from_file(cls, path: str, password: str | None = None):
        with open(path, "rb") as f:
            header_data = f.read(cls.HEADER_SIZE)
            if len(header_data) != cls.HEADER_SIZE:
                raise ValueError("Plik uszkodzony: za krótki nagłówek.")
            (magic, version, flags,
             iv_len, salt_len, tag_len, enc_len) = struct.unpack(cls.HEADER_FORMAT, header_data)

            if magic != cls.MAGIC:
                raise ValueError("Nieprawidłowy magic number.")
            if version != cls.VERSION:
                raise ValueError(
                    f"Nieobsługiwana wersja formatu: plik ma VERSION={version}, "
                    f"ten moduł czyta tylko VERSION={cls.VERSION}. "
                    f"Stare pliki wymagają skryptu migracyjnego."
                )

            is_encrypted = bool(flags & 0x01)

            if is_encrypted:
                if (iv_len, salt_len, tag_len) != (12, 16, 16):
                    raise ValueError("Plik uszkodzony: nieprawidłowe długości w nagłówku.")
                salt = f.read(salt_len)         # 16 B
                iv = f.read(iv_len)              # 12 B
                gcm_tag = f.read(tag_len)        # 16 B
                ciphertext = f.read(enc_len)     # N B

                if len(ciphertext) != enc_len:
                    raise ValueError(
                        f"Plik uszkodzony: oczekiwano {enc_len} B danych, "
                        f"odczytano {len(ciphertext)} B."
                    )
                if not password:
                    raise ValueError("Ten plik wymaga hasła.")

                key = cls()._derive_key(password, salt)
                aes = AESGCM(key)
                # decrypt wymaga: ciphertext + tag (w tej kolejności)
                try:
                    decrypted = aes.decrypt(iv, ciphertext + gcm_tag, None)
                except InvalidTag:
                    raise WrongPasswordError(
                        "Nieprawidłowe hasło — nie udało się zweryfikować pliku."
                    ) from None
                title, folder_path, text, image_data, tags = cls._deserialize_payload(decrypted)
                return cls(text, image_data, tags, title, folder_path, encrypted=True)
            else:
                # ochrona przed podłożeniem jawnego pliku do zaszyfrowanego vaulta
                if password:
                    raise ValueError("Plik niezaszyfrowany w zaszyfrowanym vaultcie — odrzucony.")
                payload = f.read(enc_len)
                if len(payload) != enc_len:
                    raise ValueError("Plik uszkodzony: niepełne dane.")
                title, folder_path, text, image_data, tags = cls._deserialize_payload(payload)
                return cls(text, image_data, tags, title, folder_path, encrypted=False)
