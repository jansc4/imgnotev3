"""
note_cache.py — jeden cache notatek zamiast dzisiejszych dwóch niezależnych
słowników (_note_cache/_thumb_cache w backend/api.py), backendV4.

Dwie warstwy kosztu, jeden obiekt na notatkę (CachedNote):
- TANIA: title, folder_path, mtime — dostępne bez deszyfrowania pliku,
  bo już są w IndexManager.index.notes (jeden Scrypt na cały indeks przy
  starcie, nie N). Wystarcza galerii/Home do wyrenderowania listy.
- DROGA: text, tags, image_data — dostępne dopiero po pełnym odszyfrowaniu
  JEDNEGO konkretnego pliku (Scrypt + AES-GCM na payloadzie). AES-GCM
  deszyfruje payload atomowo — nie da się dostać samego text bez image_data,
  więc te trzy pola zawsze hydratują się razem, jednym wywołaniem.

Etap 2 audytu (P1, P2, S5):
- pełne obrazy trzymane OSOBNO (`_images`), w LRU z budżetem w bajtach —
  wpis w `_cache` nigdy nie trzyma image_data, więc eksmisja obrazu
  faktycznie zwalnia pamięć, a text/tags/miniatura zostają;
- miniatura generowana leniwie (get_thumbnail), nie przy każdej hydratacji;
  wyjątek: put_full z reperacji, gdzie obraz i tak jest w pamięci;
- RLock wokół dostępu do obu LRU (pywebview woła js_api z wielu wątków),
  deszyfrowanie i generowanie miniatury poza blokadą.
Świeżość sprawdzamy sami, po mtime — cachetools odpowiada tylko za eksmisję.
"""
import threading
from dataclasses import dataclass, replace

from cachetools import LRUCache

from .image_note_file import ImageNoteFile
from .thumbnail import generate_thumbnail

DEFAULT_MAXSIZE = 2000                         # wpisy bez pełnych obrazów (meta+tekst+miniatura)
DEFAULT_IMAGE_BUDGET = 256 * 1024 * 1024       # bajty pełnych obrazów naraz


@dataclass
class CachedNote:
    filepath: str
    mtime: float
    title: str
    folder_path: str
    # Poniżej: warstwa DROGA. None dopóki nikt nie zażądał pełnej treści.
    # image_data wypełniane tylko w kopii zwracanej z get_full — wpis w
    # cache go nie trzyma (patrz NoteCache._images).
    text: str | None = None
    tags: list[str] | None = None
    image_data: bytes | None = None
    thumbnail_data: bytes | None = None
    # True, gdy miniaturę już próbowano wygenerować (None = brak obrazu/nieczytelny)
    thumbnail_done: bool = False
    # czy notatka w ogóle ma obraz (rozróżnia "brak obrazu" od "obraz wyeksmitowany")
    has_image: bool = False

    @property
    def is_fully_hydrated(self) -> bool:
        """True <=> text/tags są wczytane i aktualne (hydratacja wypełnia je
        razem, jednym odczytem AES-GCM)."""
        return self.text is not None


