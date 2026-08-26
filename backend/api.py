"""
Api – klasa wystawiana do JS przez pywebview (js_api).
Kontrakt backend ↔ frontend (Etap 3 planu migracji).

Zasada tytułu notatki: tak jak w v2, tytuł = nazwa pliku bez rozszerzenia
(ImageNoteFile nie przechowuje osobnego pola "title").
"""
import base64
import json
import os
import random
import shutil

import requests
import webview
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication

from .doc_extractor import extract as extract_doc_text
from .image_note_file import ImageNoteFile
from .natsort import nsorted, nsorted_paths
from .search_engine import match_tags, match_text
from .tag_registry import TagRegistry


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
        # Cache miniatur: filepath -> (mtime, base64_str). Port wzorca z v2
        # main_page.py (_thumb_cache), które unikało re-decrypt+decode przy
        # każdym powrocie do tego samego folderu.
        self._thumb_cache: dict[str, tuple[float, str]] = {}
        # Cache pełnych notatek (mtime -> ImageNoteFile): odszyfrowanie pliku
        # (Scrypt KDF + AES-GCM na całym payloadzie łącznie z obrazem) jest
        # kosztowne (dziesiątki ms na plik), więc search_notes/tag_registry/
        # count_tag_usage/_apply_tag_transform współdzielą jeden odczyt na
        # niezmieniony plik zamiast każdy robić go od nowa. Bez tego np.
        # samo wpisywanie w polu szukania odszyfrowywało WSZYSTKIE notatki
        # przy każdym zapytaniu (debounce), co przy większej ich liczbie
        # jest zauważalnie wolne.
        self._note_cache: dict[str, tuple[float, ImageNoteFile]] = {}
        self.tag_registry = TagRegistry(
            on_progress=self._on_tag_progress,
            on_updated=self._on_tags_updated,
            note_loader=self._load_note_cached,
        )

    def set_window(self, window):
        self.window = window

    def _load_note_cached(self, filepath: str) -> ImageNoteFile:
        """
        Odczyt notatki z odszyfrowaniem, ale z cache po mtime — patrz
        komentarz przy self._note_cache. Współdzielony przez wyszukiwanie,
        skan tagów i operacje masowe tag managera.
        """
        mtime = os.path.getmtime(filepath)
        cached = self._note_cache.get(filepath)
        if cached and cached[0] == mtime:
            return cached[1]
        note = ImageNoteFile.load_from_file(filepath, password=self.password or None)
        self._note_cache[filepath] = (mtime, note)
        return note

    def _invalidate_note_cache(self, filepath: str) -> None:
        self._note_cache.pop(filepath, None)

    def ping(self) -> str:
        return "ImgNote v3 backend OK"

    # ── Sesja / logowanie ────────────────────────────────────────

    def login(self, password: str) -> bool:
        """
        Jedno hasło do całej aplikacji (jak w v2). Nie ma "weryfikacji" —
        poprawność hasła ujawnia się dopiero przy próbie odczytu pliku
        zaszyfrowanego (AESGCM podniesie wyjątek przy złym haśle).
        """
        if not password:
            return False
        self.password = password
        return True

    # ── Foldery ──────────────────────────────────────────────────

    def list_root_folders(self) -> list[str]:
        return list(self.root_folders)

    def add_root_folder(self) -> str | None:
        """Natywny dialog wyboru folderu (odpowiednik v2 QFileDialog)."""
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return None
        folder = result[0]
        if folder not in self.root_folders:
            self.root_folders.append(folder)
            self.refresh_tags()
        return folder

    def remove_root_folder(self, folder: str) -> None:
        """Odepnij folder z listy — NIE usuwa niczego z dysku (jak w v2)."""
        if folder in self.root_folders:
            self.root_folders.remove(folder)

    def get_folder_tree(self) -> list[dict]:
        """Drzewo folderów głównych + podfoldery, jak QTreeWidget w v2."""
        return [self._build_tree_node(f) for f in self.root_folders]

    def _build_tree_node(self, folder: str) -> dict:
        node = {"path": folder, "name": os.path.basename(folder) or folder, "children": []}
        try:
            entries = nsorted(list(os.scandir(folder)), key=lambda e: e.name)
            for entry in entries:
                if entry.is_dir():
                    node["children"].append(self._build_tree_node(entry.path))
        except PermissionError:
            pass
        return node

    def create_subfolder(self, parent: str, name: str) -> str:
        name = name.strip().replace("/", "-").replace("\\", "-")
        if not name:
            raise ValueError("Nazwa podfolderu nie może być pusta.")
        new_path = os.path.join(parent, name)
        os.makedirs(new_path, exist_ok=True)
        return new_path

    def delete_folder(self, folder: str) -> None:
        """Usuń podfolder z zawartością. Foldery główne trzeba najpierw odpiąć."""
        if folder in self.root_folders:
            raise ValueError("To folder główny — najpierw go odepnij (remove_root_folder).")
        shutil.rmtree(folder)

    # ── Notatki ──────────────────────────────────────────────────

    def list_notes(self, folder: str) -> list[dict]:
        """Notatki bezpośrednio w folderze (bez podfolderów), jak v2 _show_folder."""
        try:
            files = nsorted_paths([
                e.path for e in os.scandir(folder)
                if e.is_file() and e.name.endswith(".imgnote")
            ])
        except Exception:
            files = []
        return [
            {"filepath": fp, "title": os.path.splitext(os.path.basename(fp))[0]}
            for fp in files
        ]

    def list_all_notes(self, sort_by: str = "folder") -> list[dict]:
        """
        Widok "Home" — płaska lista notatek ze wszystkich folderów głównych
        i podfolderów, niezależnie od aktualnie wybranego folderu w drzewie.

        sort_by:
          - "folder": kolejność zgodna z drzewem folderów (root po roocie,
            rekurencyjnie w głąb), tak jak użytkownik widzi je w panelu.
          - "newest": po mtime pliku malejąco (najnowsze na górze).
          - "random": losowa kolejność (Fisher-Yates przez random.shuffle).

        Nie wymaga odszyfrowania (tytuł = nazwa pliku, jak w list_notes),
        więc nie dotyka _note_cache/Scrypt — koszt jest czysto na poziomie
        systemu plików.
        """
        if sort_by == "folder":
            notes: list[dict] = []

            def walk(folder: str) -> None:
                for n in self.list_notes(folder):
                    n["folder"] = folder
                    notes.append(n)
                try:
                    entries = nsorted(list(os.scandir(folder)), key=lambda e: e.name)
                except Exception:
                    return
                for entry in entries:
                    if entry.is_dir():
                        walk(entry.path)

            for root in self.root_folders:
                walk(root)
            return notes

        notes = []
        for root in self.root_folders:
            for dirpath, _, filenames in os.walk(root):
                for fn in nsorted(filenames):
                    if fn.endswith(".imgnote"):
                        fp = os.path.join(dirpath, fn)
                        notes.append({
                            "filepath": fp,
                            "title": os.path.splitext(fn)[0],
                            "folder": dirpath,
                        })

        if sort_by == "newest":
            notes.sort(key=lambda n: os.path.getmtime(n["filepath"]), reverse=True)
        elif sort_by == "random":
            random.shuffle(notes)

        return notes

    def collect_all_notes(self) -> list[str]:
        """Wszystkie .imgnote ze wszystkich folderów głównych (do wyszukiwania)."""
        result = []
        for root in self.root_folders:
            for dirpath, _, filenames in os.walk(root):
                for fn in nsorted(filenames):
                    if fn.endswith(".imgnote"):
                        result.append(os.path.join(dirpath, fn))
        return result

    def collect_viewer_files(self, folder: str) -> list[str]:
        """
        Port 1:1 v2 viewer_page.py `_collect_files`: notatki z folderu
        i bezpośrednich podfolderów (nie rekurencyjnie głębiej) — do
        nawigacji prev/next w Viewerze.
        """
        result = []
        try:
            entries = nsorted(list(os.scandir(folder)), key=lambda e: e.name)
            for entry in entries:
                if entry.is_file() and entry.name.endswith(".imgnote"):
                    result.append(entry.path)
            for entry in entries:
                if entry.is_dir():
                    sub_entries = nsorted(list(os.scandir(entry.path)), key=lambda e: e.name)
                    for sub in sub_entries:
                        if sub.is_file() and sub.name.endswith(".imgnote"):
                            result.append(sub.path)
        except Exception:
            pass
        return result

    def render_markdown(self, text: str) -> str:
        """Ten sam silnik co v2 (`markdown` z extensions tables/fenced_code/nl2br)."""
        import markdown as _md
        return _md.markdown(text, extensions=["tables", "fenced_code", "nl2br"])


    def get_note(self, filepath: str) -> dict:
        note = self._load_note_cached(filepath)
        return {
            "filepath": filepath,
            "title": os.path.splitext(os.path.basename(filepath))[0],
            "text": note.text,
            "tags": note.tags,
            "image_base64": (
                base64.b64encode(note.image_data).decode() if note.image_data else ""
            ),
        }

    def get_note_thumbnail(self, filepath: str) -> str:
        """
        Sam obrazek (base64) — do lazy-loading miniatur w galerii.
        Cache po mtime (port v2 _thumb_cache): trafienie omija odczyt
        z dysku, deszyfrowanie i dekodowanie całkowicie.
        """
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            return ""

        cached = self._thumb_cache.get(filepath)
        if cached and cached[0] == mtime:
            return cached[1]

        try:
            note = self._load_note_cached(filepath)
        except Exception:
            return ""
        b64 = base64.b64encode(note.image_data).decode() if note.image_data else ""
        self._thumb_cache[filepath] = (mtime, b64)
        return b64

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
        Tworzy lub aktualizuje notatkę. Tytuł = nazwa pliku (jak w v2).
        Jeśli original_filepath podane i różni się od nowej ścieżki
        (np. zmiana tytułu) — stary plik jest usuwany po udanym zapisie.
        """
        image_data = base64.b64decode(image_base64) if image_base64 else b""
        note = ImageNoteFile(text=text, image_data=image_data, tags=list(tags))

        safe_title = (title or "").strip() or "notatka"
        target = os.path.join(folder, f"{safe_title}.imgnote")

        note.save_to_file(target, password=self.password or None)
        # Zawartość pod `target` mogła się zmienić (nadpisanie istniejącej
        # notatki) — wpisy w cache'ach muszą zniknąć, żeby mtime-check przy
        # następnym odczycie wymusił świeży odczyt zamiast starych danych.
        self._thumb_cache.pop(target, None)
        self._invalidate_note_cache(target)

        if original_filepath and original_filepath != target and os.path.exists(original_filepath):
            os.remove(original_filepath)
            self._thumb_cache.pop(original_filepath, None)
            self._invalidate_note_cache(original_filepath)

        self.refresh_tags()
        return target

    def delete_note(self, filepath: str) -> None:
        os.remove(filepath)
        self._thumb_cache.pop(filepath, None)
        self._invalidate_note_cache(filepath)
        self.refresh_tags()

    # ── Wyszukiwanie ─────────────────────────────────────────────

    def search_notes(self, tag_query: str, text_query: str) -> list[dict]:
        """
        Synchroniczne wyszukiwanie po wszystkich folderach głównych.
        MVP: brak streamingu postępu jak v2 _SearchWorker — do ew. dodania
        później (ten sam wzorzec callback co TagRegistry, patrz niżej).
        """
        results = []
        for fp in self.collect_all_notes():
            try:
                note = self._load_note_cached(fp)
            except Exception:
                continue
            if match_tags(tag_query, note.tags) and match_text(text_query, note.text):
                results.append({
                    "filepath": fp,
                    "title": os.path.splitext(os.path.basename(fp))[0],
                })
        return results

    # ── Tagi ─────────────────────────────────────────────────────

    def get_known_tags(self) -> list[str]:
        return self.tag_registry.get_tags()

    def refresh_tags(self) -> None:
        """Odpal skan tagów w tle (threading.Thread z Etapu 2)."""
        if self.root_folders:
            self.tag_registry.refresh(self.root_folders, self.password)

    def _on_tag_progress(self, current: int, total: int) -> None:
        if self.window:
            self.window.evaluate_js(
                f"window.onTagScanProgress && window.onTagScanProgress({current}, {total})"
            )

    def _on_tags_updated(self, tags: list[str]) -> None:
        if self.window:
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

    # ── Tag Manager: operacje masowe (port tag_manager.py) ──────────

    def _apply_tag_transform(self, transform) -> int:
        """
        Stosuje `transform(tags: list[str]) -> list[str]` do każdej notatki
        i zapisuje plik, jeśli lista tagów się zmieniła. Zwraca liczbę
        zmienionych plików. Odpowiednik v2 `TagManagerDialog._apply`
        (bez QProgressDialog — MVP, do rozważenia w Etapie 7 jeśli operacje
        na dużej liczbie plików okażą się zauważalnie wolne).
        """
        changed = 0
        for fp in self.collect_all_notes():
            try:
                note = self._load_note_cached(fp)
                old = list(note.tags)
                note.tags = transform(note.tags)
                if note.tags != old:
                    note.save_to_file(fp, password=self.password or None)
                    self._invalidate_note_cache(fp)
                    self._thumb_cache.pop(fp, None)
                    changed += 1
            except Exception:
                pass
        return changed

    def count_tag_usage(self, tag: str) -> int:
        count = 0
        for fp in self.collect_all_notes():
            try:
                note = self._load_note_cached(fp)
                if tag in note.tags:
                    count += 1
            except Exception:
                pass
        return count


    def rename_tag(self, old: str, new: str) -> dict:
        """Zmiana nazwy tagu we wszystkich plikach. Jeśli `new` już istnieje
        wśród znanych tagów, wywołanie i tak scala (jak w v2 po potwierdzeniu)."""
        new = new.strip()
        if not new or new == old:
            return {"changed": 0, "merged": False}
        existing = self.tag_registry.get_tags()
        merged = new in existing
        changed = self._apply_tag_transform(
            lambda tags: [new if t == old else t for t in tags]
        )
        self.tag_registry.set_tags(
            sorted({new if t == old else t for t in existing}, key=str.lower)
        )
        return {"changed": changed, "merged": merged}

    def merge_tag(self, old: str, target: str) -> dict:
        def transform(tags: list[str]) -> list[str]:
            if old in tags:
                tags = [t for t in tags if t != old]
                if target not in tags:
                    tags.append(target)
            return tags

        changed = self._apply_tag_transform(transform)
        existing = self.tag_registry.get_tags()
        self.tag_registry.set_tags(sorted({t for t in existing if t != old}, key=str.lower))
        return {"changed": changed}

    def delete_tag(self, tag: str) -> dict:
        changed = self._apply_tag_transform(lambda tags: [t for t in tags if t != tag])
        existing = self.tag_registry.get_tags()
        self.tag_registry.set_tags([t for t in existing if t != tag])
        return {"changed": changed}

    # ── Tag Manager: JSON export/import (port 1:1 formatu z v2) ─────

    def export_tags_json(self, path: str) -> dict:
        import datetime as _dt
        usage: dict[str, int] = {}
        all_files = self.collect_all_notes()
        for fp in all_files:
            try:
                note = self._load_note_cached(fp)
                for t in note.tags:
                    usage[t] = usage.get(t, 0) + 1
            except Exception:
                pass
        tags = self.tag_registry.get_tags()
        data = {
            "version": "1.0",
            "exported_at": _dt.datetime.now().isoformat(),
            "tags": sorted(tags),
            "usage_count": {t: usage.get(t, 0) for t in tags},
            "total_files_scanned": len(all_files),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return {"tags_count": len(tags)}

    def preview_import_tags_json(self, path: str) -> dict:
        """Zwraca podsumowanie do potwierdzenia przez użytkownika (jak v2 dialog)."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "tags" not in data:
            raise ValueError('Plik nie zawiera pola "tags".')
        imported = data["tags"]
        if not isinstance(imported, list):
            raise ValueError('"tags" musi być listą.')
        existing = set(self.tag_registry.get_tags())
        new_tags = sorted(
            {str(t).strip() for t in imported if str(t).strip() and str(t).strip() not in existing},
            key=str.lower,
        )
        return {
            "imported_total": len(imported),
            "new_tags": new_tags,
            "existing_count": len(imported) - len(new_tags),
            "exported_at": data.get("exported_at", "nieznana"),
        }

    def confirm_import_tags(self, new_tags: list[str]) -> None:
        """Dodaje potwierdzone nowe tagi do rejestru (bez zapisu do plików — jak v2)."""
        existing = set(self.tag_registry.get_tags())
        self.tag_registry.set_tags(sorted(existing | set(new_tags), key=str.lower))

    def save_tags_dialog(self) -> str | None:
        result = self.window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename="tagi_export.json"
        )
        return result[0] if result else None

    def open_tags_dialog(self) -> str | None:
        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG, file_types=("JSON Files (*.json)", "All files (*.*)")
        )
        return result[0] if result else None
