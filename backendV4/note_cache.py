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

CachedNote zaczyna z tanią warstwą wypełnioną, drogą pustą (None) — to jest
"częściowa hydratacja" z decyzji w planie. Kontener trzymający te obiekty to
cachetools.LRUCache: gdy wpisów zrobi się więcej niż maxsize, najdawniej
nieużywany znika automatycznie — cachetools NIE wie nic o mtime/plikach,
odpowiada wyłącznie za "za dużo wpisów, który wyrzucić". Świeżość (czy
zhydratowane dane wciąż pasują do pliku na dysku) sprawdzamy sami, po mtime,
dokładnie jak dziś w api.py.
"""
from dataclasses import dataclass

from cachetools import LRUCache

from .image_note_file import ImageNoteFile
from .thumbnail import generate_thumbnail

DEFAULT_MAXSIZE = 1000  # ile w pełni zhydratowanych notatek trzymać naraz


@dataclass
class CachedNote:
    filepath: str
    mtime: float
    title: str
    folder_path: str
    # Poniżej: warstwa DROGA. None dopóki nikt nie zażądał pełnej treści
    # tej konkretnej notatki (get_full jeszcze nie było wołane, albo plik
    # się zmienił od ostatniej hydratacji).
    text: str | None = None
    tags: list[str] | None = None
    image_data: bytes | None = None
    thumbnail_data: bytes | None = None

    @property
    def is_fully_hydrated(self) -> bool:
        """True <=> ktoś już zażądał pełnej treści i wciąż jest aktualna.
        Sprawdzamy po `text`, bo hydratacja wypełnia text/tags/image_data
        w jednym rzucie (jeden odczyt AES-GCM) — jeśli text jest ustawiony,
        pozostałe dwa też są."""
        return self.text is not None


class NoteCache:
    """Wrapper na cachetools.LRUCache — cały dostęp do cache idzie przez
    tę klasę, nikt na zewnątrz nie dotyka self._cache bezpośrednio (żeby
    reguła "sprawdź mtime przed zwróceniem" nie dała się ominąć przez
    przypadkowy bezpośredni odczyt słownika)."""

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE):
        self._cache: LRUCache = LRUCache(maxsize=maxsize)

    # ---- tania warstwa: title/folder_path/mtime, bez deszyfrowania -----

    def get_metadata(self, filepath: str, title: str, folder_path: str, mtime: float) -> CachedNote:
        """Wołane z danymi już dostępnymi z IndexManager.index.notes — zero
        I/O tutaj. Jeśli wpis już jest w cache i mtime się zgadza, ZWRACA
        GO BEZ ZMIAN (zachowuje już-zhydratowaną drogą warstwę, jeśli była).
        Jeśli mtime się różni albo wpisu nie było — nowy, pusty CachedNote
        (stare text/tags/image_data są nieaktualne, więc odrzucone)."""
        cached = self._cache.get(filepath)
        if cached is not None and cached.mtime == mtime:
            return cached
        fresh = CachedNote(filepath=filepath, mtime=mtime, title=title, folder_path=folder_path)
        self._cache[filepath] = fresh
        return fresh

    # ---- droga warstwa: pełna treść, deszyfrowanie na żądanie ----------

    def _hydrate_from_note(self, filepath: str, mtime: float, note: ImageNoteFile) -> CachedNote:
        """Wspólny rdzeń get_full/put_full: buduje CachedNote z gotowego,
        już odszyfrowanego ImageNoteFile i wstawia do cache. Sama w sobie
        nie deszyfruje niczego — o to musi zadbać wywołujący. Miniatura
        generowana TU, nie osobno — to jest ten sam punkt, gdzie pełne
        bajty obrazka już są w pamięci (darmowo, dla changed/new podczas
        reperacji), więc koszt to tylko resize, nie dodatkowa deszyfracja."""
        hydrated = CachedNote(
            filepath=filepath,
            mtime=mtime,
            title=note.title,
            folder_path=note.folder_path,
            text=note.text,
            tags=note.tags,
            image_data=note.image_data,
            thumbnail_data=generate_thumbnail(note.image_data),
        )
        self._cache[filepath] = hydrated
        return hydrated

    def get_full(self, filepath: str, password: str | None, mtime: float) -> CachedNote:
        """`mtime` podawane z zewnątrz (nie liczone tu przez os.path.getmtime),
        żeby wywołujący mógł go policzyć raz i podzielić między get_metadata
        i get_full bez podwójnego stat()-a na ten sam plik — drobiazg, ale
        to dokładnie ten rodzaj powielonej pracy, który cache ma eliminować."""
        cached = self._cache.get(filepath)
        if cached is not None and cached.mtime == mtime and cached.is_fully_hydrated:
            return cached
        note = ImageNoteFile.load_from_file(filepath, password=password)
        return self._hydrate_from_note(filepath, mtime, note)

    def put_full(self, filepath: str, mtime: float, note: ImageNoteFile) -> CachedNote:
        """Populacja z JUŻ odszyfrowanego note — zero deszyfrowania tutaj.
        Do użytku przez IndexManager podczas reperacji: skoro changed/new
        i tak przechodzi przez pełny odczyt (żeby zaktualizować indeks),
        ten sam obiekt trafia od razu do cache zamiast ginąć — "darmowa"
        hydratacja, bo Scrypt+AES-GCM i tak już się wykonał, niezależnie
        od tego, czy ktokolwiek to zawoła."""
        return self._hydrate_from_note(filepath, mtime, note)

    # ---- inwalidacja -----------------------------------------------------

    def invalidate(self, filepath: str) -> None:
        """Wołane po zapisie/usunięciu notatki (jak dziś _invalidate_note_cache
        + _thumb_cache.pop — tu jedno wywołanie zamiast dwóch)."""
        self._cache.pop(filepath, None)

    def __len__(self) -> int:
        return len(self._cache)
