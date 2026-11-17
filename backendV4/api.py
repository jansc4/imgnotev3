"""
Api – klasa wystawiana do JS przez pywebview (js_api).
Kontrakt backend ↔ frontend (Etap 3 planu migracji V2→V3), od 2026-08-28
w pełni oparty na backendV4 (V4_REDESIGN_PLAN.md, kroki 6-7). Przeniesiony
z backend/api.py do backendV4/api.py (2026-08-28) — backendV4 jest teraz
jedynym żywym backendem, `backend/` zostaje wyłącznie jako archiwalne
narzędzie migracyjne (image_note_file.py w starym formacie, potrzebne
tylko test/migrate_v2_to_v3.py).

Zmiana zasady tytułu: NIE jest już nazwą pliku (to była zasada v2/v3).
Tytuł żyje w zaszyfrowanym payloadzie (backendV4.image_note_file), nazwa
pliku na dysku jest losowa. Folder to wirtualny string w indeksie, nie
prawdziwy katalog — patrz backendV4/folder_manager.py.

TagRegistry USUNIĘTY (już nie działał poprawnie w froncie) — tagi czytane
na żywo z IndexFile.tag_index, zawsze aktualne, bez osobnego skanu w tle.

Bulk-akcje (delete_folder, rename/merge/delete taga, eksport JSON) używają
tag_index jako dokładnej listy plików do dotknięcia (nie skanują całej
biblioteki) i wspólnego popupu postępu (_push_progress, Filar 4). Import
tagów z JSON usunięty (2026-08-28, KISS) — nie miał miejsca w architekturze
opartej wyłącznie na faktycznym użyciu tagów.
"""
import base64
import functools
import inspect
import json
import os
import random
import re
import threading
import time

import requests
import webview
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication

from .doc_extractor import SUPPORTED as DOC_SUPPORTED, extract as extract_doc_text
from .image_note_file import ImageNoteFile
from .index_manager import IndexManager
from .folder_manager import FolderManager
from .note_cache import CachedNote, NoteCache
from .search_engine import SearchEngine


