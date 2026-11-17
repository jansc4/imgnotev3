"""
image_note_file.py — format pliku .imgnote v4 (backendV4). Specyfikacja: FORMAT_V4.md.

Względem v3:
- klucz pliku = HKDF(master, file_salt), master = Argon2id liczony raz na sesję
  (vault_key.py) — zamiast Scrypt na każdy plik;
- trzy osobno szyfrowane sekcje: meta (JSON), miniatura (WebP), obraz (binarnie,
  bez hex) — galeria/indeks/wyszukiwanie czytają tylko to, czego potrzebują;
- AAD każdej sekcji = 48-bajtowy prefiks nagłówka + id sekcji;
- rewrite_meta(): nowa sekcja meta, zaszyfrowana miniatura i obraz kopiowane bajt w bajt;
- szyfrowanie obowiązkowe (tryb bez hasła z v3 usunięty).
Pliki v3 czyta wyłącznie test/migrate_v3_to_v4.py.
"""
import json
import os
import struct
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import LegacyFormatError, WrongPasswordError
from .thumbnail import generate_thumbnail
from .vault_key import INFO_NOTE, KDF_ARGON2ID, SALT_LEN, KdfParams, VaultKey

TMP_SUFFIX = ".tmp"

SEC_META, SEC_THUMB, SEC_IMAGE = 1, 2, 3
_SECTIONS = (SEC_META, SEC_THUMB, SEC_IMAGE)
NONCE_LEN = 12
TAG_LEN = 16


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


class _Header:
    """Sparsowany nagłówek v4: prefiks (AAD) + tablica sekcji."""
    PREFIX_FORMAT = "<4sBBBBII16s16s"   # magic, version, flags, kdf, lanes, mem_kib, iters, vault_salt, file_salt
    PREFIX_SIZE = struct.calcsize(PREFIX_FORMAT)  # 48
    ENTRY_FORMAT = "<12sI"              # nonce, długość szyfrogramu (z tagiem)
    ENTRY_SIZE = struct.calcsize(ENTRY_FORMAT)    # 16

    def __init__(self, prefix: bytes, vault_salt: bytes, params: KdfParams, file_salt: bytes, entries):
        self.prefix = prefix
        self.vault_salt = vault_salt
        self.params = params
        self.file_salt = file_salt
        self.entries = entries  # [(nonce, length)] w kolejności _SECTIONS

    def offset(self, section_id: int) -> int:
        idx = _SECTIONS.index(section_id)
        return self.size + sum(length for _, length in self.entries[:idx])

    @property
    def size(self) -> int:
        return self.PREFIX_SIZE + self.ENTRY_SIZE * len(self.entries)


def _read_header(f, magic: bytes, version: int, n_sections: int, legacy_error: type) -> _Header:
    """Parsuje i waliduje nagłówek; parametry KDF ograniczane PRZED jakimkolwiek liczeniem."""
    prefix = f.read(_Header.PREFIX_SIZE)
    if len(prefix) < 5:
        raise ValueError("Plik uszkodzony: za krótki nagłówek.")
    if prefix[:4] != magic:
        raise ValueError("Nieprawidłowy magic number.")
    if prefix[4] < version:
        raise legacy_error(
            f"Plik w starym formacie (v{prefix[4]}) — uruchom test/migrate_v3_to_v4.py."
        )
    if prefix[4] != version:
        raise ValueError(f"Nieobsługiwana wersja formatu: v{prefix[4]}.")
    if len(prefix) != _Header.PREFIX_SIZE:
        raise ValueError("Plik uszkodzony: za krótki nagłówek.")
    _, _, _flags, kdf, lanes, mem, iters, vault_salt, file_salt = struct.unpack(_Header.PREFIX_FORMAT, prefix)
    if kdf != KDF_ARGON2ID:
        raise ValueError(f"Nieobsługiwany KDF: {kdf}.")
    params = KdfParams(memory_kib=mem, iterations=iters, lanes=lanes).validate()
    table = f.read(_Header.ENTRY_SIZE * n_sections)
    if len(table) != _Header.ENTRY_SIZE * n_sections:
        raise ValueError("Plik uszkodzony: niepełna tablica sekcji.")
    entries = [struct.unpack_from(_Header.ENTRY_FORMAT, table, i * _Header.ENTRY_SIZE) for i in range(n_sections)]
    if any(length < TAG_LEN for _, length in entries):
        raise ValueError("Plik uszkodzony: nieprawidłowa długość sekcji.")
    header = _Header(prefix, vault_salt, params, file_salt, entries)
    expected = header.size + sum(length for _, length in entries)
    if os.fstat(f.fileno()).st_size != expected:
        raise ValueError("Plik uszkodzony: rozmiar nie zgadza się z nagłówkiem.")
    return header


