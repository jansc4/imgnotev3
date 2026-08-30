"""
folder_manager.py — wirtualne drzewo folderów dla backendV4.

Trzyma referencję do IndexManager (NIE do IndexFile bezpośrednio) — po
load_or_rebuild() IndexManager PODMIENIA self.index na nowy obiekt, więc
zapamiętana referencja do starego IndexFile stałaby się martwa po drugim
odświeżeniu. Czytanie przez self.index_manager.index zawsze widzi aktualny
stan.

Dwie reprezentacje tej samej ścieżki:
- folder_path: wirtualny string z indeksu, np. "Klienci/X" (albo "" dla roota)
- display_path: folder_path z doklejonym root_folder z przodu, np.
  "/home/jan/notatki/Klienci/X" — TYLKO to wychodzi/wchodzi przez granicę
  z frontendem. gallery.js robi `path.startsWith(root + "/")` przy
  liczeniu breadcrumbów — display_path to gwarantuje, folder_path samo
  w sobie by tego nie spełniało (nie zaczyna się od root_folder).

_pending: sesyjne, puste wirtualne foldery (utworzone przyciskiem, jeszcze
bez notatki w środku) — żyją tylko w pamięci tego obiektu, znikają z
restartem appki. Nie mylić z "pustymi folderami" w sensie trwałym, których
architektura formatu świadomie nie wspiera (folder_path bez notatki nigdy
nie trafia do indeksu).
"""
import os

from .index_manager import IndexManager
from .natsort import natural_key


