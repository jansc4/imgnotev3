"""
index_manager.py — właściciel indeksu (backendV4).

Przy starcie łączy IndexFile (co wiemy), index_reconcile.reconcile() (co się
zmieniło na dysku) i ImageNoteFile (odszyfrowanie zmienionych/nowych plików).

Format v4 (FORMAT_V4.md):
- vault_salt vaulta z nagłówka indeksu; bez indeksu z pierwszej notatki v4;
  w pustym vaulcie nowy (VaultKey.new). Klucz główny liczy KeyRing raz.
- reperacja czyta TYLKO sekcję meta (bez obrazu) — miniatury galeria czyta
  leniwie z ich własnej sekcji;
- złe hasło: zgłaszane dopiero, gdy NIC się nie odszyfrowało (pojedynczy
  uszkodzony plik albo uszkodzony indeks nie blokuje logowania);
- notatka v3 w katalogu = LegacyFormatError (komunikat o migracji), nie pusty vault.

Celowo NIE zapisuje indeksu automatycznie po load_or_rebuild() — zapis to
osobny, jawny krok (save()).
"""
import os
import struct
import sys
import time

from .errors import LegacyFormatError, WrongPasswordError
from .image_note_file import ImageNoteFile, TMP_SUFFIX
from .index_file import IndexFile
from .index_reconcile import reconcile, ReconcileResult
from .vault_key import KeyRing, VaultKey


def _is_orphan_tmp(entry) -> bool:
    # nazwa z _atomic_write: "." + 32 hex + ".tmp"; starsze niż 60 s, by nie ruszyć zapisu w toku
    name = entry.name
    return (entry.is_file() and name.startswith(".") and name.endswith(TMP_SUFFIX)
            and len(name) == 1 + 32 + len(TMP_SUFFIX)
            and time.time() - entry.stat().st_mtime > 60)


class IndexManager:
    def __init__(self, root_folder: str, password: str | None = None, on_progress=None,
                 note_cache=None, keyring: KeyRing | None = None):
        self.root_folder = root_folder
        # KeyRing współdzielony przez Api (jeden Argon2id na sesję i vault_salt);
        # sam password wystarcza w testach i narzędziach
        self.keyring = keyring or KeyRing(password)
        self.key: VaultKey | None = None  # ustawiane w load_or_rebuild()
        self.index = IndexFile()
        # callable(current: int, total: int) | None — postęp reperacji
        self.on_progress = on_progress
        # NoteCache | None — meta odszyfrowane przy reperacji trafiają do niego "za darmo"
        self.note_cache = note_cache
        # True dopiero gdy hasło faktycznie zweryfikowano deszyfrując coś (indeks
        # albo notatkę). False tylko dla pustego vaulta — wtedy front wymaga
        # potwierdzenia hasła drugim polem.
        self.password_verified = False

    # ---- skanowanie dysku ----------------------------------------------

    def _scan_disk(self) -> dict:
        """filename -> mtime dla każdego *.imgnote w root_folder (+ sprzątanie
        osieroconych .tmp po przerwanym zapisie atomowym)."""
        files = {}
        with os.scandir(self.root_folder) as it:
            for entry in it:
                if entry.is_file() and entry.name.endswith(".imgnote"):
                    files[entry.name] = entry.stat().st_mtime
                elif _is_orphan_tmp(entry):
                    try:
                        os.remove(entry.path)
                    except OSError:
                        pass
        return files

    def _discover_key(self, files_on_disk: dict) -> VaultKey:
        """Bez indeksu: vault_salt z pierwszej czytelnej notatki v4, inaczej nowy."""
        for filename in sorted(files_on_disk):
            try:
                vault_salt, params = ImageNoteFile.peek_vault(os.path.join(self.root_folder, filename))
            except LegacyFormatError:
                raise
            except (ValueError, struct.error, OSError):
                continue
            return VaultKey(self.keyring, vault_salt, params)
        return VaultKey.new(self.keyring)

    # ---- start appki / reperacja ---------------------------------------

    def load_or_rebuild(self) -> ReconcileResult:
        index_path = os.path.join(self.root_folder, IndexFile.FILENAME)
        index_loaded = False
        index_suspect = False  # indeks jest, ale się nie odszyfrował (złe hasło albo uszkodzony)
        if os.path.exists(index_path):
            try:
                _, vault_salt, params, *_ = IndexFile.read_header(index_path)
                key = VaultKey(self.keyring, vault_salt, params)
                self.index = IndexFile.load_from_file(index_path, key)
                self.key = key
                self.password_verified = True
                index_loaded = True
            except WrongPasswordError:
                index_suspect = True
            except (ValueError, struct.error, OSError) as e:
                # uszkodzony/stary indeks to tylko cache -> pełna odbudowa
                print(f"[IndexManager] indeks odrzucony ({e}), odbudowa", file=sys.stderr)
        if not index_loaded:
            self.index = IndexFile()

        files_on_disk = self._scan_disk()
        if self.key is None:
            self.key = self._discover_key(files_on_disk)
        result = reconcile(self.index.notes, files_on_disk)

        for filename in result.orphaned:
            self.index.remove_note(filename)

        unreadable = 0
        to_process = (*result.changed, *result.new)
        total = len(to_process)
        for i, filename in enumerate(to_process, start=1):
            path = os.path.join(self.root_folder, filename)
            try:
                meta = ImageNoteFile.load_meta(path, self.key)
            except LegacyFormatError:
                raise
            except WrongPasswordError:
                unreadable += 1
                self.index.remove_note(filename)
                continue
            except (ValueError, struct.error, OSError) as e:
                print(f"[IndexManager] pominięto {filename}: {e}", file=sys.stderr)
                self.index.remove_note(filename)
                continue
            self.password_verified = True
            mtime = files_on_disk[filename]
            self.index.set_note(filename, meta.title, meta.folder_path, mtime, meta.tags)
            if self.note_cache is not None:
                self.note_cache.put_meta(path, mtime, meta)
            if self.on_progress:
                self.on_progress(i, total)

        if not self.password_verified and (index_suspect or unreadable):
            raise WrongPasswordError("Nieprawidłowe hasło — nie udało się odszyfrować żadnego pliku.")
        if unreadable:
            print(f"[IndexManager] {unreadable} notatek nieczytelnych tym hasłem (pominięte)", file=sys.stderr)
        return result

    def save(self) -> None:
        index_path = os.path.join(self.root_folder, IndexFile.FILENAME)
        self.index.save_to_file(index_path, self.key)

    # ---- utrzymanie indeksu w trakcie działania appki -------------------

    def note_saved(self, path: str, note: ImageNoteFile) -> None:
        """Wołane PO zapisaniu notatki (save_to_file albo rewrite_meta). Obiekt
        `note` już jest w pamięci — bez ponownego odszyfrowywania pliku."""
        filename = os.path.basename(path)
        mtime = os.path.getmtime(path)
        self.index.set_note(filename, note.title, note.folder_path, mtime, note.tags)
        if self.note_cache is not None:
            if note.meta_only:
                self.note_cache.put_meta(path, mtime, note)
            else:
                self.note_cache.put_full(path, mtime, note)

    def note_deleted(self, path: str) -> None:
        filename = os.path.basename(path)
        self.index.remove_note(filename)
        if self.note_cache is not None:
            self.note_cache.invalidate(path)
