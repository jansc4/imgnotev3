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
import json
import os
import random

import requests
import webview
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication

from .doc_extractor import extract as extract_doc_text
from .image_note_file import ImageNoteFile
from .index_manager import IndexManager
from .folder_manager import FolderManager
from .note_cache import NoteCache
from .search_engine import SearchEngine


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
        self.password: str = ""
        self.root_folders: list[str] = []
        self.window = None  # ustawiane z main.py po create_window()
        # Api() jest tworzone w main() na wątku głównym (przed webview.start()),
        # więc _ClipboardBridge dziedziczy właściwe powinowactwo wątku Qt.
        self._clipboard_bridge = _ClipboardBridge()

        # Jeden cache dla pełnej treści I miniatur (backendV4.NoteCache) —
        # zastępuje dzisiejsze dwa niezależne słowniki (_note_cache,
        # _thumb_cache). AES-GCM deszyfruje payload atomowo, więc "sama
        # miniatura" i tak wymaga pełnego odczytu — jeden cache na to
        # wystarcza, osobny na miniatury był zbędną duplikacją.
        self.note_cache = NoteCache()

        # root_folder -> IndexManager / FolderManager. Budowane w
        # _setup_root() przy login()/add_root_folder(). SearchEngine
        # świadomie NIE jest tu cache'owany jako długożyjący obiekt —
        # konstruowany świeżo przy każdym search_notes() (tani, trzy pola),
        # żeby nie złapać martwej referencji po load_or_rebuild() (ten sam
        # problem, przed którym broni się FolderManager trzymając
        # referencję do IndexManagera, nie do IndexFile).
        self.index_managers: dict[str, IndexManager] = {}
        self.folder_managers: dict[str, FolderManager] = {}

    def set_window(self, window):
        self.window = window

    def _push_progress(self, current: int, total: int) -> None:
        """Jedyny push-event dla WSZYSTKICH długich operacji (Filar 4, krok
        7) — nie osobny mechanizm na operację (jak dawniej onTagScanProgress
        tylko dla tagów). Start/koniec operacji NIE mają osobnego eventu:
        front sam wie kiedy zaczyna i kończy, bo obejmuje wywołanie własnym
        `await` (patrz progress_modal.js). Sygnatura dokładnie pasuje do
        IndexManager(on_progress=...), więc wpina się bez żadnego lambda."""
        if self.window:
            self.window.evaluate_js(
                f"window.onLongOperationProgress && window.onLongOperationProgress({current}, {total})"
            )

    # ---- pomocnicze: znajdź właściwy manager dla ścieżki --------------

    def _setup_root(self, folder: str) -> None:
        mgr = IndexManager(
            folder, password=self.password or None, note_cache=self.note_cache,
            on_progress=self._push_progress,
        )
        mgr.load_or_rebuild()
        mgr.save()
        self.index_managers[folder] = mgr
        self.folder_managers[folder] = FolderManager(mgr)

    def _folder_manager_for(self, display_path: str) -> FolderManager | None:
        for root, fm in self.folder_managers.items():
            if display_path == root or display_path.startswith(root + "/"):
                return fm
        return None

    def _index_manager_for_filepath(self, filepath: str) -> IndexManager | None:
        # pliki leżą płasko w root_folder, więc katalog nadrzędny = klucz wprost
        return self.index_managers.get(os.path.dirname(filepath))

    def _rewrite_note_tags(self, mgr: IndexManager, filename: str, transform) -> bool:
        """Odczytuje notatkę PRZEZ note_cache (bez podwójnego deszyfrowania,
        jeśli była już otwarta w tej sesji), stosuje `transform(tags) -> tags`,
        zapisuje z powrotem TYLKO jeśli tagi faktycznie się zmieniły. Dzielony
        rdzeń rename_tag/merge_tag/delete_tag — trzy operacje, jeden helper."""
        path = os.path.join(mgr.root_folder, filename)
        mtime = os.path.getmtime(path)
        cached = self.note_cache.get_full(path, self.password or None, mtime)
        old_tags = list(cached.tags)
        new_tags = transform(old_tags)
        if new_tags == old_tags:
            return False
        note = ImageNoteFile(
            text=cached.text, image_data=cached.image_data, tags=new_tags,
            title=cached.title, folder_path=cached.folder_path,
        )
        note.save_to_file(path, password=self.password or None)
        mgr.note_saved(path, note)
        return True

    def ping(self) -> str:
        return "ImgNote v3 backend OK"

    # ── Sesja / logowanie ────────────────────────────────────────

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
        self.password = password

        mgr = IndexManager(
            root_folder, password=self.password or None, note_cache=self.note_cache,
            on_progress=self._push_progress,
        )
        mgr.load_or_rebuild()
        self.index_managers[root_folder] = mgr
        self.folder_managers[root_folder] = FolderManager(mgr)

        if mgr.password_verified:
            mgr.save()
            if root_folder not in self.root_folders:
                self.root_folders.append(root_folder)
            self._push_known_tags()

        return {"password_verified": mgr.password_verified}

    def confirm_new_vault_password(self, root_folder: str, password_confirm: str) -> bool:
        """Domyka login() dla ŚWIEŻEGO (pustego) vaulta — front woła to
        dopiero po drugim wpisaniu hasła i sprawdzeniu, że się zgadza z
        pierwszym. Dopiero teraz zapisujemy indeks (czyli faktycznie
        "ustawiamy" hasło do tego vaulta) — do tego momentu na dysku nic
        się nie zmieniło, więc literówka w pierwszym polu nic nie kosztuje."""
        mgr = self.index_managers.get(root_folder)
        if not mgr:
            raise ValueError(f"Brak sesji logowania dla: {root_folder} (wywołaj najpierw login()).")
        if password_confirm != self.password:
            raise ValueError("Hasła nie są identyczne.")
        mgr.save()
        if root_folder not in self.root_folders:
            self.root_folders.append(root_folder)
        self._push_known_tags()
        return True

    def pick_folder(self) -> str | None:
        """Sam wybór folderu (dialog), bez żadnych efektów ubocznych —
        używane przez ekran logowania PRZED tym, jak hasło jest znane."""
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else None

    # ── Foldery ──────────────────────────────────────────────────

    def list_root_folders(self) -> list[str]:
        return list(self.root_folders)

    def add_root_folder(self) -> str | None:
        """Dodatkowy root (multi-root) — dziś używane rzadko (jeden root
        w praktyce), zostaje na przyszłość."""
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        folder = result[0]
        if folder not in self.root_folders:
            self.root_folders.append(folder)
            self._setup_root(folder)
            self._push_known_tags()
        return folder

    def remove_root_folder(self, folder: str) -> None:
        """Odepnij folder z listy — NIE usuwa niczego z dysku."""
        if folder in self.root_folders:
            self.root_folders.remove(folder)
        self.index_managers.pop(folder, None)
        self.folder_managers.pop(folder, None)

    def get_folder_tree(self) -> list[dict]:
        return [fm.get_tree_root_node() for fm in self.folder_managers.values()]

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

    def delete_folder(self, folder: str) -> dict:
        """Usuwa WSZYSTKIE notatki pod tą wirtualną ścieżką (siebie i
        wszystkich podfolderów) — nie ma czegoś do "opróżnienia" osobno,
        drzewo jest emergentne z folder_path."""
        if folder in self.root_folders:
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
            self.note_cache.invalidate(path)
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
        for fm in self.folder_managers.values():
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

    def render_markdown(self, text: str) -> str:
        """Ten sam silnik co v2 (`markdown` z extensions tables/fenced_code/nl2br)."""
        import markdown as _md
        return _md.markdown(text, extensions=["tables", "fenced_code", "nl2br"])

    def get_note(self, filepath: str) -> dict:
        mtime = os.path.getmtime(filepath)
        note = self.note_cache.get_full(filepath, self.password or None, mtime)
        # display_path do UI (np. Viewer musi wiedzieć, do jakiego folderu
        # zapisać przy edycji inline — nie da się tego odtworzyć z samego
        # filepath, bo w v4 wszystkie pliki leżą płasko w root_folder).
        fm = self.folder_managers.get(os.path.dirname(filepath))
        folder = fm.display_path(note.folder_path) if fm else ""
        return {
            "filepath": filepath,
            "title": note.title,
            "folder": folder,
            "text": note.text,
            "tags": note.tags,
            "image_base64": (
                base64.b64encode(note.image_data).decode() if note.image_data else ""
            ),
        }

    def get_note_thumbnail(self, filepath: str) -> str:
        """Realna, zmniejszona miniatura (backendV4.thumbnail) do
        lazy-loadingu w galerii — nie pełny obraz jak dawniej w V3 (tam
        'miniatura' była zawsze pełnym image_data, tylko wyświetlanym
        mniejszym przez CSS). Przez note_cache, więc darmowa przy
        changed/new podczas reperacji."""
        try:
            mtime = os.path.getmtime(filepath)
            cached = self.note_cache.get_full(filepath, self.password or None, mtime)
        except Exception:
            return ""
        return base64.b64encode(cached.thumbnail_data).decode() if cached.thumbnail_data else ""

    def save_note(
        self,
        folder: str,
        title: str,
        text: str,
        tags: list[str],
        image_base64: str = "",
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
        """
        fm = self._folder_manager_for(folder)
        if not fm:
            raise ValueError(f"Nieznany folder: {folder}")
        mgr = fm.index_manager
        folder_path = fm.to_folder_path(folder)

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

        note.save_to_file(target, password=self.password or None)
        mgr.note_saved(target, note)
        fm.note_saved(folder_path)
        mgr.save()  # indeks na dysku zawsze aktualny, nie tylko "po następnym logowaniu"
        self._push_known_tags()

        return target

    def delete_note(self, filepath: str) -> None:
        mgr = self._index_manager_for_filepath(filepath)
        os.remove(filepath)
        if mgr:
            mgr.note_deleted(filepath)
            mgr.save()
        self._push_known_tags()

    # ── Wyszukiwanie ─────────────────────────────────────────────

    def search_notes(self, tag_query: str, text_query: str) -> list[dict]:
        """Cienka delegacja do SearchEngine per root — AST parsowane raz,
        ewaluowane na indeksie odwróconym, tekst dotyka tylko kandydatów
        po filtrze tagów (patrz backendV4/search_engine.py)."""
        results = []
        for root, mgr in self.index_managers.items():
            engine = SearchEngine(
                mgr.index, root,
                note_loader=lambda p: self.note_cache.get_full(
                    p, self.password or None, os.path.getmtime(p)
                ).text,
            )
            results.extend(engine.search(tag_query, text_query))
        return results

    # ── Tagi ─────────────────────────────────────────────────────
    # TagRegistry usunięty — tagi czytane na żywo z IndexFile.tag_index,
    # zawsze aktualne (set_note/remove_note utrzymują go przy każdym
    # zapisie/usunięciu), bez osobnego skanu w tle.

    def get_known_tags(self) -> list[str]:
        tags: set[str] = set()
        for mgr in self.index_managers.values():
            tags.update(mgr.index.tag_index.keys())
        return sorted(tags, key=str.lower)

    def refresh_tags(self) -> None:
        """Zostaje jako nazwa/wywołanie dla frontu (gallery.js woła to przy
        wejściu do widoku) — ale już nic nie skanuje, tylko przepycha
        aktualny stan. Trzymam kontrakt (onTagsUpdated) identyczny, żeby
        front nie wymagał zmian."""
        self._push_known_tags()

    def _push_known_tags(self) -> None:
        if self.window:
            tags = self.get_known_tags()
            self.window.evaluate_js(
                f"window.onTagsUpdated && window.onTagsUpdated({json.dumps(tags)})"
            )

    # ── Import: dokumenty i URL (Etap 0a — DnD obrazka z przeglądarki) ──

    def extract_doc(self, path: str) -> dict:
        """Ekstrakcja tekstu z pliku (pdf/docx/odt/rtf/txt/md), jak v2."""
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
            resp = requests.get(url, timeout=15, headers=browser_headers)
            resp.raise_for_status()
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 403:
                raise
            from urllib.parse import urlparse
            parsed = urlparse(url)
            retry_headers = {**browser_headers, "Referer": f"{parsed.scheme}://{parsed.netloc}/"}
            resp = requests.get(url, timeout=15, headers=retry_headers)
            resp.raise_for_status()

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
        resp = requests.get(url, timeout=15, headers={"User-Agent": "ImgNoteV3/1.0"})
        resp.raise_for_status()
        return {"text": resp.text}

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
        result = self.window.create_file_dialog(webview.OPEN_DIALOG)
        return result[0] if result else None

    def read_binary_file(self, path: str) -> str:
        """
        Odczyt dowolnego pliku jako base64 — używane dla obrazków wybranych
        przez open_file_dialog() lub upuszczonych z systemu (Etap 0b,
        pywebviewFullPath).
        """
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
        return sum(len(mgr.index.tag_index.get(tag, [])) for mgr in self.index_managers.values())

    def rename_tag(self, old: str, new: str) -> dict:
        new = new.strip()
        if not new or new == old:
            return {"changed": 0, "merged": False}

        def transform(tags):
            return [new if t == old else t for t in tags]

        changed = 0
        merged = False
        for mgr in self.index_managers.values():
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
        for mgr in self.index_managers.values():
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

    def delete_tag(self, tag: str) -> dict:
        def transform(tags):
            return [t for t in tags if t != tag]

        changed = 0
        for mgr in self.index_managers.values():
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
        import datetime as _dt
        usage: dict[str, int] = {}
        total_files = 0
        for mgr in self.index_managers.values():
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
        result = self.window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename="tagi_export.json"
        )
        return result[0] if result else None