class FolderManager:
    def __init__(self, index_manager: IndexManager):
        self.index_manager = index_manager
        self._pending: set[str] = set()

    # ---- tłumaczenie folder_path <-> display_path ----------------------
    # Publiczne (bez podkreślnika) celowo — Api legalnie tego potrzebuje
    # przy save_note/collect_viewer_files, to nie przeciek abstrakcji.

    def display_path(self, folder_path: str) -> str:
        root = self.index_manager.root_folder
        return root if not folder_path else f"{root}/{folder_path}"

    def to_folder_path(self, display_path: str) -> str:
        root = self.index_manager.root_folder
        if display_path == root:
            return ""
        prefix = root + "/"
        if not display_path.startswith(prefix):
            raise ValueError(f"{display_path!r} nie należy do roota {root!r}")
        return display_path[len(prefix):]

    # ---- drzewo ----------------------------------------------------------

    def get_tree_root_node(self) -> dict:
        """Jeden węzeł na cały root (name/path/children) — dokładnie kształt,
        jaki dziś zwraca Api._build_tree_node, więc gallery.js renderuje go
        bez żadnych zmian. Węzły pośrednie (np. "Klienci" gdy istnieje tylko
        "Klienci/X") dobudowane automatycznie w pętli niżej — folder bez
        notatek bezpośrednio w nim, ale z niepustymi podfolderami, to co
        innego niż w pełni pusty folder (którego architektura nie wspiera)."""
        root = self.index_manager.root_folder
        root_node = {"path": root, "name": os.path.basename(root) or root, "children": []}
        node_by_path: dict[str, dict] = {"": root_node}

        leaf_paths = {
            entry["folder_path"]
            for entry in self.index_manager.index.notes.values()
            if entry["folder_path"]
        }
        leaf_paths |= self._pending

        for folder_path in sorted(leaf_paths):
            segments = folder_path.split("/")
            acc = ""
            for seg in segments:
                parent_acc = acc
                acc = f"{acc}/{seg}" if acc else seg
                if acc in node_by_path:
                    continue
                node = {"path": self.display_path(acc), "name": seg, "children": []}
                node_by_path[acc] = node
                node_by_path[parent_acc]["children"].append(node)

        self._sort_children_recursive(root_node)
        return root_node

    def _sort_children_recursive(self, node: dict) -> None:
        """Naturalne, case-insensitive sortowanie (Jan, 2026-08-28:
        "straciliśmy po drodze sortowanie w tree") — regres z porządków po
        V4, gdzie backend/natsort.py skasowano jako rzekomo nieużywane,
        nie zauważając że stary backend/api.py używał go WSZĘDZIE przy
        budowie drzewa. Osobny przebieg PO zbudowaniu drzewa, nie poleganie
        na kolejności sorted(leaf_paths) wyżej — ten drugi sortuje pełne
        ścieżki jako stringi, co przez przypadek często daje poprawną
        kolejność rodzeństwa, ale nie gwarantuje jej (i nigdy nie było
        natural/case-insensitive)."""
        node["children"].sort(key=lambda n: natural_key(n["name"]))
        for child in node["children"]:
            self._sort_children_recursive(child)

    # ---- notatki w folderze / zarządzanie pending -----------------------

    def list_notes_in(self, display_path: str) -> list[dict]:
        folder_path = self.to_folder_path(display_path)
        root = self.index_manager.root_folder
        notes = [
            {
                "filepath": os.path.join(root, filename),
                "title": entry["title"],
                "folder": display_path,
            }
            for filename, entry in self.index_manager.index.notes.items()
            if entry["folder_path"] == folder_path
        ]
        # Naturalne sortowanie po TYTULE (nie nazwie pliku — w V4 to losowy
        # UUID, nie ma już żadnej treści; w V3 nazwa pliku BYŁA tytułem,
        # więc dawne sortowanie po plikach de facto sortowało po tytule).
        notes.sort(key=lambda n: natural_key(n["title"]))
        return notes

    def create_folder(self, parent_display_path: str, name: str) -> str:
        name = name.strip().replace("/", "-").replace("\\", "-")
        if not name:
            raise ValueError("Nazwa podfolderu nie może być pusta.")
        parent_folder_path = self.to_folder_path(parent_display_path)
        new_folder_path = f"{parent_folder_path}/{name}" if parent_folder_path else name
        self._pending.add(new_folder_path)
        return self.display_path(new_folder_path)

    def note_saved(self, folder_path: str) -> None:
        """Wołane po zapisaniu notatki — jeśli trafiła do folderu, który był
        tylko 'obiecany' (pending), przestaje nim być: ma teraz realną
        zawartość w indeksie, get_tree_root_node znajdzie go i tak."""
        self._pending.discard(folder_path)

    def rename_pending(self, old_folder_path: str, new_folder_path: str) -> None:
        """Aktualizuje _pending po rename/move folderu (Api._rewrite_notes_
        folder_path) — pusty, jeszcze bez żadnej notatki podfolder inaczej
        zniknąłby z drzewa po zmianie ścieżki (rewrite notatek go nie
        dotyczy, bo w indeksie po prostu nie ma dla niego wpisu)."""
        prefix = f"{old_folder_path}/"
        updated: set[str] = set()
        for p in self._pending:
            if p == old_folder_path:
                updated.add(new_folder_path)
            elif p.startswith(prefix):
                updated.add(new_folder_path + p[len(old_folder_path):])
            else:
                updated.add(p)
        self._pending = updated

    def list_all_notes(self) -> list[dict]:
        """Wszystkie notatki w tym roocie, płasko (widok Home) — folder
        jako display_path, gotowe do zwrócenia na front. `mtime` dorzucone
        za darmo (już jest w indeksie) — sortowanie "najnowsze" we froncie
        nie wymaga osobnego os.path.getmtime() na każdy plik. Domyślnie
        posortowane naturalnie po tytule — front i tak potrafi przesortować
        inaczej (alfabetycznie/datą/losowo, patrz home-sort-bar), ale to
        sensowna baza zamiast nieokreślonej kolejności ze słownika."""
        root = self.index_manager.root_folder
        notes = [
            {
                "filepath": os.path.join(root, filename),
                "title": entry["title"],
                "folder": self.display_path(entry["folder_path"]),
                "mtime": entry["mtime"],
            }
            for filename, entry in self.index_manager.index.notes.items()
        ]
        notes.sort(key=lambda n: natural_key(n["title"]))
        return notes

    def list_notes_in_and_direct_children(self, display_path: str) -> list[dict]:
        """Notatki w tym folderze ORAZ w jego bezpośrednich podfolderach
        (nie głębiej) — do nawigacji prev/next w Viewerze (port
        collect_viewer_files, bez rekursji w głąb jak w oryginale).
        Naturalne sortowanie po tytule — front tu NIE ma własnego
        przełącznika sortowania (w odróżnieniu od Home), więc kolejność
        z backendu jest ostateczna."""
        folder_path = self.to_folder_path(display_path)
        prefix = f"{folder_path}/" if folder_path else ""
        root = self.index_manager.root_folder
        results = []
        for filename, entry in self.index_manager.index.notes.items():
            fp = entry["folder_path"]
            if fp == folder_path:
                is_match = True
            elif fp.startswith(prefix):
                remainder = fp[len(prefix):]
                is_match = remainder != "" and "/" not in remainder
            else:
                is_match = False
            if is_match:
                results.append({
                    "filepath": os.path.join(root, filename),
                    "title": entry["title"],
                    "folder": self.display_path(fp),
                })
        results.sort(key=lambda n: natural_key(n["title"]))
        return results
