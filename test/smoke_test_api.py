"""
smoke_test_api.py — end-to-end sanity check nowo wpiętego backend/api.py
(krok 6) na prawdziwym, tymczasowym roocie. Pomija dialogi plikowe
(pick_folder/add_root_folder) — woła _setup_root() bezpośrednio, tak jak
login() robiłby to po wybraniu folderu przez użytkownika.

Uruchomienie:
    uv run python test/smoke_test_api.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backendV4.api import Api

PASSWORD = "haslo123"


class _FakeWindow:
    """Zamiast prawdziwego okna pywebview — zbiera wywołania evaluate_js,
    żeby sprawdzić że _push_progress faktycznie coś pcha podczas rebuildu."""
    def __init__(self):
        self.calls: list[str] = []

    def evaluate_js(self, script: str) -> None:
        self.calls.append(script)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        api = Api()
        api.password = PASSWORD
        api.root_folders.append(root)
        api._setup_root(root)  # to samo co login() robi po wyborze folderu
        print("OK  _setup_root")

        target = api.save_note(root, "Pierwsza notatka", "treść testowa", ["projekt", "wazne"])
        assert Path(target).exists()
        print(f"OK  save_note -> {target}")

        note = api.get_note(target)
        assert note["title"] == "Pierwsza notatka"
        assert note["text"] == "treść testowa"
        assert note["tags"] == ["projekt", "wazne"]
        assert note["folder"] == root, "get_note musi zwrócić display_path, nie sam filepath"
        print("OK  get_note (w tym pole 'folder')")

        notes_in_root = api.list_notes(root)
        assert len(notes_in_root) == 1 and notes_in_root[0]["title"] == "Pierwsza notatka"
        print("OK  list_notes (root)")

        tree = api.get_folder_tree()
        assert tree == [{"path": root, "name": Path(root).name, "children": []}]
        print("OK  get_folder_tree (płasko, bez podfolderów)")

        sub_display_path = api.create_subfolder(root, "Klienci")
        assert sub_display_path == f"{root}/Klienci"
        print(f"OK  create_subfolder -> {sub_display_path}")

        target2 = api.save_note(sub_display_path, "Druga notatka", "w podfolderze", ["projekt"])
        note2 = api.get_note(target2)
        assert note2["title"] == "Druga notatka"
        print("OK  save_note do podfolderu")

        tree = api.get_folder_tree()
        assert len(tree[0]["children"]) == 1
        assert tree[0]["children"][0]["name"] == "Klienci"
        assert tree[0]["children"][0]["path"] == sub_display_path
        print("OK  get_folder_tree (z podfolderem, po zapisaniu notatki - pending -> realny)")

        notes_in_sub = api.list_notes(sub_display_path)
        assert len(notes_in_sub) == 1 and notes_in_sub[0]["title"] == "Druga notatka"
        print("OK  list_notes (podfolder)")

        viewer_files = api.collect_viewer_files(root)
        assert all(isinstance(f, dict) and "title" in f and "folder" in f for f in viewer_files), (
            "collect_viewer_files musi zwracać obiekty {filepath, title, folder}, nie gołe stringi"
        )
        print("OK  collect_viewer_files (obiekty, nie gołe ścieżki)")

        home = api.list_all_notes(sort_by="folder")
        assert len(home) == 2
        print("OK  list_all_notes")

        results = api.search_notes(tag_query="projekt", text_query="")
        assert len(results) == 2
        print("OK  search_notes (tag_query, obie notatki mają 'projekt')")

        results = api.search_notes(tag_query="", text_query="podfolderze")
        assert len(results) == 1 and results[0]["title"] == "Druga notatka"
        print("OK  search_notes (text_query)")

        known_tags = api.get_known_tags()
        assert known_tags == ["projekt", "wazne"]
        print("OK  get_known_tags")

        thumb = api.get_note_thumbnail(target)
        assert thumb == ""  # brak obrazka w tej notatce
        print("OK  get_note_thumbnail (brak obrazka -> pusty string)")

        import io as _io
        from PIL import Image as _Image
        _png_buf = _io.BytesIO()
        _Image.frombytes("RGB", (500, 400), os.urandom(500 * 400 * 3)).save(_png_buf, format="PNG")
        _png_bytes = _png_buf.getvalue()
        import base64 as _base64
        target_with_image = api.save_note(
            root, "Z obrazkiem", "", [], image_base64=_base64.b64encode(_png_bytes).decode()
        )
        thumb_b64 = api.get_note_thumbnail(target_with_image)
        assert thumb_b64 != ""
        thumb_bytes = _base64.b64decode(thumb_b64)
        assert len(thumb_bytes) < len(_png_bytes), "miniatura powinna być mniejsza niż oryginał"
        thumb_img = _Image.open(_io.BytesIO(thumb_bytes))
        assert max(thumb_img.size) <= 320
        print("OK  get_note_thumbnail (realna miniatura, mniejsza niż oryginał)")

        api.delete_note(target_with_image)  # posprzątane, żeby nie zaburzać dalszych asercji

        api.delete_note(target)
        assert not Path(target).exists()
        notes_in_root_after = api.list_notes(root)
        assert notes_in_root_after == []
        print("OK  delete_note")

        # edycja istniejącej notatki NADPISUJE ten sam plik (nie tworzy nowego)
        edited_target = api.save_note(
            sub_display_path, "Druga notatka (edytowana)", "nowa treść",
            ["projekt", "nowy"], original_filepath=target2,
        )
        assert edited_target == target2, "edycja powinna nadpisać ten sam plik, nie stworzyć nowy"
        edited = api.get_note(target2)
        assert edited["title"] == "Druga notatka (edytowana)"
        assert edited["text"] == "nowa treść"
        print("OK  save_note (edycja nadpisuje ten sam plik)")

        print("\nWszystkie kroki smoke testu przeszły.")


def test_progress_pushed_during_setup_root():
    """Osobny root, ZE plikami PRZED _setup_root (symulacja świeżego
    importu) — udowadnia że _push_progress faktycznie leci przez
    window.evaluate_js z rosnącym current i stałym total podczas
    load_or_rebuild(), nie tylko że hak istnieje."""
    from backendV4.image_note_file import ImageNoteFile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for i in range(3):
            note = ImageNoteFile(text="", title=f"N{i}", folder_path="", tags=[])
            note.save_to_file(str(root / ImageNoteFile.new_filename()), password=PASSWORD)

        api = Api()
        api.password = PASSWORD
        api.window = _FakeWindow()
        api._setup_root(str(root))

        progress_calls = [c for c in api.window.calls if "onLongOperationProgress" in c]
        assert progress_calls == [
            "window.onLongOperationProgress && window.onLongOperationProgress(1, 3)",
            "window.onLongOperationProgress && window.onLongOperationProgress(2, 3)",
            "window.onLongOperationProgress && window.onLongOperationProgress(3, 3)",
        ], f"nieoczekiwane wywołania progresu: {progress_calls}"
    print("OK  test_progress_pushed_during_setup_root")


def test_bulk_tag_actions_and_delete_folder():
    """rename_tag/merge_tag/delete_tag/count_tag_usage/export_tags_json/
    delete_folder na realnych plikach — potwierdza że tag_index-owe
    skróty dają te same wyniki co dawny pełny skan, i że mgr.save()
    faktycznie zapisuje indeks na dysk po każdej operacji (nowy,
    świeży IndexManager na tym samym roocie widzi zmiany bez reperacji)."""
    from backendV4.image_note_file import ImageNoteFile
    from backendV4.index_manager import IndexManager

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        api = Api()
        api.password = PASSWORD
        api.root_folders.append(str(root))
        api._setup_root(str(root))

        p1 = api.save_note(str(root), "A", "", ["stary", "wspolny"])
        p2 = api.save_note(str(root), "B", "", ["stary"])
        p3 = api.save_note(str(root), "C", "", ["inny"])
        sub = api.create_subfolder(str(root), "Sub")
        p4 = api.save_note(sub, "D", "", ["wspolny"])

        assert api.count_tag_usage("stary") == 2
        assert api.count_tag_usage("wspolny") == 2
        assert api.count_tag_usage("nieistniejacy") == 0
        print("OK  count_tag_usage")

        result = api.rename_tag("stary", "nowy")
        assert result == {"changed": 2, "merged": False}
        assert api.get_note(p1)["tags"] == ["nowy", "wspolny"]
        assert api.get_note(p2)["tags"] == ["nowy"]
        print("OK  rename_tag")

        result = api.merge_tag("nowy", "wspolny")
        assert result == {"changed": 2}
        assert set(api.get_note(p1)["tags"]) == {"wspolny"}
        assert set(api.get_note(p2)["tags"]) == {"wspolny"}
        print("OK  merge_tag")

        result = api.delete_tag("wspolny")
        assert result == {"changed": 3}  # p1, p2, p4
        assert api.get_note(p1)["tags"] == []
        assert api.get_note(p4)["tags"] == []
        assert api.get_note(p3)["tags"] == ["inny"]  # nietknięta
        print("OK  delete_tag")

        export_path = root.parent / "export.json"
        info = api.export_tags_json(str(export_path))
        assert export_path.exists()
        import json as _json
        with open(export_path, encoding="utf-8") as f:
            data = _json.load(f)
        assert set(data["tags"]) == {"inny"}
        assert data["usage_count"]["inny"] == 1
        assert data["total_files_scanned"] == 4
        assert info == {"tags_count": 1}
        export_path.unlink()
        print("OK  export_tags_json")

        # sprawdzenie że mgr.save() faktycznie leci po każdej operacji:
        # świeży IndexManager na tym samym roocie widzi zmiany BEZ reperacji
        fresh_mgr = IndexManager(str(root), password=PASSWORD)
        fresh_result = fresh_mgr.load_or_rebuild()
        assert fresh_result.new == [] and fresh_result.changed == [], (
            "zapisany na dysku indeks powinien już odzwierciedlać wszystkie zmiany"
        )
        assert len(fresh_result.unchanged) == 4
        print("OK  mgr.save() po każdej operacji - świeży IndexManager widzi bez reperacji")

        deleted = api.delete_folder(sub)
        assert deleted == {"deleted": 1}
        assert not Path(p4).exists()
        assert api.list_notes(str(root))  # reszta roota nietknięta
        assert api.get_folder_tree()[0]["children"] == []  # Sub zniknął z drzewa
        print("OK  delete_folder")

        try:
            api.delete_folder(str(root))
        except ValueError:
            pass
        else:
            raise AssertionError("delete_folder na roocie powinno rzucić ValueError")
        print("OK  delete_folder odmawia usunięcia roota")

        assert not hasattr(api, "confirm_import_tags")
        assert not hasattr(api, "preview_import_tags_json")
        assert not hasattr(api, "open_tags_dialog")
        print("OK  import tagów faktycznie usunięty (KISS)")


if __name__ == "__main__":
    main()
    test_progress_pushed_during_setup_root()
    test_bulk_tag_actions_and_delete_folder()
