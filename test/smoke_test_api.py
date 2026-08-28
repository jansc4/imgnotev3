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


def test_count_notes_in_folder():
    """count_notes_in_folder (krok 8, punkt 4) — musi zgadzać się z tym,
    co faktycznie usunąłby delete_folder, i NIE dotykać dysku."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        api = Api()
        api.password = PASSWORD
        api.root_folders.append(str(root))
        api._setup_root(str(root))

        sub = api.create_subfolder(str(root), "Sub")
        subsub = api.create_subfolder(sub, "SubSub")
        api.save_note(str(root), "A", "", [])
        api.save_note(sub, "B", "", [])
        api.save_note(sub, "C", "", [])
        api.save_note(subsub, "D", "", [])

        assert api.count_notes_in_folder(str(root)) == 4, "root: wszystkie 4 notatki"
        assert api.count_notes_in_folder(sub) == 3, "sub: B, C + D z subsub"
        assert api.count_notes_in_folder(subsub) == 1
        print("OK  count_notes_in_folder liczy siebie + podfoldery")

        # count nie usuwa NIC — delete_folder zaraz potem powinien zwrócić
        # dokładnie tyle samo, ile zapowiedział count.
        expected = api.count_notes_in_folder(sub)
        result = api.delete_folder(sub)
        assert result == {"deleted": expected}, "count_notes_in_folder musi się zgadzać z delete_folder"
        print("OK  count_notes_in_folder zgadza się z realnym delete_folder")


def test_two_phase_login_fresh_vault():
    """Świeży, pusty root: login() NIE zapisuje nic na dysk i zwraca
    password_verified=False; dopiero confirm_new_vault_password() z
    poprawnym powtórzeniem hasła faktycznie tworzy plik indeksu."""
    from backendV4.index_file import IndexFile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        index_path = root / IndexFile.FILENAME
        api = Api()

        result = api.login(str(root), PASSWORD)
        assert result == {"password_verified": False}, "pusty root: nic do zweryfikowania"
        assert not index_path.exists(), "login() na świeżym vaulcie nie może niczego zapisać"
        assert str(root) not in api.root_folders, "root_folders dopiero po potwierdzeniu"
        print("OK  login() na świeżym vaulcie: nic nie zapisuje, zwraca password_verified=False")

        try:
            api.confirm_new_vault_password(str(root), "zle_haslo")
        except ValueError:
            pass
        else:
            raise AssertionError("niezgodne hasło powinno rzucić ValueError")
        assert not index_path.exists(), "błędne potwierdzenie nadal nie może niczego zapisać"
        print("OK  confirm_new_vault_password odrzuca niezgodne hasło, nic nie zapisując")

        ok = api.confirm_new_vault_password(str(root), PASSWORD)
        assert ok is True
        assert index_path.exists(), "poprawne potwierdzenie musi zapisać indeks"
        assert str(root) in api.root_folders
        print("OK  confirm_new_vault_password z poprawnym hasłem zapisuje indeks")


def test_two_phase_login_existing_vault():
    """Vault z istniejącym indeksem: login() weryfikuje hasło od razu
    (próbą odszyfrowania) i zapisuje bez żadnego potwierdzenia — tak jak
    dawniej, jednofazowo."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bootstrap = Api()
        bootstrap.password = PASSWORD
        bootstrap._setup_root(str(root))  # tworzy realny, zapisany indeks

        api = Api()
        result = api.login(str(root), PASSWORD)
        assert result == {"password_verified": True}
        assert str(root) in api.root_folders
        print("OK  login() na istniejącym vaulcie: od razu zweryfikowane i zapisane")

        api_wrong = Api()
        try:
            api_wrong.login(str(root), "zupelnie_inne_haslo")
        except Exception:
            pass  # złe hasło do istniejącego indeksu wywala się przy odszyfrowaniu - oczekiwane
        else:
            raise AssertionError("złe hasło do istniejącego indeksu powinno rzucić wyjątek")
        print("OK  login() ze złym hasłem do istniejącego vaulta rzuca wyjątek")