class NoteCache:
    """Cały dostęp do cache idzie przez tę klasę, nikt na zewnątrz nie
    dotyka _cache/_images bezpośrednio (reguła "sprawdź mtime przed
    zwróceniem" nie da się ominąć)."""

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE, image_budget: int = DEFAULT_IMAGE_BUDGET):
        self._cache: LRUCache = LRUCache(maxsize=maxsize)
        # filepath -> (mtime, image_data); rozmiar wpisu = długość bajtów
        self._images: LRUCache = LRUCache(maxsize=image_budget, getsizeof=lambda v: max(1, len(v[1])))
        self._lock = threading.RLock()

    # ---- tania warstwa: title/folder_path/mtime, bez deszyfrowania -----

    def get_metadata(self, filepath: str, title: str, folder_path: str, mtime: float) -> CachedNote:
        """Zero I/O. Wpis z pasującym mtime zwracany bez zmian (z już
        zhydratowaną warstwą); inaczej nowy, pusty CachedNote."""
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
        """Bajty obrazu dla świeżego wpisu; b"" gdy notatka nie ma obrazu,
        None gdy obraz trzeba odszyfrować ponownie (wyeksmitowany)."""
        if not entry.has_image:
            return b""
        hit = self._images.get(filepath)
        if hit is not None and hit[0] == entry.mtime:
            return hit[1]
        return None

    def _store(self, filepath: str, mtime: float, note: ImageNoteFile,
               keep_image: bool, thumbnail: bytes | None, thumbnail_done: bool) -> CachedNote:
        entry = CachedNote(
            filepath=filepath, mtime=mtime, title=note.title, folder_path=note.folder_path,
            text=note.text, tags=note.tags, thumbnail_data=thumbnail,
            thumbnail_done=thumbnail_done, has_image=bool(note.image_data),
        )
        with self._lock:
            self._cache[filepath] = entry
            self._images.pop(filepath, None)
            if keep_image and note.image_data:
                try:
                    self._images[filepath] = (mtime, note.image_data)
                except ValueError:
                    pass  # obraz większy niż cały budżet — nie cache'ujemy
        return entry

    def _load(self, filepath: str, password: str | None, mtime: float) -> tuple[CachedNote, bytes]:
        # deszyfrowanie poza blokadą — to jest ta droga część
        note = ImageNoteFile.load_from_file(filepath, password=password)
        entry = self._store(filepath, mtime, note, keep_image=True, thumbnail=None, thumbnail_done=False)
        return entry, note.image_data

    # ---- droga warstwa: pełna treść, deszyfrowanie na żądanie ----------

    def get_content(self, filepath: str, password: str | None, mtime: float) -> CachedNote:
        """title/folder_path/text/tags bez gwarancji image_data — dla
        wyszukiwania tekstowego: nie deszyfruje ponownie tylko dlatego,
        że obraz wypadł z budżetu."""
        with self._lock:
            entry = self._fresh_entry(filepath, mtime)
        if entry is not None:
            return entry
        return self._load(filepath, password, mtime)[0]

    def get_full(self, filepath: str, password: str | None, mtime: float) -> CachedNote:
        """Kopia wpisu z wypełnionym image_data. `mtime` podawane z zewnątrz,
        żeby wywołujący liczył stat() raz."""
        with self._lock:
            entry = self._fresh_entry(filepath, mtime)
            image = self._cached_image(filepath, entry) if entry is not None else None
        if entry is None or image is None:
            entry, image = self._load(filepath, password, mtime)
        return replace(entry, image_data=image)

    def get_thumbnail(self, filepath: str, password: str | None, mtime: float) -> bytes | None:
        """Leniwa miniatura (P1): generowana raz, przy pierwszym żądaniu."""
        with self._lock:
            entry = self._fresh_entry(filepath, mtime)
            if entry is not None and entry.thumbnail_done:
                return entry.thumbnail_data
        full = self.get_full(filepath, password, mtime)
        thumb = generate_thumbnail(full.image_data)
        with self._lock:
            entry = self._fresh_entry(filepath, mtime)
            if entry is not None:
                entry.thumbnail_data = thumb
                entry.thumbnail_done = True
        return thumb

    def put_full(self, filepath: str, mtime: float, note: ImageNoteFile,
                 keep_image: bool = True, with_thumbnail: bool = False) -> CachedNote:
        """Populacja z JUŻ odszyfrowanego note — zero deszyfrowania.
        Reperacja woła z keep_image=False (P2: nie trzymamy N pełnych obrazów)
        i with_thumbnail=True (obraz i tak jest w pamięci, koszt to tylko resize)."""
        thumb = generate_thumbnail(note.image_data) if with_thumbnail else None
        return self._store(filepath, mtime, note, keep_image, thumb, thumbnail_done=with_thumbnail)

    # ---- inwalidacja -----------------------------------------------------

    def invalidate(self, filepath: str) -> None:
        """Wołane po zapisie/usunięciu notatki."""
        with self._lock:
            self._cache.pop(filepath, None)
            self._images.pop(filepath, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)
