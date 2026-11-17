"""
index_file.py — format pliku indeksu (.imgidx) v2 dla backendV4. Spec: FORMAT_V4.md.

Osobna koperta niż `.imgnote` (celowa duplikacja nagłówka/AES-GCM względem
image_note_file.py — decyzja KISS); wspólny jest tylko KDF (vault_key.py).
Ten sam 48-bajtowy prefiks (magic IMIX, version 2), jedna sekcja.

Payload to CACHE POCHODNY, nie źródło prawdy — przy rozjeździe zawsze wygrywa notatka.

  notes:     filename -> {"title", "folder_path", "mtime", "tags"}   (zapisywane)
  tag_index: tag -> set(filename)   (P3: tylko w pamięci, odtwarzany z notes przy wczytaniu)

`filename` to sama nazwa pliku (np. "803c...ab.imgnote"), nie pełna ścieżka.
"""
import json
import os
import struct
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import WrongPasswordError
from .vault_key import INFO_INDEX, KDF_ARGON2ID, SALT_LEN, KdfParams, VaultKey

TMP_SUFFIX = ".tmp"
NONCE_LEN = 12
TAG_LEN = 16
SEC_PAYLOAD = 1


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
    VERSION = 2
    PREFIX_FORMAT = "<4sBBBBII16s16s"  # jak w image_note_file.py (48 B)
    PREFIX_SIZE = struct.calcsize(PREFIX_FORMAT)
    ENTRY_FORMAT = "<12sI"
    ENTRY_SIZE = struct.calcsize(ENTRY_FORMAT)
    FILENAME = "_index.imgidx"  # stała nazwa - skanowanie *.imgnote ją pomija

    def __init__(self, notes=None):
        self.notes: dict[str, dict] = {}
        self.tag_index: dict[str, set[str]] = {}
        for filename, entry in (notes or {}).items():
            self.set_note(filename, entry.get("title", ""), entry.get("folder_path", ""),
                          entry.get("mtime", 0.0), entry.get("tags", []))
        # vault_salt/parametry z nagłówka wczytanego pliku (None dla nowego indeksu)
        self.vault_salt: bytes | None = None
        self.params: KdfParams | None = None

    # ---- mutacje utrzymujące spójność notes <-> tag_index --------------

    def set_note(self, filename: str, title: str, folder_path: str, mtime: float, tags):
        """Dodaje/aktualizuje wpis i jego pozycję w tag_index (usuwa stare tagi notatki)."""
        self.remove_note(filename)
        tags = list(dict.fromkeys(tags or []))  # bez duplikatów, kolejność zachowana
        self.notes[filename] = {"title": title, "folder_path": folder_path, "mtime": mtime, "tags": tags}
        for tag in tags:
            self.tag_index.setdefault(tag, set()).add(filename)

    def remove_note(self, filename: str):
        """P3: dotyka tylko tagów tej notatki (odwrotna mapa w notes[filename]["tags"]),
        nie przegląda całego tag_index. Puste klucze tagów usuwane."""
        entry = self.notes.pop(filename, None)
        if not entry:
            return
        for tag in entry.get("tags", []):
            filenames = self.tag_index.get(tag)
            if filenames is None:
                continue
            filenames.discard(filename)
            if not filenames:
                del self.tag_index[tag]

    # ---- zapis ------------------------------------------------------------

    def save_to_file(self, path: str, key: VaultKey) -> None:
        if not isinstance(key, VaultKey):
            raise TypeError("save_to_file wymaga VaultKey (format v4 jest zawsze szyfrowany).")
        payload = json.dumps({"notes": self.notes}).encode("utf-8")
        file_salt = os.urandom(SALT_LEN)
        p = key.params
        prefix = struct.pack(self.PREFIX_FORMAT, self.MAGIC, self.VERSION, 0, KDF_ARGON2ID,
                             p.lanes, p.memory_kib, p.iterations, key.vault_salt, file_salt)
        fkey = key.file_key(file_salt, INFO_INDEX)
        nonce = os.urandom(NONCE_LEN)
        ct = AESGCM(fkey).encrypt(nonce, payload, prefix + bytes([SEC_PAYLOAD]))
        _atomic_write(path, (prefix, struct.pack(self.ENTRY_FORMAT, nonce, len(ct)), ct))
        self.vault_salt, self.params = key.vault_salt, key.params

    # ---- odczyt -----------------------------------------------------------

    @classmethod
    def read_header(cls, path: str):
        """(prefix, vault_salt, params, file_salt, nonce, length) z walidacją — bez klucza."""
        with open(path, "rb") as f:
            prefix = f.read(cls.PREFIX_SIZE)
            if len(prefix) < 5 or prefix[:4] != cls.MAGIC:
                raise ValueError("Nieprawidłowy magic number (to nie jest plik indeksu).")
            if prefix[4] != cls.VERSION:
                # stary indeks (v1) to tylko cache — wywołujący go odbuduje
                raise ValueError(f"Nieobsługiwana wersja indeksu: v{prefix[4]}.")
            if len(prefix) != cls.PREFIX_SIZE:
                raise ValueError("Plik uszkodzony: za krótki nagłówek.")
            _, _, _flags, kdf, lanes, mem, iters, vault_salt, file_salt = struct.unpack(cls.PREFIX_FORMAT, prefix)
            if kdf != KDF_ARGON2ID:
                raise ValueError(f"Nieobsługiwany KDF: {kdf}.")
            params = KdfParams(memory_kib=mem, iterations=iters, lanes=lanes).validate()
            entry = f.read(cls.ENTRY_SIZE)
            if len(entry) != cls.ENTRY_SIZE:
                raise ValueError("Plik uszkodzony: niepełna tablica sekcji.")
            nonce, length = struct.unpack(cls.ENTRY_FORMAT, entry)
            if length < TAG_LEN or os.fstat(f.fileno()).st_size != cls.PREFIX_SIZE + cls.ENTRY_SIZE + length:
                raise ValueError("Plik uszkodzony: rozmiar nie zgadza się z nagłówkiem.")
            ct = f.read(length)
        return prefix, vault_salt, params, file_salt, nonce, ct

    @classmethod
    def load_from_file(cls, path: str, key: VaultKey) -> "IndexFile":
        """key: VaultKey z dowolnym KeyRing — vault_salt/params brane z nagłówka indeksu."""
        prefix, vault_salt, params, file_salt, nonce, ct = cls.read_header(path)
        fkey = key.file_key(file_salt, INFO_INDEX, vault_salt, params)
        try:
            payload = AESGCM(fkey).decrypt(nonce, ct, prefix + bytes([SEC_PAYLOAD]))
        except InvalidTag:
            raise WrongPasswordError("Nieprawidłowe hasło albo uszkodzony indeks.") from None
        index = cls(json.loads(payload.decode("utf-8")).get("notes", {}))
        index.vault_salt, index.params = vault_salt, params
        return index
