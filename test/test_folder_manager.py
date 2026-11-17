"""
test_folder_manager.py — testy backendV4/folder_manager.py.

Większość testów na lekkim fake'u (tylko .root_folder i .index.notes —
FolderManager niczego więcej z IndexManagera nie potrzebuje), bo cała
logika tu to operacje na stringach/słownikach, zero kryptografii/I/O.
Jeden test integracyjny na końcu z prawdziwym IndexManager + tempdir,
żeby potwierdzić że realne wpięcie też działa.

Uruchomienie:
    uv run python test/test_folder_manager.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backendV4.folder_manager import FolderManager
from backendV4.image_note_file import ImageNoteFile
from backendV4.index_manager import IndexManager

PASSWORD = "haslo123"

from _v4 import new_key  # noqa: E402  (szybkie parametry Argon2id w testach)
KEY = new_key(PASSWORD)


class _FakeIndex:
    def __init__(self, notes):
        self.notes = notes


class _FakeIndexManager:
    def __init__(self, root_folder, notes):
        self.root_folder = root_folder
        self.index = _FakeIndex(notes)


def _entry(title, folder_path, mtime=1.0):
    return {"title": title, "folder_path": folder_path, "mtime": mtime}


def test_tree_empty_when_no_folders():
    idx = _FakeIndexManager("/root", {"a.imgnote": _entry("A", "")})
    fm = FolderManager(idx)
    tree = fm.get_tree_root_node()
    assert tree == {"path": "/root", "name": "root", "children": []}
    print("OK  test_tree_empty_when_no_folders")


def test_tree_single_level():
    idx = _FakeIndexManager("/root", {"a.imgnote": _entry("A", "Klienci")})
    fm = FolderManager(idx)
    tree = fm.get_tree_root_node()
    assert len(tree["children"]) == 1
    child = tree["children"][0]
    assert child["path"] == "/root/Klienci"
    assert child["name"] == "Klienci"
    assert child["children"] == []
    print("OK  test_tree_single_level")


def test_tree_nested_creates_intermediate_nodes():
    """Tylko 'Klienci/X/Umowy' w indeksie -> Klienci i Klienci/X muszą
    powstać jako węzły pośrednie, mimo że żadna notatka nie ma folder_path
    równego dokładnie im."""
    idx = _FakeIndexManager("/root", {"a.imgnote": _entry("A", "Klienci/X/Umowy")})
    fm = FolderManager(idx)
    tree = fm.get_tree_root_node()

    assert len(tree["children"]) == 1
    klienci = tree["children"][0]
    assert klienci["name"] == "Klienci" and klienci["path"] == "/root/Klienci"

    assert len(klienci["children"]) == 1
    x = klienci["children"][0]
    assert x["name"] == "X" and x["path"] == "/root/Klienci/X"

    assert len(x["children"]) == 1
    umowy = x["children"][0]
    assert umowy["name"] == "Umowy" and umowy["path"] == "/root/Klienci/X/Umowy"
    assert umowy["children"] == []
    print("OK  test_tree_nested_creates_intermediate_nodes")


def test_tree_dedup_shared_prefix():
    idx = _FakeIndexManager("/root", {
        "a.imgnote": _entry("A", "A/B"),
        "b.imgnote": _entry("B", "A/C"),
    })
    fm = FolderManager(idx)
    tree = fm.get_tree_root_node()

    assert len(tree["children"]) == 1  # "A" pojawia się RAZ, nie dwa razy
    a_node = tree["children"][0]
    assert a_node["name"] == "A"
    child_names = {c["name"] for c in a_node["children"]}
    assert child_names == {"B", "C"}
    print("OK  test_tree_dedup_shared_prefix")


def test_tree_children_sorted_naturally():
    """Jan, 2026-08-28: 'straciliśmy po drodze sortowanie w tree' —
    regres z porządków po V4 (backend/natsort.py skasowany jako rzekomo
    nieużywany). case-insensitive + liczby jako liczby, nie stringi."""
    idx = _FakeIndexManager("/root", {
        "a.imgnote": _entry("A", "zebra"),
        "b.imgnote": _entry("B", "Apple"),   # wielka litera — musi wylądować obok apple, nie przed Z
        "c.imgnote": _entry("C", "Folder10"),
        "d.imgnote": _entry("D", "Folder2"), # naturalnie: 2 przed 10, nie leksykograficznie
        "e.imgnote": _entry("E", "banana"),
    })
    fm = FolderManager(idx)
    tree = fm.get_tree_root_node()
    names = [c["name"] for c in tree["children"]]
    assert names == ["Apple", "banana", "Folder2", "Folder10", "zebra"], names
    print("OK  test_tree_children_sorted_naturally")


def test_tree_children_sorted_naturally_at_every_level():
    """Sortowanie musi działać na KAŻDYM poziomie drzewa, nie tylko na
    korzeniu — rekurencyjny przebieg po zbudowaniu, nie efekt uboczny
    sorted(leaf_paths) po pełnych ścieżkach."""
    idx = _FakeIndexManager("/root", {
        "a.imgnote": _entry("A", "Rodzic/Zeta"),
        "b.imgnote": _entry("B", "Rodzic/alfa"),
    })
    fm = FolderManager(idx)
    tree = fm.get_tree_root_node()
    rodzic = tree["children"][0]
    names = [c["name"] for c in rodzic["children"]]
    assert names == ["alfa", "Zeta"], names
    print("OK  test_tree_children_sorted_naturally_at_every_level")


def test_list_notes_in_root():
    idx = _FakeIndexManager("/root", {
        "a.imgnote": _entry("A", ""),
        "b.imgnote": _entry("B", "Sub"),
    })
    fm = FolderManager(idx)
    results = fm.list_notes_in("/root")
    assert len(results) == 1
    assert results[0]["title"] == "A"
    assert results[0]["filepath"] == "/root/a.imgnote"
    assert results[0]["folder"] == "/root"
    print("OK  test_list_notes_in_root")


def test_list_notes_in_subfolder():
    idx = _FakeIndexManager("/root", {
        "a.imgnote": _entry("A", ""),
        "b.imgnote": _entry("B", "Klienci/X"),
    })
    fm = FolderManager(idx)
    results = fm.list_notes_in("/root/Klienci/X")
    assert len(results) == 1
    assert results[0]["title"] == "B"
    print("OK  test_list_notes_in_subfolder")


def test_to_folder_path_rejects_foreign_root():
    idx = _FakeIndexManager("/root", {})
    fm = FolderManager(idx)
    try:
        fm.list_notes_in("/zupelnie/inny/root")
    except ValueError:
        pass
    else:
        raise AssertionError("obca ścieżka powinna rzucić ValueError")
    print("OK  test_to_folder_path_rejects_foreign_root")


def test_pending_folder_appears_then_survives_note_saved():
    idx = _FakeIndexManager("/root", {})
    fm = FolderManager(idx)

    display_path = fm.create_folder("/root", "Nowy")
    assert display_path == "/root/Nowy"
    tree = fm.get_tree_root_node()
    assert [c["name"] for c in tree["children"]] == ["Nowy"]

    # notatka faktycznie ląduje w indeksie z tym folder_path...
    idx.index.notes["x.imgnote"] = _entry("X", "Nowy")
    # ...i note_saved usuwa go z pending — drzewo nadal go pokazuje,
    # bo teraz pochodzi z indeksu, nie z pending
    fm.note_saved("Nowy")
    tree = fm.get_tree_root_node()
    assert [c["name"] for c in tree["children"]] == ["Nowy"]
    print("OK  test_pending_folder_appears_then_survives_note_saved")


def test_pending_folder_disappears_if_never_used():
    """Symulacja 'przepadnie z sesją': jeśli nic nie zapisano, a ktoś
    (np. nowa sesja / świeży FolderManager) nie ma tego w pending, folder
    po prostu nie istnieje w drzewie — nie ma go w indeksie."""
    idx = _FakeIndexManager("/root", {})
    fm = FolderManager(idx)
    fm.create_folder("/root", "Ulotny")
    assert len(fm.get_tree_root_node()["children"]) == 1

    fresh_fm = FolderManager(idx)  # "nowa sesja" - nie dziedziczy _pending
    assert fresh_fm.get_tree_root_node()["children"] == []
    print("OK  test_pending_folder_disappears_if_never_used")


def test_real_index_manager_survives_reload_without_stale_reference():
    """Integracyjny: prawdziwy IndexManager + prawdziwe pliki. Kluczowy
    dowód: load_or_rebuild() PODMIENIA index_manager.index na nowy obiekt
    (patrz komentarz w folder_manager.py) — FolderManager skonstruowany
    RAZ, na początku, musi mimo to widzieć drugi rebuild, bo czyta przez
    self.index_manager.index (na żywo), nie przez zapamiętaną referencję."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        note1 = ImageNoteFile(text="", title="Pierwsza", folder_path="Klienci", tags=[])
        note1.save_to_file(str(root / ImageNoteFile.new_filename()), KEY)

        mgr = IndexManager(str(root), password=PASSWORD)
        mgr.load_or_rebuild()
        fm = FolderManager(mgr)  # skonstruowany RAZ, przed drugim rebuildem

        tree = fm.get_tree_root_node()
        assert [c["name"] for c in tree["children"]] == ["Klienci"]

        # druga notatka, w NOWYM folderze, dopisana PO tym jak fm powstał
        note2 = ImageNoteFile(text="", title="Druga", folder_path="Archiwum", tags=[])
        note2.save_to_file(str(root / ImageNoteFile.new_filename()), KEY)

        mgr.load_or_rebuild()  # to podmienia mgr.index na nowy obiekt

        tree = fm.get_tree_root_node()  # ten sam fm, żadnej rekonstrukcji
        names = {c["name"] for c in tree["children"]}
        assert names == {"Klienci", "Archiwum"}, (
            f"fm powinien widzieć Archiwum po drugim rebuildzie, widzi: {names}"
        )
    print("OK  test_real_index_manager_survives_reload_without_stale_reference")


