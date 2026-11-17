"""
note_cache.py — cache notatek w pamięci (backendV4, format v4).

Trzy niezależne LRU, każde dla innej sekcji pliku v4 (FORMAT_V4.md):
- `_cache`  : filepath -> CachedNote (title, folder_path, text, tags, has_image) — sekcja meta;
- `_thumbs` : filepath -> (mtime, miniatura WebP | None) — sekcja miniatury, małe;
- `_images` : filepath -> (mtime, bajty obrazu) — sekcja obrazu, budżet w bajtach (P2).
Każdą sekcję da się odszyfrować osobno, więc galeria nie dotyka obrazów,
a wyszukiwanie nie dotyka ani obrazów, ani miniatur.

Świeżość po mtime sprawdzamy sami; cachetools odpowiada tylko za eksmisję.
RLock wokół LRU (pywebview woła js_api z wielu wątków), deszyfrowanie poza blokadą.
"""
import threading
from dataclasses import dataclass, replace

from cachetools import LRUCache

from .image_note_file import ImageNoteFile
from .vault_key import VaultKey

DEFAULT_MAXSIZE = 2000                         # wpisy meta (tekst + tagi)
DEFAULT_THUMB_MAXSIZE = 5000                   # miniatury WebP, kilka-kilkanaście KB każda
DEFAULT_IMAGE_BUDGET = 256 * 1024 * 1024       # bajty pełnych obrazów naraz


@dataclass
class CachedNote:
    filepath: str
    mtime: float
    title: str
    folder_path: str
    text: str | None = None
    tags: list[str] | None = None
    # wypełniane tylko w kopii zwracanej z get_full — wpis w cache go nie trzyma
    image_data: bytes | None = None
    has_image: bool = False

    @property
    def is_fully_hydrated(self) -> bool:
        """True <=> sekcja meta (text/tags) wczytana i aktualna."""
        return self.text is not None


class NoteCache:
    def __init__(self, maxsize: int = DEFAULT_MAXSIZE, image_budget: int = DEFAULT_IMAGE_BUDGET,
                 thumb_maxsize: int = DEFAULT_THUMB_MAXSIZE):
        self._cache: LRUCache = LRUCache(maxsize=maxsize)
        self._thumbs: LRUCache = LRUCache(maxsize=thumb_maxsize)
        self._images: LRUCache = LRUCache(maxsize=image_budget, getsizeof=lambda v: max(1, len(v[1])))
        self._lock = threading.RLock()

    # ---- tania warstwa: title/folder_path/mtime, bez deszyfrowania -----

    def get_metadata(self, filepath: str, title: str, folder_path: str, mtime: float) -> CachedNote:
        """Zero I/O. Wpis z pasującym mtime zwracany bez zmian; inaczej nowy, pusty."""
        with self._lock:
            cached = self._cache.get(filepath)
            if cached is not None and cached.mtime == mtime:
                return cached
            fresh = CachedNote(filepath=filepath, mtime=mtime, title=title, folder_path=folder_path)
            self._cache[filepath] = fresh
            self._images.pop(filepath, None)
            return fresh

    # ---- wewnętrzne ------------------------------------------------------

    def _fresh_entry(self, filepath: str, mtime: float) -> CachedNote | None:
        cached = self._cache.get(filepath)
        if cached is not None and cached.mtime == mtime and cached.is_fully_hydrated:
            return cached
        return None

    def _cached_image(self, filepath: str, entry: CachedNote) -> bytes | None:
        """b"" gdy notatka nie ma obrazu, None gdy obraz trzeba odszyfrować (wyeksmitowany)."""
        if not entry.has_image:
            return b""
        hit = self._images.get(filepath)
        if hit is not None and hit[0] == entry.mtime:
            return hit[1]
        return None

    def _store(self, filepath: str, mtime: float, note: ImageNoteFile, keep_image: bool) -> CachedNote:
        entry = CachedNote(
            filepath=filepath, mtime=mtime, title=note.title, folder_path=note.folder_path,
            text=note.text, tags=note.tags, has_image=note.image_size > 0,
        )
        with self._lock:
            self._cache[filepath] = entry
            self._images.pop(filepath, None)
            if keep_image and note.image_data:
                try:
                    self._images[filepath] = (mtime, note.image_data)
                except ValueError:
                    pass  # obraz większy niż cały budżet — nie cache'ujemy
            if not note.meta_only:
                self._thumbs[filepath] = (mtime, note.thumbnail_data)
            else:
                hit = self._thumbs.get(filepath)
                if hit is not None and hit[0] != mtime:
                    self._thumbs.pop(filepath, None)
        return entry

    # ---- droga warstwa: odszyfrowanie konkretnych sekcji na żądanie -----

    def get_content(self, filepath: str, key: VaultKey, mtime: float) -> CachedNote:
        """Sama sekcja meta (wyszukiwanie tekstowe, operacje na tagach/folderach)."""
        with self._lock:
            entry = self._fresh_entry(filepath, mtime)
        if entry is not None:
            return entry
        note = ImageNoteFile.load_meta(filepath, key)
        return self._store(filepath, mtime, note, keep_image=False)

    def get_full(self, filepath: str, key: VaultKey, mtime: float) -> CachedNote:
        """Kopia wpisu z wypełnionym image_data (podgląd, eksport)."""
        with self._lock:
            entry = self._fresh_entry(filepath, mtime)
            image = self._cached_image(filepath, entry) if entry is not None else None
        if entry is not None and image is not None:
            return replace(entry, image_data=image)
        note = ImageNoteFile.load_from_file(filepath, key)
        entry = self._store(filepath, mtime, note, keep_image=True)
        return replace(entry, image_data=note.image_data)

    def get_thumbnail(self, filepath: str, key: VaultKey, mtime: float) -> bytes | None:
        """Sekcja miniatury — bez odszyfrowania obrazu i bez meta."""
        with self._lock:
            hit = self._thumbs.get(filepath)
            if hit is not None and hit[0] == mtime:
                return hit[1]
        thumb = ImageNoteFile.load_thumbnail(filepath, key)
        with self._lock:
            self._thumbs[filepath] = (mtime, thumb)
        return thumb

    def put_full(self, filepath: str, mtime: float, note: ImageNoteFile) -> CachedNote:
        """Populacja z pełnego, już odszyfrowanego/zapisanego note — zero deszyfrowania."""
        return self._store(filepath, mtime, note, keep_image=True)

    def put_meta(self, filepath: str, mtime: float, note: ImageNoteFile) -> CachedNote:
        """Populacja z note meta_only (reperacja indeksu, rewrite_meta)."""
        return self._store(filepath, mtime, note, keep_image=False)

    # ---- inwalidacja -----------------------------------------------------

    def invalidate(self, filepath: str) -> None:
        with self._lock:
            self._cache.pop(filepath, None)
            self._images.pop(filepath, None)
            self._thumbs.pop(filepath, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)