def _mutating(fn):
    """S5: pywebview woła js_api z wielu wątków — operacje zmieniające indeks
    idą pod jedną blokadą Api (RLock, więc zagnieżdżenia są bezpieczne)."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._mutate_lock:
            return fn(self, *args, **kwargs)
    # pywebview czyta nazwy argumentów przez getfullargspec, a ten honoruje __signature__
    wrapper.__signature__ = inspect.signature(fn)
    return wrapper


class _ClipboardBridge(QObject):
    """
    Wywołania js_api (w tym Api.read_clipboard_text) pywebview odpala na
    osobnym threading.Thread (patrz webview.util.js_bridge_call), a QClipboard
    trzeba czytać na wątku głównym Qt. _request emitowany z wątku roboczego
    z BlockingQueuedConnection bezpiecznie "przeskakuje" na główny wątek
    (na którym powstał ten obiekt) i blokuje aż _read_on_main_thread skończy.

    _image_request — analogicznie, ale dla obrazu. Potrzebne, bo QtWebEngine
    (zweryfikowane na Wayland) nie oddaje image/png w JS clipboardData przy
    evencie paste, mimo że system clipboard (wl-paste -l) go ma — JS Clipboard
    API w tym środowisku jest ślepe na obrazy, trzeba czytać przez QClipboard.
    """
    _request = pyqtSignal()
    _image_request = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._text = ""
        self._image_bytes = b""
        self._request.connect(self._read_on_main_thread, Qt.ConnectionType.BlockingQueuedConnection)
        self._image_request.connect(self._read_image_on_main_thread, Qt.ConnectionType.BlockingQueuedConnection)

    def _read_on_main_thread(self):
        app = QApplication.instance()
        self._text = app.clipboard().text() if app else ""

    def _read_image_on_main_thread(self):
        from PyQt6.QtCore import QBuffer, QIODevice
        self._image_bytes = b""
        app = QApplication.instance()
        if not app:
            return
        img = app.clipboard().image()
        if img.isNull():
            return
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        self._image_bytes = bytes(buf.data())

    def read(self) -> str:
        self._request.emit()
        return self._text

    def read_image_png(self) -> bytes:
        self._image_request.emit()
        return self._image_bytes


class Api:
    def __init__(self):
        self._password: str = ""
        self._root_folders: list[str] = []
        self._window = None  # ustawiane z main.py po create_window()
        # Api() jest tworzone w main() na wątku głównym (przed webview.start()),
        # więc _ClipboardBridge dziedziczy właściwe powinowactwo wątku Qt.
        self._clipboard_bridge = _ClipboardBridge()

        # Jeden cache dla pełnej treści I miniatur (backendV4.NoteCache) —
        # zastępuje dzisiejsze dwa niezależne słowniki (_note_cache,
        # _thumb_cache). AES-GCM deszyfruje payload atomowo, więc "sama
        # miniatura" i tak wymaga pełnego odczytu — jeden cache na to
        # wystarcza, osobny na miniatury był zbędną duplikacją.
        self._note_cache = NoteCache()

        # root_folder -> IndexManager / FolderManager. Budowane w
        # _setup_root() przy login()/add_root_folder(). SearchEngine
        # świadomie NIE jest tu cache'owany jako długożyjący obiekt —
        # konstruowany świeżo przy każdym search_notes() (tani, trzy pola),
        # żeby nie złapać martwej referencji po load_or_rebuild() (ten sam
        # problem, przed którym broni się FolderManager trzymając
        # referencję do IndexManagera, nie do IndexFile).
        self._index_managers: dict[str, IndexManager] = {}
        self._folder_managers: dict[str, FolderManager] = {}
        # S2: ostatnie wyniki własnych dialogów — eksport tylko tam, gdzie wskazał użytkownik
        self._last_export_folder: str | None = None
        self._last_tags_save_path: str | None = None
        self._mutate_lock = threading.RLock()
        self._last_progress_t = 0.0

    def set_window(self, window):
        self._window = window

    def _push_progress(self, current: int, total: int) -> None:
        """Jedyny push-event dla WSZYSTKICH długich operacji (Filar 4, krok
        7) — nie osobny mechanizm na operację (jak dawniej onTagScanProgress
        tylko dla tagów). Start/koniec operacji NIE mają osobnego eventu:
        front sam wie kiedy zaczyna i kończy, bo obejmuje wywołanie własnym
        `await` (patrz progress_modal.js). Sygnatura dokładnie pasuje do
        IndexManager(on_progress=...), więc wpina się bez żadnego lambda."""
        # P5: najwyżej co ~50 ms, ostatni krok zawsze (evaluate_js na każdy plik dławi most)
        now = time.monotonic()
        if current < total and now - self._last_progress_t < 0.05:
            return
        self._last_progress_t = now
        if self._window:
            self._window.evaluate_js(
                f"window.onLongOperationProgress && window.onLongOperationProgress({current}, {total})"
            )

    # ---- pomocnicze: znajdź właściwy manager dla ścieżki --------------

    def _setup_root(self, folder: str) -> None:
        mgr = IndexManager(
            folder, password=self._password or None, note_cache=self._note_cache,
            on_progress=self._push_progress,
        )
        mgr.load_or_rebuild()
        mgr.save()
        self._index_managers[folder] = mgr
        self._folder_managers[folder] = FolderManager(mgr)

    def _folder_manager_for(self, display_path: str) -> FolderManager | None:
        for root, fm in self._folder_managers.items():
            if display_path == root or display_path.startswith(root + "/"):
                return fm
        return None

    def _index_manager_for_filepath(self, filepath: str) -> IndexManager | None:
        # pliki leżą płasko w root_folder, więc katalog nadrzędny = klucz wprost
        return self._index_managers.get(os.path.dirname(filepath))

    # ---- S2: walidacja ścieżek przychodzących z JS ----------------------

    _NOTE_NAME_RE = re.compile(r"^[0-9a-f]{32}\.imgnote$")
    _MAX_INPUT_BYTES = 100 * 1024 * 1024
    _IMAGE_INPUT_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".gif", ".webp", ".bmp"}

    def _require_note_path(self, filepath) -> str:
        """Tylko <32hex>.imgnote leżący bezpośrednio w zalogowanym root_folder."""
        if not isinstance(filepath, str) or not self._NOTE_NAME_RE.match(os.path.basename(filepath)):
            raise ValueError("Nieprawidłowa ścieżka notatki.")
        parent = os.path.normpath(os.path.dirname(filepath))
        if parent not in {os.path.normpath(r) for r in self._root_folders}:
            raise ValueError("Notatka spoza otwartych folderów.")
        return filepath

    def _require_input_file(self, path, allowed_ext: set) -> str:
        """Plik wejściowy (obraz/dokument): dozwolone rozszerzenie i limit rozmiaru."""
        if not isinstance(path, str) or os.path.splitext(path)[1].lower() not in allowed_ext:
            raise ValueError("Nieobsługiwany typ pliku.")
        if not os.path.isfile(path):
            raise ValueError("Plik nie istnieje.")
        if os.path.getsize(path) > self._MAX_INPUT_BYTES:
            raise ValueError("Plik jest za duży (limit 100 MB).")
        return path

    def _rewrite_note_tags(self, mgr: IndexManager, filename: str, transform) -> bool:
        """Odczytuje notatkę PRZEZ note_cache (bez podwójnego deszyfrowania,
        jeśli była już otwarta w tej sesji), stosuje `transform(tags) -> tags`,
        zapisuje z powrotem TYLKO jeśli tagi faktycznie się zmieniły. Dzielony
        rdzeń rename_tag/merge_tag/delete_tag — trzy operacje, jeden helper."""
        path = os.path.join(mgr.root_folder, filename)
        mtime = os.path.getmtime(path)
        cached = self._note_cache.get_full(path, self._password or None, mtime)
        old_tags = list(cached.tags)
        new_tags = transform(old_tags)
        if new_tags == old_tags:
            return False
        note = ImageNoteFile(
            text=cached.text, image_data=cached.image_data, tags=new_tags,
            title=cached.title, folder_path=cached.folder_path,
        )
        note.save_to_file(path, password=self._password or None)
        mgr.note_saved(path, note)
        return True

    def ping(self) -> str:
        return "ImgNote v3 backend OK"

    # ── Sesja / logowanie ────────────────────────────────────────

    @_mutating
    def login(self, root_folder: str, password: str) -> dict:
        """
        Folder + hasło razem, jeden krok. Buduje IndexManager i robi
        load_or_rebuild() — dla już zaindeksowanego roota to reperacja
        (hasło od razu zweryfikowane próbą odszyfrowania indeksu/notatek);
        dla ZUPEŁNIE PUSTEGO roota nie ma z czym porównać podanego hasła
        (mgr.password_verified zostaje False).

        W tym drugim przypadku CELOWO NIE wołamy tu mgr.save() — zapis
        zaszyfrowanego indeksu tym hasłem to moment, w którym literówka
        staje się nieodwracalna (użytkownik dowie się o niej dopiero przy
        następnym logowaniu). Front musi wywołać confirm_new_vault_password()
        z drugim wpisanym hasłem, zanim cokolwiek faktycznie trafi na dysk.
        Dla istniejącego, zweryfikowanego vaulta zapisujemy od razu jak
        dawniej — nie ma tam nic do potwierdzania.
        """
        if not root_folder:
            raise ValueError("Nie wybrano folderu.")
        if not password:
            raise ValueError("Hasło nie może być puste.")
        self._password = password

        mgr = IndexManager(
            root_folder, password=self._password or None, note_cache=self._note_cache,
            on_progress=self._push_progress,
        )
        mgr.load_or_rebuild()
        self._index_managers[root_folder] = mgr
        self._folder_managers[root_folder] = FolderManager(mgr)

        if mgr.password_verified:
            mgr.save()
            if root_folder not in self._root_folders:
                self._root_folders.append(root_folder)
            self._push_known_tags()

        return {"password_verified": mgr.password_verified}

    @_mutating
    def confirm_new_vault_password(self, root_folder: str, password_confirm: str) -> bool:
        """Domyka login() dla ŚWIEŻEGO (pustego) vaulta — front woła to
        dopiero po drugim wpisaniu hasła i sprawdzeniu, że się zgadza z
        pierwszym. Dopiero teraz zapisujemy indeks (czyli faktycznie
        "ustawiamy" hasło do tego vaulta) — do tego momentu na dysku nic
        się nie zmieniło, więc literówka w pierwszym polu nic nie kosztuje."""
        mgr = self._index_managers.get(root_folder)
        if not mgr:
            raise ValueError(f"Brak sesji logowania dla: {root_folder} (wywołaj najpierw login()).")
        if password_confirm != self._password:
            raise ValueError("Hasła nie są identyczne.")
        mgr.save()
        if root_folder not in self._root_folders:
            self._root_folders.append(root_folder)
        self._push_known_tags()
        return True

    def pick_folder(self) -> str | None:
        """Sam wybór folderu (dialog), bez żadnych efektów ubocznych —
        używane przez ekran logowania PRZED tym, jak hasło jest znane."""
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else None

    # ── Foldery ──────────────────────────────────────────────────

    def list_root_folders(self) -> list[str]:
        return list(self._root_folders)

    @_mutating
    def add_root_folder(self) -> str | None:
        """Dodatkowy root (multi-root) — dziś używane rzadko (jeden root
        w praktyce), zostaje na przyszłość."""
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        folder = result[0]
        if folder not in self._root_folders:
            self._root_folders.append(folder)
            self._setup_root(folder)
            self._push_known_tags()
        return folder

    @_mutating
    def remove_root_folder(self, folder: str) -> None:
        """Odepnij folder z listy — NIE usuwa niczego z dysku."""
        if folder in self._root_folders:
            self._root_folders.remove(folder)
        self._index_managers.pop(folder, None)
        self._folder_managers.pop(folder, None)

    def get_folder_tree(self) -> list[dict]:
        return [fm.get_tree_root_node() for fm in self._folder_managers.values()]

    @_mutating
    def create_subfolder(self, parent: str, name: str) -> str:
        fm = self._folder_manager_for(parent)
        if not fm:
            raise ValueError(f"Nieznany folder: {parent}")
        return fm.create_folder(parent, name)

    def _notes_under_folder(self, fm: FolderManager, folder: str) -> list[str]:
        """Nazwy plików (siebie + wszystkie podfoldery) pod wirtualną
        ścieżką `folder` — dzielony rdzeń delete_folder/count_notes_in_folder,
        żeby definicja "co należy do tego folderu" nie żyła w dwóch
        miejscach osobno."""
        mgr = fm.index_manager
        folder_path = fm.to_folder_path(folder)
        prefix = f"{folder_path}/" if folder_path else ""
        return [
            filename for filename, entry in mgr.index.notes.items()
            if entry["folder_path"] == folder_path or entry["folder_path"].startswith(prefix)
        ]

    def count_notes_in_folder(self, folder: str) -> int:
        """Czysto odczytowe — do ostrzeżenia w UI PRZED wywołaniem
        delete_folder (krok 8, punkt 4). Zero efektów ubocznych."""
        fm = self._folder_manager_for(folder)
        if not fm:
            raise ValueError(f"Nieznany folder: {folder}")
        return len(self._notes_under_folder(fm, folder))

    def list_note_filepaths_in_folder(self, folder: str) -> list[str]:
        """Rekurencyjnie (siebie + podfoldery) — pełne filepaths, nie same
        nazwy plików jak _notes_under_folder. Używane przez front do
        resolvowania Ctrl/Shift+klik na kafelku podfolderu w gridzie do
        zbioru zaznaczonych notatek (runda "eksport", 2026-09-24) — folder
        jest wirtualny, więc zaznaczenie liczy się zawsze jako płaska lista
        id notatek, nigdy jako sam folder."""
        fm = self._folder_manager_for(folder)
        if not fm:
            raise ValueError(f"Nieznany folder: {folder}")
        mgr = fm.index_manager
        return [os.path.join(mgr.root_folder, fn) for fn in self._notes_under_folder(fm, folder)]

    def _validate_folder_op(self, folder: str) -> FolderManager:
        """Wspólny guard dla rename_folder/move_folder — root folderów nie
        da się przemianować/przenieść (to nie wirtualna ścieżka w indeksie,
        tylko sam root_folder; trzeba by fizycznie przenieść katalog na
        dysku i przepiąć IndexManager, poza zakresem tej rundy)."""
        if folder in self._root_folders:
            raise ValueError("To folder główny — zmiana nazwy/przeniesienie niedostępne.")
        fm = self._folder_manager_for(folder)
        if not fm:
            raise ValueError(f"Nieznany folder: {folder}")
        return fm

    def _rewrite_notes_folder_path(self, fm: FolderManager, old_display_path: str, new_display_path: str) -> int:
        """Dzielony rdzeń rename_folder/move_folder. Foldery są emergentne
        z folder_path (patrz folder_manager.py) — nie ma osobnej encji
        folderu do zaktualizowania, więc "zmiana nazwy/przeniesienie" to
        przepisanie prefiksu folder_path w KAŻDEJ notatce pod starą
        ścieżką (siebie + podfoldery), jedna po drugiej. Analogiczne do
        _rewrite_note_tags (tam: transform tagów; tu: transform ścieżki)."""
        mgr = fm.index_manager
        old_folder_path = fm.to_folder_path(old_display_path)
        new_folder_path = fm.to_folder_path(new_display_path)
        prefix = f"{old_folder_path}/" if old_folder_path else ""

        # entry["folder_path"] z indeksu — tania warstwa, bez deszyfrowania —
        # wystarcza żeby policzyć NOWĄ ścieżkę przed sięgnięciem po pełną treść.
        targets = [
            (filename, entry["folder_path"]) for filename, entry in mgr.index.notes.items()
            if entry["folder_path"] == old_folder_path or entry["folder_path"].startswith(prefix)
        ]
        total = len(targets)
        for i, (filename, entry_folder_path) in enumerate(targets, start=1):
            rel = entry_folder_path[len(old_folder_path):]  # "" albo "/reszta_podfolderu"
            updated_folder_path = new_folder_path + rel

            path = os.path.join(mgr.root_folder, filename)
            mtime = os.path.getmtime(path)
            cached = self._note_cache.get_full(path, self._password or None, mtime)
            note = ImageNoteFile(
                text=cached.text, image_data=cached.image_data, tags=cached.tags,
                title=cached.title, folder_path=updated_folder_path,
            )
            note.save_to_file(path, password=self._password or None)
            mgr.note_saved(path, note)
            self._push_progress(i, total)

        mgr.save()
        fm.rename_pending(old_folder_path, new_folder_path)
        return total

    @_mutating
    def rename_folder(self, folder: str, new_name: str) -> str:
        """Zmienia OSTATNI segment ścieżki (zostaje w tym samym rodzicu).
        Zwraca nowy display_path — front musi go użyć do dalszej nawigacji
        (stary już nie istnieje)."""
        new_name = (new_name or "").strip().replace("/", "-").replace("\\", "-")
        if not new_name:
            raise ValueError("Nazwa nie może być pusta.")
        fm = self._validate_folder_op(folder)
        parent = folder.rsplit("/", 1)[0]
        new_display_path = f"{parent}/{new_name}"
        if new_display_path == folder:
            return folder
        self._rewrite_notes_folder_path(fm, folder, new_display_path)
        return new_display_path

    @_mutating
    def move_folder(self, folder: str, new_parent: str) -> str:
        """Przenosi folder (razem z zawartością i podfolderami) pod nowego
        rodzica, zachowując jego nazwę. Jeden root w praktyce (decyzja z
        tej sesji) — nie obsługuje przenoszenia między różnymi rootami."""
        fm = self._validate_folder_op(folder)
        if new_parent == folder or new_parent.startswith(folder + "/"):
            raise ValueError("Nie można przenieść folderu do samego siebie ani do własnego podfolderu.")
        name = folder.rsplit("/", 1)[-1]
        new_display_path = f"{new_parent}/{name}" if new_parent else name
        if new_display_path == folder:
            return folder
        self._rewrite_notes_folder_path(fm, folder, new_display_path)
        return new_display_path

    @_mutating
    def move_note(self, filepath: str, new_folder: str) -> None:
        """Przenosi POJEDYNCZĄ notatkę do innego folderu — nazwa pliku na
        dysku (losowa) się nie zmienia, tylko folder_path w zaszyfrowanym
        payloadzie. Bez bulk-progresu (jeden plik, nie warto pokazywać modala)."""
        self._require_note_path(filepath)
        fm = self._folder_manager_for(new_folder)
        if not fm:
            raise ValueError(f"Nieznany folder docelowy: {new_folder}")
        new_folder_path = fm.to_folder_path(new_folder)

        mgr = self._index_manager_for_filepath(filepath)
        if not mgr:
            raise ValueError(f"Nieznany plik: {filepath}")
        mtime = os.path.getmtime(filepath)
        cached = self._note_cache.get_full(filepath, self._password or None, mtime)
        if cached.folder_path == new_folder_path:
            return
        note = ImageNoteFile(
            text=cached.text, image_data=cached.image_data, tags=cached.tags,
            title=cached.title, folder_path=new_folder_path,
        )
        note.save_to_file(filepath, password=self._password or None)
        mgr.note_saved(filepath, note)
        fm.note_saved(new_folder_path)
        mgr.save()

    @_mutating
    def delete_folder(self, folder: str) -> dict:
        """Usuwa WSZYSTKIE notatki pod tą wirtualną ścieżką (siebie i
        wszystkich podfolderów) — nie ma czegoś do "opróżnienia" osobno,
        drzewo jest emergentne z folder_path."""
        if folder in self._root_folders:
            raise ValueError("To folder główny — najpierw go odepnij (remove_root_folder).")
        fm = self._folder_manager_for(folder)
        if not fm:
            raise ValueError(f"Nieznany folder: {folder}")
        mgr = fm.index_manager
        to_delete = self._notes_under_folder(fm, folder)
        total = len(to_delete)
        for i, filename in enumerate(to_delete, start=1):
            path = os.path.join(mgr.root_folder, filename)
            if os.path.exists(path):
                os.remove(path)
            mgr.note_deleted(path)
            self._note_cache.invalidate(path)
            self._push_progress(i, total)

        mgr.save()
        self._push_known_tags()
        return {"deleted": total}

    # ── Notatki ──────────────────────────────────────────────────

    def list_notes(self, folder: str) -> list[dict]:
        fm = self._folder_manager_for(folder)
        return fm.list_notes_in(folder) if fm else []

    def list_all_notes(self, sort_by: str = "folder") -> list[dict]:
        """
        Widok "Home" — płaska lista notatek ze wszystkich folderów, wszystkich
        rootów. `mtime` do sortowania "newest" bierzemy z indeksu (już tam
        jest) zamiast osobnego os.path.getmtime() na każdy plik jak dawniej.
        """
        notes: list[dict] = []
        for fm in self._folder_managers.values():
            notes.extend(fm.list_all_notes())

        if sort_by == "newest":
            notes.sort(key=lambda n: n["mtime"], reverse=True)
        elif sort_by == "random":
            random.shuffle(notes)
        else:  # "folder"
            notes.sort(key=lambda n: (n["folder"], n["title"]))

        return notes

    def collect_viewer_files(self, folder: str) -> list[dict]:
        """Notatki z folderu i bezpośrednich podfolderów — nawigacja
        prev/next w Viewerze. Zwraca {filepath, title, folder} — NIE gołe
        stringi (jak dawniej), bo front nie ma jak wyprowadzić prawdziwego
        tytułu/folderu z samej ścieżki pliku (nazwa jest losowa)."""
        fm = self._folder_manager_for(folder)
        if not fm:
            return []
        return fm.list_notes_in_and_direct_children(folder)

    _MD_TAGS = {
        "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "b", "i",
        "del", "s", "code", "pre", "blockquote", "ul", "ol", "li", "a",
        "table", "thead", "tbody", "tr", "th", "td",
    }
    _MD_ATTRS = {"a": {"href", "title"}, "code": {"class"}, "th": {"style"}, "td": {"style"}}

    def render_markdown(self, text: str) -> str:
        """Ten sam silnik co v2 (`markdown` z extensions tables/fenced_code/nl2br).
        Wynik przez nh3: bez surowego HTML/skryptów, bez <img> (zero żądań sieciowych)."""
        import markdown as _md
        import nh3
        html = _md.markdown(text or "", extensions=["tables", "fenced_code", "nl2br"])
        return nh3.clean(
            html,
            tags=self._MD_TAGS,
            attributes=self._MD_ATTRS,
            url_schemes={"http", "https", "mailto"},
            filter_style_properties={"text-align"},
            link_rel="noopener noreferrer",
        )

    def get_note(self, filepath: str) -> dict:
        self._require_note_path(filepath)
        mtime = os.path.getmtime(filepath)
        note = self._note_cache.get_full(filepath, self._password or None, mtime)
        # display_path do UI (np. Viewer musi wiedzieć, do jakiego folderu
        # zapisać przy edycji inline — nie da się tego odtworzyć z samego
        # filepath, bo w v4 wszystkie pliki leżą płasko w root_folder).
        fm = self._folder_managers.get(os.path.dirname(filepath))
        folder = fm.display_path(note.folder_path) if fm else ""
        return {
            "filepath": filepath,
            "title": note.title,
            "folder": folder,
            "text": note.text,
            "html": self.render_markdown(note.text),  # P6: bez drugiego przejścia przez most
            "tags": note.tags,
            "image_base64": (
                base64.b64encode(note.image_data).decode() if note.image_data else ""
            ),
        }

    @_mutating
    def update_note_text(self, filepath: str, text: str) -> dict:
        """P4: edycja samego tekstu (Viewer) — obraz bierzemy z cache po
        stronie Pythona, zamiast przesyłać go JS->Py i z powrotem."""
        self._require_note_path(filepath)
        mgr = self._index_manager_for_filepath(filepath)
        if not mgr:
            raise ValueError(f"Nieznany plik: {filepath}")
        mtime = os.path.getmtime(filepath)
        cached = self._note_cache.get_full(filepath, self._password or None, mtime)
        note = ImageNoteFile(
            text=text, image_data=cached.image_data, tags=list(cached.tags),
            title=cached.title, folder_path=cached.folder_path,
        )
        note.save_to_file(filepath, password=self._password or None)
        mgr.note_saved(filepath, note)
        mgr.save()
        return {"html": self.render_markdown(text)}

    def get_note_thumbnail(self, filepath: str) -> str:
        """Realna, zmniejszona miniatura (backendV4.thumbnail) do
        lazy-loadingu w galerii — nie pełny obraz jak dawniej w V3 (tam
        'miniatura' była zawsze pełnym image_data, tylko wyświetlanym
        mniejszym przez CSS). Przez note_cache, więc darmowa przy
        changed/new podczas reperacji."""
        try:
            self._require_note_path(filepath)
            mtime = os.path.getmtime(filepath)
            thumb = self._note_cache.get_thumbnail(filepath, self._password or None, mtime)
        except Exception:
            return ""
        return base64.b64encode(thumb).decode() if thumb else ""

    @_mutating
    def save_note(
        self,
        folder: str,
        title: str,
        text: str,
        tags: list[str],
        image_base64: str | None = "",
        original_filepath: str | None = None,
    ) -> str:
        """
        Tworzy lub aktualizuje notatkę. `folder` to display_path (root_folder
        + "/" + folder_path wirtualny) — Api tłumaczy go na folder_path przez
        FolderManager, sam nie wie nic o wirtualnych ścieżkach.

        Zmiana względem v3: tytuł już NIE jest nazwą pliku, więc edycja
        istniejącej notatki (title/text/tags się zmieniają) NADPISUJE TEN SAM
        plik zamiast zapisywać pod nową nazwą i usuwać starą — zbędne dla
        formatu, gdzie nazwa pliku jest losowa i nic nie koduje. Nowa nazwa
        (losowa) powstaje tylko dla faktycznie nowej notatki.

        P4: image_base64=None (null z JS) = "obraz bez zmian" — bierzemy go
        z istniejącej notatki zamiast przesyłać przez most; "" = brak obrazu.
        """
        fm = self._folder_manager_for(folder)
        if not fm:
            raise ValueError(f"Nieznany folder: {folder}")
        mgr = fm.index_manager
        folder_path = fm.to_folder_path(folder)
        if original_filepath:
            self._require_note_path(original_filepath)

        if image_base64 is None and original_filepath and os.path.exists(original_filepath):
            mtime = os.path.getmtime(original_filepath)
            image_data = self._note_cache.get_full(original_filepath, self._password or None, mtime).image_data
        else:
            image_data = base64.b64decode(image_base64) if image_base64 else b""
        safe_title = (title or "").strip() or "notatka"
        note = ImageNoteFile(
            text=text, image_data=image_data, tags=list(tags),
            title=safe_title, folder_path=folder_path,
        )

        if original_filepath and os.path.exists(original_filepath):
            target = original_filepath
        else:
            target = os.path.join(mgr.root_folder, ImageNoteFile.new_filename())

        note.save_to_file(target, password=self._password or None)
        mgr.note_saved(target, note)
        fm.note_saved(folder_path)
        mgr.save()  # indeks na dysku zawsze aktualny, nie tylko "po następnym logowaniu"
        self._push_known_tags()

        return target

    @_mutating
    def delete_note(self, filepath: str) -> None:
        self._require_note_path(filepath)
        mgr = self._index_manager_for_filepath(filepath)
        os.remove(filepath)
        if mgr:
            mgr.note_deleted(filepath)
            mgr.save()
        self._push_known_tags()

    # ── Eksport (runda "eksport", 2026-09-24) ───────────────────────────
    # Jeden mechanizm dla pojedynczej notatki, wielu zaznaczonych i (w
    # przyszłości) całej biblioteki — front zawsze przekazuje płaską listę
    # filepaths (patrz AppState.selectedNotes / list_note_filepaths_in_folder),
    # export_notes nie wie i nie musi wiedzieć skąd ta lista pochodzi.
    # Odtwarzanie struktury folderów w output_root jest "za darmo" —
    # os.makedirs per notatka tworzy tylko katalogi faktycznie potrzebne,
    # bez osobnej logiki przycinania drzewa do "użytych gałęzi".
    #
    # odt świadomie POMINIĘTY w tej rundzie (Jan, 2026-09-24: "zostawmy go")
    # — prostszy niż pdf (format płynący, bez paginacji), do dopisania
    # osobno później.

    _IMAGE_EXT = (
        (b"\xff\xd8\xff", ".jpg"),
        (b"\x89PNG\r\n\x1a\n", ".png"),
        (b"GIF87a", ".gif"),
        (b"GIF89a", ".gif"),
        (b"RIFF", ".webp"),
        (b"BM", ".bmp"),
    )

    @staticmethod
    def _image_ext(data: bytes) -> str:
        for sig, ext in Api._IMAGE_EXT:
            if data.startswith(sig):
                return ext
        return ".png"  # nie powinno się zdarzyć — patrz fetch_url_image._IMAGE_MAGIC

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        import re
        name = re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip())
        return name or "notatka"

    def export_notes(self, filepaths: list[str], output_root: str, fmt: str) -> dict:
        """fmt: "png" | "txt" | "png_txt" | "pdf". Kolizje nazw (ten sam
        tytuł, ten sam katalog wyjściowy) rozwiązywane numerycznym sufiksem."""
        if not output_root or output_root != self._last_export_folder:
            raise ValueError("Folder eksportu musi pochodzić z okna wyboru folderu.")
        for filepath in filepaths:
            self._require_note_path(filepath)
        used_names: dict[str, set[str]] = {}
        total = len(filepaths)
        for i, filepath in enumerate(filepaths, start=1):
            mtime = os.path.getmtime(filepath)
            note = self._note_cache.get_full(filepath, self._password or None, mtime)

            # S8: każdy segment przez _sanitize_filename (folder_path jest już bez "."/"..")
            segments = [self._sanitize_filename(s) for s in note.folder_path.split("/") if s]
            out_dir = os.path.join(output_root, *segments) if segments else output_root
            os.makedirs(out_dir, exist_ok=True)

            base = self._sanitize_filename(note.title)
            used = used_names.setdefault(out_dir, set())
            final_base, suffix = base, 1
            while final_base in used:
                suffix += 1
                final_base = f"{base}_{suffix}"
            used.add(final_base)

            self._write_note_export(out_dir, final_base, note, fmt)
            self._push_progress(i, total)

        return {"exported": total}

    def _write_note_export(self, out_dir: str, base: str, note: CachedNote, fmt: str) -> None:
        if fmt == "png":
            self._export_write_image(out_dir, base, note)
        elif fmt == "txt":
            self._export_write_text(out_dir, base, note)
        elif fmt == "png_txt":
            self._export_write_image(out_dir, base, note)
            self._export_write_text(out_dir, base, note)
        elif fmt == "pdf":
            self._export_write_pdf(out_dir, base, note)
        else:
            raise ValueError(f"Nieznany format eksportu: {fmt}")

    def _export_write_image(self, out_dir: str, base: str, note: CachedNote) -> None:
        if not note.image_data:
            return  # notatka bez obrazu — nic do zapisania w tym formacie
        ext = self._image_ext(note.image_data)
        with open(os.path.join(out_dir, f"{base}{ext}"), "wb") as f:
            f.write(note.image_data)

    def _export_write_text(self, out_dir: str, base: str, note: CachedNote) -> None:
        with open(os.path.join(out_dir, f"{base}.txt"), "w", encoding="utf-8") as f:
            f.write(note.text or "")

    # -- PDF: obraz (jeśli jest) na pierwszej stronie A4, dalej tekst.
    # PyMuPDF nie ma wbudowanej paginacji tekstu (insert_textbox mówi tylko
    # "zmieściło się / nie", nie zwraca niezmieszczonej reszty) —
    # _pdf_split_to_fit robi to bisekcją na scratch-page (log n prób),
    # cięcie na granicy spacji/nowej linii, żeby nie łamać słów w środku.
    _PDF_PAGE_W, _PDF_PAGE_H, _PDF_MARGIN, _PDF_FONTSIZE = 595, 842, 40, 11

    def _export_write_pdf(self, out_dir: str, base: str, note: CachedNote) -> None:
        import fitz
        doc = fitz.open()
        rect = fitz.Rect(
            self._PDF_MARGIN, self._PDF_MARGIN,
            self._PDF_PAGE_W - self._PDF_MARGIN, self._PDF_PAGE_H - self._PDF_MARGIN,
        )
        if note.image_data:
            self._pdf_insert_image_page(doc, note.image_data, rect)
        if note.text:
            self._pdf_insert_text_pages(doc, note.text, rect)
        if len(doc) == 0:
            doc.new_page(width=self._PDF_PAGE_W, height=self._PDF_PAGE_H)  # bez obrazu i tekstu — pusta strona zamiast PDF-a bez stron
        doc.save(os.path.join(out_dir, f"{base}.pdf"))
        doc.close()

    def _pdf_insert_image_page(self, doc, image_data: bytes, rect) -> None:
        import fitz
        pix = fitz.Pixmap(image_data)
        scale = min(rect.width / pix.width, rect.height / pix.height, 1.0)
        w, h = pix.width * scale, pix.height * scale
        x0 = rect.x0 + (rect.width - w) / 2
        page = doc.new_page(width=self._PDF_PAGE_W, height=self._PDF_PAGE_H)
        page.insert_image(fitz.Rect(x0, rect.y0, x0 + w, rect.y0 + h), stream=image_data)

    def _pdf_insert_text_pages(self, doc, text: str, rect) -> None:
        remaining = text
        while remaining:
            chunk, remaining = self._pdf_split_to_fit(rect, remaining)
            if not chunk:  # zabezpieczenie przed nieskończoną pętlą (nie powinno się zdarzyć)
                chunk, remaining = remaining, ""
            page = doc.new_page(width=self._PDF_PAGE_W, height=self._PDF_PAGE_H)
            page.insert_textbox(rect, chunk, fontsize=self._PDF_FONTSIZE, fontname="helv")

    def _pdf_fits(self, rect, text: str) -> bool:
        import fitz
        scratch = fitz.open()
        page = scratch.new_page(width=rect.width, height=rect.height)
        overflow = page.insert_textbox(
            fitz.Rect(0, 0, rect.width, rect.height), text,
            fontsize=self._PDF_FONTSIZE, fontname="helv",
        )
        scratch.close()
        return overflow >= 0

    def _pdf_split_to_fit(self, rect, text: str) -> tuple[str, str]:
        """Maks. prefiks `text`, który zmieści się w `rect` (bisekcja), cięty
        na granicy spacji/nowej linii, jeśli to możliwe."""
        if self._pdf_fits(rect, text):
            return text, ""
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._pdf_fits(rect, text[:mid]):
                lo = mid
            else:
                hi = mid - 1
        cut = lo
        if cut < len(text) and not text[cut].isspace():
            boundary = max(text.rfind(" ", 0, cut), text.rfind("\n", 0, cut))
            if boundary > 0:
                cut = boundary + 1
        return text[:cut], text[cut:]

    def pick_export_folder(self) -> str | None:
        """Dialog wyboru folderu docelowego eksportu — osobna nazwa niż
        pick_folder(), żeby front nie mylił z wyborem roota vaulta."""
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        self._last_export_folder = result[0] if result else None
        return self._last_export_folder

    # ── Wyszukiwanie ─────────────────────────────────────────────

    def search_notes(self, tag_query: str, text_query: str) -> list[dict]:
        """Cienka delegacja do SearchEngine per root — AST parsowane raz,
        ewaluowane na indeksie odwróconym, tekst dotyka tylko kandydatów
        po filtrze tagów (patrz backendV4/search_engine.py)."""
        results = []
        for root, mgr in self._index_managers.items():
            fm = self._folder_managers[root]
            engine = SearchEngine(
                mgr.index, root,
                note_loader=lambda p: self._note_cache.get_content(
                    p, self._password or None, os.path.getmtime(p)
                ).text,
            )
            for r in engine.search(tag_query, text_query):
                # SearchEngine zwraca surowy folder_path (nic nie wie o
                # FolderManager — świadomie lean, patrz jego docstring).
                # Front (gallery.js::folderOf) oczekuje pola "folder" jako
                # PEŁNEGO display_path, tak jak z list_notes_in/
                # list_all_notes/collect_viewer_files — bez tej podmiany
                # note.folder było undefined, front spadał na wadliwy
                # fallback (filepath.lastIndexOf("/")), który w V4 (płaskie
                # pliki, losowe UUID) zawsze dawał root_folder. Efekt: klik
                # w kartę notatki z wyszukiwania spoza roota/bezpośrednich
                # podfolderów otwierał Viewer na złej notatce (Jan,
                # 2026-08-28: "niektóre cardy pokazują pierwszy w vaulcie").
                r["folder"] = fm.display_path(r.pop("folder_path"))
                results.append(r)
        return results

    # ── Tagi ─────────────────────────────────────────────────────
    # TagRegistry usunięty — tagi czytane na żywo z IndexFile.tag_index,
    # zawsze aktualne (set_note/remove_note utrzymują go przy każdym
    # zapisie/usunięciu), bez osobnego skanu w tle.

    def get_known_tags(self) -> list[str]:
        tags: set[str] = set()
        for mgr in self._index_managers.values():
            tags.update(mgr.index.tag_index.keys())
        return sorted(tags, key=str.lower)

    def refresh_tags(self) -> None:
        """Zostaje jako nazwa/wywołanie dla frontu (gallery.js woła to przy
        wejściu do widoku) — ale już nic nie skanuje, tylko przepycha
        aktualny stan. Trzymam kontrakt (onTagsUpdated) identyczny, żeby
        front nie wymagał zmian."""
        self._push_known_tags()

    def _push_known_tags(self) -> None:
        if self._window:
            tags = self.get_known_tags()
            self._window.evaluate_js(
                f"window.onTagsUpdated && window.onTagsUpdated({json.dumps(tags)})"
            )

    # ── Import: dokumenty i URL (Etap 0a — DnD obrazka z przeglądarki) ──

    def extract_doc(self, path: str) -> dict:
        """Ekstrakcja tekstu z pliku (pdf/docx/odt/rtf/txt/md), jak v2."""
        self._require_input_file(path, DOC_SUPPORTED)
        text, info = extract_doc_text(path)
        return {"text": text, "info": info}

    # Sygnatury (magic bytes) rozpoznawanych formatów obrazu — port kontroli
    # z v2 add_page.py `_UrlFetcher.run` (tam sprawdzane PRZED zwróceniem
    # sukcesu). Bez tej walidacji strona chroniona (np. Cloudflare) może
    # oddać 200 OK z HTML-em (challenge/błąd) zamiast pliku graficznego —
    # backend meldował wtedy "pobrano obraz", a w podglądzie nic się nie
    # pokazywało, bo <img> dostawał base64 zakodowanego HTML-a.
    _IMAGE_MAGIC = (
        b"\xff\xd8\xff",       # JPEG
        b"\x89PNG\r\n\x1a\n",  # PNG
        b"GIF87a",             # GIF
        b"GIF89a",             # GIF
        b"RIFF",               # WEBP (kontener RIFF/WEBP)
        b"BM",                 # BMP
    )

    def fetch_url_image(self, url: str, referer: str | None = None) -> dict:
        """
        Pobiera obraz po URL — obsługuje scenariusz Etap 0a: przeciągnięcie
        obrazka z przeglądarki (frontend wysyła sam URL, nie bajty).

        `referer`, jeśli podany, to prawdziwy adres strony, na której osadzony
        był obrazek (np. strona submission) — dokładnie to, co przeglądarka
        wysyła jako nagłówek Referer przy normalnym wczytywaniu strony.
        Niektóre CDN-y stosują ochronę przed hotlinkowaniem i sprawdzają ten
        nagłówek (musi wskazywać na stronę serwisu, nie na sam plik ani na
        domenę CDN-a) i/lub User-Agent. Pierwsza próba idzie z przeglądarkowym
        User-Agentem i realnym Refererem (o ile mamy); jeśli mimo to serwer
        odrzuci żądanie 403-ką, druga próba dokłada Referer zgadywany z
        domeny obrazka — słabszy fallback na wypadek, gdy referer nie był
        znany z góry.
        """
        browser_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        if referer:
            browser_headers["Referer"] = referer

        try:
            resp = self._http_get(url, browser_headers, self._MAX_FETCH_IMAGE)
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 403:
                raise
            from urllib.parse import urlparse
            parsed = urlparse(url)
            retry_headers = {**browser_headers, "Referer": f"{parsed.scheme}://{parsed.netloc}/"}
            resp = self._http_get(url, retry_headers, self._MAX_FETCH_IMAGE)

        content = resp.content
        if not any(content.startswith(sig) for sig in self._IMAGE_MAGIC):
            content_type = resp.headers.get("Content-Type", "")
            raise ValueError(
                "Serwer nie zwrócił pliku graficznego (dostano "
                f"{content_type or 'nieznany typ'}) — strona mogła zablokować "
                "pobieranie (np. zabezpieczenie przed hotlinkowaniem)."
            )

        return {
            "image_base64": base64.b64encode(content).decode(),
            "content_type": resp.headers.get("Content-Type", ""),
        }

    def fetch_url_text(self, url: str) -> dict:
        """Import tekstu z URL (drag&drop linku, jak TextDropArea w v2)."""
        resp = self._http_get(url, {"User-Agent": "ImgNoteV3/1.0"}, self._MAX_FETCH_TEXT)
        return {"text": resp.text}

    _MAX_FETCH_IMAGE = 50 * 1024 * 1024
    _MAX_FETCH_TEXT = 5 * 1024 * 1024

    @staticmethod
    def _http_get(url: str, headers: dict, limit: int):
        """S8: tylko http/https, odczyt strumieniowy z twardym limitem bajtów
        (resp.content bez limitu = cały plik w RAM, nawet wielogigabajtowy)."""
        from urllib.parse import urlparse
        if urlparse(url or "").scheme not in ("http", "https"):
            raise ValueError("Obsługiwane są tylko adresy http/https.")
        resp = requests.get(url, timeout=15, headers=headers, stream=True)
        try:
            resp.raise_for_status()
            declared = resp.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > limit:
                raise ValueError(f"Plik za duży (limit {limit // (1024 * 1024)} MB).")
            buf = bytearray()
            for chunk in resp.iter_content(64 * 1024):
                buf += chunk
                if len(buf) > limit:
                    raise ValueError(f"Plik za duży (limit {limit // (1024 * 1024)} MB).")
        finally:
            resp.close()
        # resp.content/.text działają dalej na już odczytanych bajtach (z kodowaniem jak dawniej)
        resp._content = bytes(buf)
        return resp

    def read_clipboard_text(self) -> str:
        """
        Odczyt tekstu ze schowka systemowego (Qt) — używane przez przycisk
        "Wklej notatkę" w edytorze, do zdeserializowania JSON-a skopiowanego
        przez rozszerzenie ImgNote Tag Extractor. Nie wymaga JS Clipboard API
        (który QtWebEngine domyślnie blokuje bez obsłużonej
        featurePermissionRequested).
        """
        return self._clipboard_bridge.read()

    def read_clipboard_image(self) -> str:
        """
        Odczyt obrazu ze schowka systemowego przez QClipboard — fallback dla
        edytora, gdy JS clipboardData nie widzi image/png (zweryfikowane na
        Wayland+QtWebEngine: JS dostaje tylko text/uri-list, mimo że obraz
        naprawdę jest w schowku). Zwraca base64 PNG albo "" jeśli schowek
        nie zawiera obrazu.
        """
        data = self._clipboard_bridge.read_image_png()
        return base64.b64encode(data).decode() if data else ""

    # ── Dialogi natywne (fallback dla DnD z systemu, Etap 0b) ───────

    def open_file_dialog(self) -> str | None:
        result = self._window.create_file_dialog(webview.OPEN_DIALOG)
        return result[0] if result else None

    def read_binary_file(self, path: str) -> str:
        """
        Odczyt obrazka jako base64 — wybranego przez open_file_dialog() lub
        upuszczonego z systemu (Etap 0b, pywebviewFullPath). S2: tylko
        rozszerzenia obrazów i limit rozmiaru.
        """
        self._require_input_file(path, self._IMAGE_INPUT_EXT)
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()

    @staticmethod
    def parse_bulk_tags(raw: str) -> list[str]:
        """
        Port 1:1 z v2 add_page.py `_parse_bulk_tags`: rozbija tekst po
        przecinku/spacji/nowej linii na listę tagów.
        """
        import re
        parts = re.split(r"[\s,\n]+", raw.strip())
        return [t for t in parts if t and not t.isspace()]

    # ── Tag Manager: operacje masowe ─────────────────────────────────
    # tag_index[tag] to DOKŁADNA lista plików do dotknięcia — nie skanujemy
    # już wszystkich notatek jak w V3 (`collect_all_notes()` + sprawdzanie
    # każdej), tylko te, które faktycznie mają dany tag. Import tagów z JSON
    # (preview/confirm) USUNIĘTY (2026-08-28, decyzja Jana, KISS): opierał
    # się na "znanych" nazwach tagów niezależnych od użycia, a tag_index z
    # definicji nie ma takiego pojęcia — tag istnieje tylko jako pochodna
    # faktycznego użycia. `open_tags_dialog` (picker do importu) poszedł
    # razem z nim; `save_tags_dialog` (eksport) zostaje.

    def count_tag_usage(self, tag: str) -> int:
        return sum(len(mgr.index.tag_index.get(tag, [])) for mgr in self._index_managers.values())

    @_mutating
    def rename_tag(self, old: str, new: str) -> dict:
        new = new.strip()
        if not new or new == old:
            return {"changed": 0, "merged": False}

        def transform(tags):
            return [new if t == old else t for t in tags]

        changed = 0
        merged = False
        for mgr in self._index_managers.values():
            filenames = list(mgr.index.tag_index.get(old, []))  # kopia - mutujemy tag_index w trakcie
            if not filenames:
                continue
            merged = merged or (new in mgr.index.tag_index)
            total = len(filenames)
            for i, filename in enumerate(filenames, start=1):
                if self._rewrite_note_tags(mgr, filename, transform):
                    changed += 1
                self._push_progress(i, total)
            mgr.save()

        self._push_known_tags()
        return {"changed": changed, "merged": merged}

    @_mutating
    def merge_tag(self, old: str, target: str) -> dict:
        target = target.strip()
        if not target or target == old:
            return {"changed": 0}

        def transform(tags):
            if old in tags:
                tags = [t for t in tags if t != old]
                if target not in tags:
                    tags.append(target)
            return tags

        changed = 0
        for mgr in self._index_managers.values():
            filenames = list(mgr.index.tag_index.get(old, []))
            if not filenames:
                continue
            total = len(filenames)
            for i, filename in enumerate(filenames, start=1):
                if self._rewrite_note_tags(mgr, filename, transform):
                    changed += 1
                self._push_progress(i, total)
            mgr.save()

        self._push_known_tags()
        return {"changed": changed}

    @_mutating
    def delete_tag(self, tag: str) -> dict:
        def transform(tags):
            return [t for t in tags if t != tag]

        changed = 0
        for mgr in self._index_managers.values():
            filenames = list(mgr.index.tag_index.get(tag, []))
            if not filenames:
                continue
            total = len(filenames)
            for i, filename in enumerate(filenames, start=1):
                if self._rewrite_note_tags(mgr, filename, transform):
                    changed += 1
                self._push_progress(i, total)
            mgr.save()

        self._push_known_tags()
        return {"changed": changed}

    def export_tags_json(self, path: str) -> dict:
        """Zero deszyfrowania — liczby użycia to długości list w
        tag_index, już policzone, nie trzeba niczego skanować."""
        if not path or path != self._last_tags_save_path:
            raise ValueError("Ścieżka zapisu musi pochodzić z okna zapisu.")
        import datetime as _dt
        usage: dict[str, int] = {}
        total_files = 0
        for mgr in self._index_managers.values():
            total_files += len(mgr.index.notes)
            for tag, filenames in mgr.index.tag_index.items():
                usage[tag] = usage.get(tag, 0) + len(filenames)
        tags = sorted(usage.keys(), key=str.lower)
        data = {
            "version": "1.0",
            "exported_at": _dt.datetime.now().isoformat(),
            "tags": tags,
            "usage_count": usage,
            "total_files_scanned": total_files,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return {"tags_count": len(tags)}

    def save_tags_dialog(self) -> str | None:
        result = self._window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename="tagi_export.json"
        )
        self._last_tags_save_path = result[0] if result else None
        return self._last_tags_save_path