def test_list_all_notes_flat_with_mtime():
    idx = _FakeIndexManager("/root", {
        "a.imgnote": _entry("A", "", mtime=1.0),
        "b.imgnote": _entry("B", "Klienci/X", mtime=2.0),
    })
    fm = FolderManager(idx)
    results = fm.list_all_notes()
    by_title = {r["title"]: r for r in results}
    assert by_title["A"]["folder"] == "/root"
    assert by_title["A"]["mtime"] == 1.0
    assert by_title["B"]["folder"] == "/root/Klienci/X"
    assert by_title["B"]["mtime"] == 2.0
    print("OK  test_list_all_notes_flat_with_mtime")


def test_list_notes_in_and_direct_children():
    idx = _FakeIndexManager("/root", {
        "a.imgnote": _entry("A", "Klienci"),           # bezpośrednio w Klienci
        "b.imgnote": _entry("B", "Klienci/X"),          # bezpośredni podfolder
        "c.imgnote": _entry("C", "Klienci/X/Umowy"),    # za głęboko - NIE powinno się pojawić
        "d.imgnote": _entry("D", "Inne"),               # zupełnie inny folder
    })
    fm = FolderManager(idx)
    results = fm.list_notes_in_and_direct_children("/root/Klienci")
    titles = {r["title"] for r in results}
    assert titles == {"A", "B"}
    print("OK  test_list_notes_in_and_direct_children")


