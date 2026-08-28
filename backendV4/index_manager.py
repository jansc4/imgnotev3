"""
index_manager.py — właściciel indeksu (backendV4).

Jedyna klasa w tym pakiecie, która ma stan i orkiestruje pozostałe: przy
starcie łączy IndexFile (co wiemy), index_reconcile.reconcile() (co się
zmieniło na dysku) i ImageNoteFile (odszyfrowanie zmienionych/nowych
plików), dając spójny indeks w pamięci.

Celowo NIE zapisuje indeksu automatycznie po load_or_rebuild() — zapis to
osobny, jawny krok (save()), żeby wywołujący decydował o momencie zapisu,
zamiast każdy start appki bezwarunkowo dopisywał coś na dysk.

Folder rename/move (Filar 3 w planie) świadomie poza zakresem tej wersji —
dobudowywane później, na tym samym fundamencie.
"""
import os

from .image_note_file import ImageNoteFile
from .index_file import IndexFile
from .index_reconcile import reconcile, ReconcileResult


class IndexManager:
    def __init__(self, root_folder: str, password: str | None = None, on_progress=None, note_cache=None):
        self.root_folder = root_folder
        self.password = password
        self.index = IndexFile()  # nadpisywane przez load_or_rebuild()
        # callable(current: int, total: int) | None — wołany podczas
        # odszyfrowywania changed/new w load_or_rebuild(). Sam hak nie ma
        # GUI ani pywebview — to dopiero krok 7 podepnie coś, co realnie
        # pokazuje popup (ten sam wzorzec co dziś TagRegistry.on_progress).
        # Domyślnie no-op, więc IndexManager działa identycznie jak wcześniej,
        # jeśli nikt tego nie poda.
        self.on_progress = on_progress
        # NoteCache | None — opcjonalny. Jeśli podany, każdy plik odszyfrowany
        # podczas reperacji (changed/new) trafia do niego "za darmo": Scrypt+
        # AES-GCM i tak się wykonał, żeby zaktualizować indeks, więc nie ma
        # sensu wyrzucać text/image_data i deszyfrować ponownie przy pierwszym
        # otwarciu notatki. Bez tego IndexManager działa jak wcześniej.
        self.note_cache = note_cache
        # True dopiero gdy hasło faktycznie zweryfikowano deszyfrując coś
        # (indeks albo dowolną notatkę). Zostaje False tylko wtedy, gdy
        # root_folder jest całkowicie pusty (świeży vault) — backend
        # fizycznie nie ma wtedy z czym porównać podanego hasła. Ekran
        # logowania powinien w takim wypadku wymagać potwierdzenia hasła
        # (dwa pola), zamiast ufać jednemu bez weryfikacji.
        self.password_verified = False

    # ---- skanowanie dysku ----------------------------------------------

    def _scan_disk(self) -> dict:
        """filename -> mtime dla każdego *.imgnote w root_folder. Plik
        indeksu (.imgidx) ma inne rozszerzenie, więc filtr sam go pomija,
        bez specjalnego przypadku. os.scandir zamiast glob+getmtime — jeden
        stat na plik (DirEntry go cache'uje), nie dwa osobne wywołania."""
        files = {}
        with os.scandir(self.root_folder) as it:
            for entry in it:
                if entry.is_file() and entry.name.endswith(".imgnote"):
                    files[entry.name] = entry.stat().st_mtime
        return files

    # ---- start appki / reperacja ---------------------------------------

    def load_or_rebuild(self) -> ReconcileResult:
        """Flow startu z planu: wczytaj istniejący indeks (albo zacznij od
        pustego, jeśli go nie ma), porównaj z dyskiem, zaaplikuj różnicę.
        Złe hasło do istniejącego indeksu wyleci stąd jako wyjątek — to
        jest ten "tani test hasła" z planu, zanim appka zdąży zrobić
        cokolwiek droższego."""
        index_path = os.path.join(self.root_folder, IndexFile.FILENAME)
        if os.path.exists(index_path):
            self.index = IndexFile.load_from_file(index_path, password=self.password)
            self.password_verified = True  # indeks się odszyfrował -> hasło poprawne
        else:
            self.index = IndexFile()
            # UWAGA: samo to NIE weryfikuje hasła. Jeśli poniżej okaże się,
            # że root_folder jest pusty, password_verified zostanie False
            # aż do końca metody — patrz komentarz w __init__.

        files_on_disk = self._scan_disk()
        result = reconcile(self.index.notes, files_on_disk)

        for filename in result.orphaned:
            self.index.remove_note(filename)

        # changed i new traktowane identycznie: oba wymagają odszyfrowania
        # pliku, oba dostają pełne bajty obrazka w pamięci przy okazji —
        # dokładnie ten punkt, gdzie w przyszłości "doczepi się" generowanie
        # miniatury bez dodatkowej deszyfracji (decyzja "Miniatury" w planie)
        to_process = (*result.changed, *result.new)
        total = len(to_process)
        for i, filename in enumerate(to_process, start=1):
            path = os.path.join(self.root_folder, filename)
            note = ImageNoteFile.load_from_file(path, password=self.password)
            self.password_verified = True  # udana deszyfracja = hasło poprawne
            mtime = files_on_disk[filename]
            self.index.set_note(filename, note.title, note.folder_path, mtime, note.tags)
            if self.note_cache is not None:
                self.note_cache.put_full(path, mtime, note)  # darmowa hydratacja
            if self.on_progress:
                self.on_progress(i, total)

        return result

    def save(self) -> None:
        index_path = os.path.join(self.root_folder, IndexFile.FILENAME)
        self.index.save_to_file(index_path, password=self.password)

    # ---- utrzymanie indeksu w trakcie działania appki -------------------

    def note_saved(self, path: str, note: ImageNoteFile) -> None:
        """Wołane PO zapisaniu notatki na dysk (note.save_to_file już się
        wykonało). Przyjmuje gotowy obiekt `note` zamiast ponownie
        odszyfrowywać plik z dysku — wywołujący (przyszłe Api.save_note)
        i tak go ma w ręku, a Scrypt jest drogi, nie duplikujemy go tam,
        gdzie dane już są w pamięci. Ta sama zasada obejmuje note_cache —
        hydratacja jest darmowa, nie tylko przy load_or_rebuild()."""
        filename = os.path.basename(path)
        mtime = os.path.getmtime(path)
        self.index.set_note(filename, note.title, note.folder_path, mtime, note.tags)
        if self.note_cache is not None:
            self.note_cache.put_full(path, mtime, note)

    def note_deleted(self, path: str) -> None:
        filename = os.path.basename(path)
        self.index.remove_note(filename)
        if self.note_cache is not None:
            self.note_cache.invalidate(path)
