"""
index_file.py — format pliku indeksu (.imgidx) dla backendV4.

Osobna koperta kryptograficzna niż `.imgnote` — świadoma duplikacja logiki
nagłówka/Scrypt/AES-GCM względem image_note_file.py, wybrana zamiast
wspólnej abstrakcji, żeby oba formaty były w pełni niezależne i osobno
testowalne (patrz dyskusja w V4_REDESIGN_PLAN.md).

Payload to CACHE POCHODNY, nie źródło prawdy — przy rozjeździe z payloadem
`.imgnote` zawsze wygrywa notatka (Filar 2 w planie).

  notes:     filename -> {"title", "folder_path", "mtime"}
  tag_index: tag -> [filename, ...]        (indeks odwrócony)

`filename` to sama nazwa pliku (np. "803c...ab.imgnote"), NIE pełna
ścieżka — notatki żyją płasko w jednym katalogu, tym samym co plik
indeksu, więc ścieżka bezwzględna byłaby zbędna i złamałaby przenośność
(skopiowanie katalogu gdzie indziej przestałoby działać).
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
    # kopia z image_note_file.py (celowa duplikacja): tmp + fsync + replace
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


class IndexFile:
    MAGIC = b'IMIX'
    VERSION = 1
    HEADER_FORMAT = '<4sBBBBBI'
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
    FILENAME = "_index.imgidx"  # stała nazwa - skanowanie *.imgnote ją pomija

    def __init__(self, notes=None, tag_index=None):
        self.notes = notes or {}
        self.tag_index = tag_index or {}

    # ---- mutacje utrzymujące spójność notes <-> tag_index --------------

    def set_note(self, filename: str, title: str, folder_path: str, mtime: float, tags):
        """Dodaje/aktualizuje wpis notatki i przepisuje jej pozycję w
        tag_index. Najpierw usuwa stare wystąpienia (na wypadek, gdyby
        notatka przy edycji straciła jakiś tag), potem dopisuje aktualne —
        bez tego kroku stary tag zostałby w tag_index na zawsze."""
        self.remove_note(filename)
        self.notes[filename] = {"title": title, "folder_path": folder_path, "mtime": mtime}
        for tag in tags:
            self.tag_index.setdefault(tag, [])
            if filename not in self.tag_index[tag]:
                self.tag_index[tag].append(filename)

    def remove_note(self, filename: str):
        """Usuwa notatkę z `notes` i ze wszystkich list w tag_index — w
        tym pustych list po usunięciu (osierocony klucz taga bez żadnego
        pliku to szum, nie informacja)."""
        self.notes.pop(filename, None)
        empty_tags = []
        for tag, filenames in self.tag_index.items():
            if filename in filenames:
                filenames.remove(filename)
            if not filenames:
                empty_tags.append(tag)
        for tag in empty_tags:
            del self.tag_index[tag]

    # ---- derywacja klucza (identyczna jak w ImageNoteFile) -------------

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
        return kdf.derive(password.encode("utf-8"))

    # ---- (de)serializacja payloadu -------------------------------------

    def _serialize_payload(self) -> bytes:
        return json.dumps({
            "notes": self.notes,
            "tag_index": self.tag_index,
        }).encode("utf-8")

    @classmethod
    def _deserialize_payload(cls, data: bytes):
        j = json.loads(data.decode("utf-8"))
        return j.get("notes", {}), j.get("tag_index", {})

    # ---- zapis (kopia layoutu z ImageNoteFile) -------------------------

    def save_to_file(self, path: str, password: str | None = None):
        payload = self._serialize_payload()

        if password:
            salt = os.urandom(16)
            iv = os.urandom(12)
            key = self._derive_key(password, salt)
            aes = AESGCM(key)
            encrypted_with_tag = aes.encrypt(iv, payload, None)
            ciphertext = encrypted_with_tag[:-16]
            gcm_tag = encrypted_with_tag[-16:]

            flags = 0x01
            header = struct.pack(
                self.HEADER_FORMAT,
                self.MAGIC, self.VERSION, flags,
                len(iv), len(salt), len(gcm_tag), len(ciphertext)
            )
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
                raise ValueError("Nieprawidłowy magic number (to nie jest plik indeksu).")
            if version != cls.VERSION:
                raise ValueError(
                    f"Nieobsługiwana wersja indeksu: plik ma VERSION={version}, "
                    f"ten moduł czyta tylko VERSION={cls.VERSION}."
                )

            is_encrypted = bool(flags & 0x01)

            if is_encrypted:
                if (iv_len, salt_len, tag_len) != (12, 16, 16):
                    raise ValueError("Plik uszkodzony: nieprawidłowe długości w nagłówku.")
                salt = f.read(salt_len)
                iv = f.read(iv_len)
                gcm_tag = f.read(tag_len)
                ciphertext = f.read(enc_len)

                if len(ciphertext) != enc_len:
                    raise ValueError(
                        f"Plik uszkodzony: oczekiwano {enc_len} B danych, "
                        f"odczytano {len(ciphertext)} B."
                    )
                if not password:
                    raise ValueError("Ten indeks wymaga hasła.")

                key = cls()._derive_key(password, salt)
                aes = AESGCM(key)
                try:
                    decrypted = aes.decrypt(iv, ciphertext + gcm_tag, None)
                except InvalidTag:
                    raise WrongPasswordError(
                        "Nieprawidłowe hasło — nie udało się zweryfikować indeksu."
                    ) from None
                notes, tag_index = cls._deserialize_payload(decrypted)
                return cls(notes, tag_index)
            else:
                # ochrona przed podłożeniem jawnego indeksu do zaszyfrowanego vaulta
                if password:
                    raise ValueError("Indeks niezaszyfrowany w zaszyfrowanym vaultcie — odrzucony.")
                payload = f.read(enc_len)
                if len(payload) != enc_len:
                    raise ValueError("Plik uszkodzony: niepełne dane.")
                notes, tag_index = cls._deserialize_payload(payload)
                return cls(notes, tag_index)