def test_note_lists_sorted_naturally_by_title():
    """Ten sam regres co w drzewie (Jan, 2026-08-28), ale dla list
    notatek — w V3 nazwa pliku BYŁA tytułem, więc sortowanie po plikach
    (nsorted_paths) de facto sortowało po tytule; w V4 nazwa pliku to
    losowy UUID, więc odpowiednik musi jawnie sortować po polu title."""
    idx = _FakeIndexManager("/root", {
        "a.imgnote": _entry("Notatka 10", ""),
        "b.imgnote": _entry("notatka 2", ""),
        "c.imgnote": _entry("Alfa", ""),
    })
    fm = FolderManager(idx)

    titles_in = [n["title"] for n in fm.list_notes_in("/root")]
    assert titles_in == ["Alfa", "notatka 2", "Notatka 10"], titles_in

    titles_all = [n["title"] for n in fm.list_all_notes()]
    assert titles_all == ["Alfa", "notatka 2", "Notatka 10"], titles_all

    titles_viewer = [n["title"] for n in fm.list_notes_in_and_direct_children("/root")]
    assert titles_viewer == ["Alfa", "notatka 2", "Notatka 10"], titles_viewer
    print("OK  test_note_lists_sorted_naturally_by_title")


def main():
    test_tree_empty_when_no_folders()
    test_tree_single_level()
    test_tree_nested_creates_intermediate_nodes()
    test_tree_dedup_shared_prefix()
    test_tree_children_sorted_naturally()
    test_tree_children_sorted_naturally_at_every_level()
    test_list_notes_in_root()
    test_list_notes_in_subfolder()
    test_to_folder_path_rejects_foreign_root()
    test_pending_folder_appears_then_survives_note_saved()
    test_pending_folder_disappears_if_never_used()
    test_real_index_manager_survives_reload_without_stale_reference()
    test_list_all_notes_flat_with_mtime()
    test_list_notes_in_and_direct_children()
    test_note_lists_sorted_naturally_by_title()
    print("\nWszystkie testy przeszły.")


if __name__ == "__main__":
    main()