def test_rename_move_folder_and_note():
    """rename_folder/move_folder/move_note (runda folder rename/move,
    2026-08-28) — na realnych plikach, w tym pusty (pending) podfolder,
    żeby sprawdzić że nie znika po zmianie ścieżki."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        api = Api()
        api.password = PASSWORD
        api.root_folders.append(str(root))
        api._setup_root(str(root))

        klienci = api.create_subfolder(str(root), "Klienci")
        p1 = api.save_note(klienci, "A", "treść A", ["x"])
        firma_x = api.create_subfolder(klienci, "FirmaX")
        p2 = api.save_note(firma_x, "B", "treść B", ["y"])
        pusty = api.create_subfolder(klienci, "PustyPodfolder")  # pending, bez notatki

        # --- rename_folder: "Klienci" -> "Kontrahenci" (ten sam rodzic) ---
        new_klienci = api.rename_folder(klienci, "Kontrahenci")
        assert new_klienci == f"{root}/Kontrahenci"
        assert api.get_note(p1)["folder"] == new_klienci
        assert api.get_note(p2)["folder"] == f"{new_klienci}/FirmaX", (
            "podfolder FirmaX musi wędrować razem z rodzicem"
        )
        print("OK  rename_folder przenosi notatki w sobie i w podfolderach")

        tree = api.get_folder_tree()
        names_at_root = {c["name"] for c in tree[0]["children"]}
        assert "Kontrahenci" in names_at_root and "Klienci" not in names_at_root
        kontrahenci_node = next(c for c in tree[0]["children"] if c["name"] == "Kontrahenci")
        child_names = {c["name"] for c in kontrahenci_node["children"]}
        assert "PustyPodfolder" in child_names, (
            "pusty (pending) podfolder nie może zniknąć z drzewa po rename rodzica"
        )
        print("OK  rename_folder aktualizuje też pending (pusty podfolder)")

        # --- move_folder: "FirmaX" spod Kontrahenci -> bezpośrednio pod root ---
        firma_x_new = f"{new_klienci}/FirmaX"
        moved = api.move_folder(firma_x_new, str(root))
        assert moved == f"{root}/FirmaX"
        assert api.get_note(p2)["folder"] == moved
        tree = api.get_folder_tree()
        root_children = {c["name"] for c in tree[0]["children"]}
        assert "FirmaX" in root_children
        kontrahenci_node = next(c for c in tree[0]["children"] if c["name"] == "Kontrahenci")
        assert not any(c["name"] == "FirmaX" for c in kontrahenci_node["children"])
        print("OK  move_folder przenosi pod nowego rodzica")

        try:
            api.move_folder(new_klienci, f"{new_klienci}/PustyPodfolder")
        except ValueError:
            pass
        else:
            raise AssertionError("przeniesienie folderu do własnego podfolderu powinno rzucić ValueError")
        print("OK  move_folder odrzuca przeniesienie do samego siebie/podfolderu")

        try:
            api.rename_folder(str(root), "CośInnego")
        except ValueError:
            pass
        else:
            raise AssertionError("rename_folder na roocie powinno rzucić ValueError")
        print("OK  rename_folder odmawia zmiany nazwy roota")

        # --- move_note: pojedyncza notatka do innego folderu ---
        api.move_note(p1, moved)  # p1 ("A") z Kontrahenci -> FirmaX
        assert api.get_note(p1)["folder"] == moved
        assert api.list_notes(new_klienci) == []
        assert len(api.list_notes(moved)) == 2
        print("OK  move_note przenosi pojedynczą notatkę")


if __name__ == "__main__":
    main()
    test_progress_pushed_during_setup_root()
    test_bulk_tag_actions_and_delete_folder()
    test_count_notes_in_folder()
    test_two_phase_login_fresh_vault()
    test_two_phase_login_existing_vault()
    test_rename_move_folder_and_note()