class ImageNoteFile:
    MAGIC = b"IMNG"
    VERSION = 4

    def __init__(
        self,
        text: str = "",
        image_data: bytes = b"",
        tags=None,
        title: str = "",
        folder_path: str = "",
        thumbnail_data: bytes | None = None,
    ):
        self.text = text
        self.image_data = image_data or b""
        self.tags = list(tags or [])
        self.title = title
        self.folder_path = self._normalize_folder_path(folder_path)
        self.thumbnail_data = thumbnail_data
        # load_meta(): obraz nie jest wczytany, znamy tylko jego rozmiar z nagłówka
        self.image_size = len(self.image_data)
        self.meta_only = False

    # ---- generowanie nazwy pliku ------------------------------------

    @staticmethod
    def new_filename() -> str:
        """Losowa nazwa fizycznego pliku — nie niesie żadnej informacji (tytuł
        żyje wyłącznie w zaszyfrowanej sekcji meta)."""
        return f"{uuid.uuid4().hex}.imgnote"

    # ---- normalizacja folder_path ------------------------------------

    @staticmethod
    def _normalize_folder_path(folder_path: str) -> str:
        """Wirtualna ścieżka zawsze w postaci 'A/B/C' — bez wiodącego i
        końcowego '/', separatory ujednolicone do '/'."""
        if not folder_path:
            return ""
        normalized = folder_path.replace("\\", "/").strip("/")
        # S8: bez segmentów "." i ".." — przy eksporcie folder_path staje się prawdziwą ścieżką
        parts = [p for p in normalized.split("/") if p and p not in (".", "..")]
        return "/".join(parts)

    # ---- sekcje ---------------------------------------------------------

    def _meta_bytes(self) -> bytes:
        return json.dumps({
            "title": self.title,
            "folder_path": self.folder_path,
            "text": self.text,
            "tags": self.tags,
        }).encode("utf-8")

    @staticmethod
    def _seal(fkey: bytes, prefix: bytes, section_id: int, plaintext: bytes) -> tuple[bytes, bytes]:
        nonce = os.urandom(NONCE_LEN)
        ct = AESGCM(fkey).encrypt(nonce, plaintext, prefix + bytes([section_id]))
        if len(ct) >= 2**32:
            raise ValueError("Sekcja za duża (limit 4 GB).")
        return nonce, ct

    @staticmethod
    def _open_section(f, header: _Header, fkey: bytes, section_id: int) -> bytes:
        nonce, length = header.entries[_SECTIONS.index(section_id)]
        f.seek(header.offset(section_id))
        ct = f.read(length)
        if len(ct) != length:
            raise ValueError("Plik uszkodzony: niepełna sekcja.")
        try:
            return AESGCM(fkey).decrypt(nonce, ct, header.prefix + bytes([section_id]))
        except InvalidTag:
            if section_id == SEC_META:
                # meta jest odszyfrowywana zawsze pierwsza — to ona weryfikuje hasło
                raise WrongPasswordError("Nieprawidłowe hasło albo uszkodzona notatka.") from None
            raise ValueError("Plik uszkodzony: sekcja nie przeszła weryfikacji.") from None

    @classmethod
    def _header_and_key(cls, f, key: VaultKey) -> tuple[_Header, bytes]:
        header = _read_header(f, cls.MAGIC, cls.VERSION, len(_SECTIONS), LegacyFormatError)
        fkey = key.file_key(header.file_salt, INFO_NOTE, header.vault_salt, header.params)
        return header, fkey

    @classmethod
    def _from_meta(cls, meta_plain: bytes) -> "ImageNoteFile":
        j = json.loads(meta_plain.decode("utf-8"))
        return cls(text=j.get("text", ""), tags=j.get("tags", []), title=j.get("title", ""),
                   folder_path=j.get("folder_path", ""))

    # ---- zapis -----------------------------------------------------------

    def save_to_file(self, path: str, key: VaultKey) -> None:
        """Pełny zapis (nowy file_salt). Miniatura generowana tu, jeśli jej nie podano."""
        if not isinstance(key, VaultKey):
            raise TypeError("save_to_file wymaga VaultKey (format v4 jest zawsze szyfrowany).")
        if self.thumbnail_data is None and self.image_data:
            self.thumbnail_data = generate_thumbnail(self.image_data)
        file_salt = os.urandom(SALT_LEN)
        p = key.params
        prefix = struct.pack(_Header.PREFIX_FORMAT, self.MAGIC, self.VERSION, 0, KDF_ARGON2ID,
                             p.lanes, p.memory_kib, p.iterations, key.vault_salt, file_salt)
        fkey = key.file_key(file_salt, INFO_NOTE)
        plains = (self._meta_bytes(), self.thumbnail_data or b"", self.image_data)
        table, cts = b"", []
        for section_id, plain in zip(_SECTIONS, plains):
            nonce, ct = self._seal(fkey, prefix, section_id, plain)
            table += struct.pack(_Header.ENTRY_FORMAT, nonce, len(ct))
            cts.append(ct)
        _atomic_write(path, (prefix, table, *cts))
        self.image_size = len(self.image_data)

    @classmethod
    def rewrite_meta(cls, path: str, key: VaultKey, *, title: str, folder_path: str,
                     tags, text: str) -> "ImageNoteFile":
        """Nowa sekcja meta; zaszyfrowana miniatura i obraz kopiowane bez odszyfrowania.
        Zwraca obiekt meta_only (bez image_data) z nowymi wartościami."""
        with open(path, "rb") as f:
            header, fkey = cls._header_and_key(f, key)
            cls._open_section(f, header, fkey, SEC_META)  # weryfikacja klucza przed nadpisaniem
            raw = {}
            for section_id in (SEC_THUMB, SEC_IMAGE):
                f.seek(header.offset(section_id))
                raw[section_id] = f.read(header.entries[_SECTIONS.index(section_id)][1])
        note = cls(text=text, tags=tags, title=title, folder_path=folder_path)
        nonce, meta_ct = cls._seal(fkey, header.prefix, SEC_META, note._meta_bytes())
        table = struct.pack(_Header.ENTRY_FORMAT, nonce, len(meta_ct))
        for section_id in (SEC_THUMB, SEC_IMAGE):
            table += struct.pack(_Header.ENTRY_FORMAT, *header.entries[_SECTIONS.index(section_id)])
        _atomic_write(path, (header.prefix, table, meta_ct, raw[SEC_THUMB], raw[SEC_IMAGE]))
        note.image_size = header.entries[_SECTIONS.index(SEC_IMAGE)][1] - TAG_LEN
        note.meta_only = True
        return note

    # ---- odczyt ---------------------------------------------------------

    @classmethod
    def load_from_file(cls, path: str, key: VaultKey) -> "ImageNoteFile":
        """Pełna notatka: meta + miniatura + obraz."""
        with open(path, "rb") as f:
            header, fkey = cls._header_and_key(f, key)
            note = cls._from_meta(cls._open_section(f, header, fkey, SEC_META))
            note.thumbnail_data = cls._open_section(f, header, fkey, SEC_THUMB) or None
            note.image_data = cls._open_section(f, header, fkey, SEC_IMAGE)
            note.image_size = len(note.image_data)
        return note

    @classmethod
    def load_meta(cls, path: str, key: VaultKey) -> "ImageNoteFile":
        """Tylko title/folder_path/text/tags (indeks, wyszukiwanie) + rozmiar obrazu."""
        with open(path, "rb") as f:
            header, fkey = cls._header_and_key(f, key)
            note = cls._from_meta(cls._open_section(f, header, fkey, SEC_META))
        note.image_size = header.entries[_SECTIONS.index(SEC_IMAGE)][1] - TAG_LEN
        note.meta_only = True
        return note

    @classmethod
    def load_thumbnail(cls, path: str, key: VaultKey) -> bytes | None:
        """Sama sekcja miniatury (galeria) — bez odszyfrowania obrazu."""
        with open(path, "rb") as f:
            header, fkey = cls._header_and_key(f, key)
            return cls._open_section(f, header, fkey, SEC_THUMB) or None

    @classmethod
    def peek_vault(cls, path: str) -> tuple[bytes, KdfParams]:
        """vault_salt i parametry KDF z nagłówka — bez klucza (odkrycie vaulta bez indeksu)."""
        with open(path, "rb") as f:
            header = _read_header(f, cls.MAGIC, cls.VERSION, len(_SECTIONS), LegacyFormatError)
        return header.vault_salt, header.params
