"""
test_index_manager.py — testy backendV4/index_manager.py na prawdziwych
tymczasowych katalogach z syntetycznymi plikami .imgnote v3. Realne I/O
celowo — IndexManager i tak robi realne os.scandir/os.path.getmtime,
więc mockowanie tego dawałoby fałszywą pewność.

Uruchomienie:
    uv run python test/test_index_manager.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backendV4.image_note_file import ImageNoteFile
from backendV4.index_manager import IndexManager
from backendV4.note_cache import NoteCache
from backendV4.errors import WrongPasswordError

PASSWORD = "haslo123"


def _make_note(root: Path, title: str, folder_path: str, tags=None) -> Path:
    note = ImageNoteFile(text="", title=title, folder_path=folder_path, tags=tags or [])
    path = root / ImageNoteFile.new_filename()
    note.save_to_file(str(path), password=PASSWORD)
    return path


def test_rebuild_from_scratch_and_persists():
    """Pusty root -> dwie notatki -> pełny rebuild -> save() -> świeży
    manager na tym samym katalogu wczytuje zapisany indeks (nie rebuilduje)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_note(root, "A", "Klienci/X", tags=["a"])
        _make_note(root, "B", "", tags=["b"])

        mgr = IndexManager(str(root), password=PASSWORD)
        result = mgr.load_or_rebuild()
        assert result.is_full_rebuild is True
        assert len(mgr.index.notes) == 2
        assert {v["title"] for v in mgr.index.notes.values()} == {"A", "B"}
        mgr.save()

        mgr2 = IndexManager(str(root), password=PASSWORD)
        result2 = mgr2.load_or_rebuild()
        assert result2.is_full_rebuild is False
        assert set(result2.unchanged) == set(mgr.index.notes.keys())
        assert mgr2.index.notes == mgr.index.notes
    print("OK  test_rebuild_from_scratch_and_persists")


def test_detects_new_file_added_externally():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_note(root, "A", "", tags=[])

        mgr = IndexManager(str(root), password=PASSWORD)
        mgr.load_or_rebuild()
        mgr.save()

        _make_note(root, "B", "", tags=[])  # dodany PO zapisie indeksu

        mgr2 = IndexManager(str(root), password=PASSWORD)
        result = mgr2.load_or_rebuild()
        assert len(result.new) == 1
        assert len(result.unchanged) == 1
        assert len(mgr2.index.notes) == 2
    print("OK  test_detects_new_file_added_externally")


def test_detects_file_deleted_externally():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path_a = _make_note(root, "A", "", tags=[])
        _make_note(root, "B", "", tags=[])

        mgr = IndexManager(str(root), password=PASSWORD)
        mgr.load_or_rebuild()
        mgr.save()

        os.remove(path_a)  # symulacja usunięcia pliku poza appką

        mgr2 = IndexManager(str(root), password=PASSWORD)
        result = mgr2.load_or_rebuild()
        assert len(result.orphaned) == 1
        assert len(mgr2.index.notes) == 1
    print("OK  test_detects_file_deleted_externally")


def test_detects_modified_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _make_note(root, "Stary tytuł", "", tags=["stary"])

        mgr = IndexManager(str(root), password=PASSWORD)
        mgr.load_or_rebuild()
        mgr.save()
        old_mtime = os.path.getmtime(path)

        edited = ImageNoteFile(text="", title="Nowy tytuł", folder_path="", tags=["nowy"])
        edited.save_to_file(str(path), password=PASSWORD)
        # gwarancja różnicy mtime niezależnie od rozdzielczości zegara fs
        os.utime(path, (old_mtime + 10, old_mtime + 10))

        mgr2 = IndexManager(str(root), password=PASSWORD)
        result = mgr2.load_or_rebuild()
        assert len(result.changed) == 1
        filename = os.path.basename(path)
        assert mgr2.index.notes[filename]["title"] == "Nowy tytuł"
    print("OK  test_detects_modified_file")


def test_note_saved_and_deleted_convenience():
    """note_saved()/note_deleted() - ścieżka runtime (bez load_or_rebuild),
    tak jak przyszłe api.py.save_note() będzie to wołać po zapisie pliku."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mgr = IndexManager(str(root), password=PASSWORD)
        mgr.load_or_rebuild()

        note = ImageNoteFile(text="x", title="Ręcznie", folder_path="", tags=["r"])
        path = root / ImageNoteFile.new_filename()
        note.save_to_file(str(path), password=PASSWORD)
        mgr.note_saved(str(path), note)

        filename = os.path.basename(path)
        assert filename in mgr.index.notes
        assert mgr.index.tag_index == {"r": [filename]}

        mgr.note_deleted(str(path))
        assert filename not in mgr.index.notes
        assert mgr.index.tag_index == {}
    print("OK  test_note_saved_and_deleted_convenience")


def test_empty_root_folder():
    with tempfile.TemporaryDirectory() as tmp:
        mgr = IndexManager(tmp, password=PASSWORD)
        result = mgr.load_or_rebuild()
        assert mgr.index.notes == {}
        assert result.new == [] and result.orphaned == [] and result.changed == []
    print("OK  test_empty_root_folder")


def test_wrong_password_on_existing_index_raises():
    """Tani test hasła z flow startu: złe hasło do już-istniejącego
    indeksu ma wywalić się na wczytaniu indeksu, nie próbować dalej."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_note(root, "A", "", tags=[])
        mgr = IndexManager(str(root), password=PASSWORD)
        mgr.load_or_rebuild()
        mgr.save()

        mgr2 = IndexManager(str(root), password="zle_haslo")
        try:
            mgr2.load_or_rebuild()
        except WrongPasswordError:
            pass
        else:
            raise AssertionError("złe hasło powinno rzucić WrongPasswordError")
    print("OK  test_wrong_password_on_existing_index_raises")


def test_wrong_password_notes_only_no_index_raises():
    """To samo, ale bez indeksu na dysku — walidacja hasła dzieje się
    przy próbie odszyfrowania pierwszej notatki w pętli reperacji,
    zamiast na tanim teście indeksu (bo indeksu nie ma)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_note(root, "A", "", tags=[])

        mgr = IndexManager(str(root), password="zle_haslo")
        try:
            mgr.load_or_rebuild()
        except WrongPasswordError:
            pass
        else:
            raise AssertionError("złe hasło powinno rzucić WrongPasswordError")
    print("OK  test_wrong_password_notes_only_no_index_raises")


def test_password_verified_true_when_index_exists():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_note(root, "A", "", tags=[])
        mgr = IndexManager(str(root), password=PASSWORD)
        mgr.load_or_rebuild()
        mgr.save()

        mgr2 = IndexManager(str(root), password=PASSWORD)
        assert mgr2.password_verified is False  # jeszcze nic nie zrobił
        mgr2.load_or_rebuild()
        assert mgr2.password_verified is True  # indeks się odszyfrował
    print("OK  test_password_verified_true_when_index_exists")


def test_password_verified_true_when_notes_exist_but_no_index():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_note(root, "A", "", tags=[])  # notatka jest, indeksu nie ma

        mgr = IndexManager(str(root), password=PASSWORD)
        mgr.load_or_rebuild()
        assert mgr.password_verified is True  # zweryfikowane przy deszyfrowaniu notatki
    print("OK  test_password_verified_true_when_notes_exist_but_no_index")


def test_password_verified_false_on_fresh_empty_vault():
    """Sedno zgłoszonego problemu: zupełnie pusty katalog -> backend nie
    ma z czym porównać hasła, więc password_verified zostaje False. To
    jest sygnał dla przyszłego ekranu logowania, żeby zażądać dwóch pól
    (hasło + powtórz hasło), zamiast ufać jednemu bez weryfikacji."""
    with tempfile.TemporaryDirectory() as tmp:
        mgr = IndexManager(tmp, password="cokolwiek")
        mgr.load_or_rebuild()
        assert mgr.password_verified is False
    print("OK  test_password_verified_false_on_fresh_empty_vault")


def test_on_progress_called_for_each_decrypted_file():
    """on_progress(current, total) woła się raz na plik w changed+new,
    z narastającym current i stałym total — nie dla unchanged/orphaned."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_note(root, "A", "", tags=[])
        _make_note(root, "B", "", tags=[])

        calls = []
        mgr = IndexManager(str(root), password=PASSWORD, on_progress=lambda c, t: calls.append((c, t)))
        mgr.load_or_rebuild()

        assert calls == [(1, 2), (2, 2)]
    print("OK  test_on_progress_called_for_each_decrypted_file")


def test_on_progress_not_called_when_nothing_to_decrypt():
    """Pusty vault -> zero plików do odszyfrowania -> callback w ogóle
    nie wołany (total=0, pętla się nie wykonuje)."""
    with tempfile.TemporaryDirectory() as tmp:
        calls = []
        mgr = IndexManager(tmp, password=PASSWORD, on_progress=lambda c, t: calls.append((c, t)))
        mgr.load_or_rebuild()
        assert calls == []
    print("OK  test_on_progress_not_called_when_nothing_to_decrypt")


def test_load_or_rebuild_populates_note_cache_for_free():
    """changed/new podczas reperacji trafiają do note_cache bez drugiego
    deszyfrowania — dowód: get_full ze ZŁYM hasłem po rebuildzie i tak
    zwraca dane, bo w ogóle nie sięga do ImageNoteFile.load_from_file."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _make_note(root, "A", "", tags=["x"])

        cache = NoteCache()
        mgr = IndexManager(str(root), password=PASSWORD, note_cache=cache)
        mgr.load_or_rebuild()

        mtime = os.path.getmtime(path)
        result = cache.get_full(str(path), "zle_haslo", mtime)  # nie powinno się wywalić
        assert result.text == ""
        assert result.tags == ["x"]
    print("OK  test_load_or_rebuild_populates_note_cache_for_free")


def test_load_or_rebuild_does_not_prepopulate_unchanged_files():
    """Selektywność: pliki, które NIE zostały odszyfrowane podczas
    reperacji (mtime się zgadzał), nie dostają darmowej hydratacji —
    tylko changed/new. Tu: drugi rebuild na niezmienionym pliku -> cache
    wciąż pusty dla tego pliku, złe hasło faktycznie się wywala."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _make_note(root, "A", "", tags=[])

        cache = NoteCache()
        mgr = IndexManager(str(root), password=PASSWORD, note_cache=cache)
        mgr.load_or_rebuild()  # pierwszy rebuild: A trafia do cache (to jest "new")
        mgr.save()

        cache2 = NoteCache()  # świeży, pusty cache
        mgr2 = IndexManager(str(root), password=PASSWORD, note_cache=cache2)
        mgr2.load_or_rebuild()  # A jest "unchanged" (indeks już go zna) -> bez darmowej hydratacji

        mtime = os.path.getmtime(path)
        try:
            cache2.get_full(str(path), "zle_haslo", mtime)
        except WrongPasswordError:
            pass
        else:
            raise AssertionError("unchanged nie powinno być pre-hydratowane w cache2")
    print("OK  test_load_or_rebuild_does_not_prepopulate_unchanged_files")


def test_note_saved_populates_note_cache():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = NoteCache()
        mgr = IndexManager(str(root), password=PASSWORD, note_cache=cache)
        mgr.load_or_rebuild()

        note = ImageNoteFile(text="świeżo", title="Nowa", folder_path="", tags=["n"])
        path = root / ImageNoteFile.new_filename()
        note.save_to_file(str(path), password=PASSWORD)
        mgr.note_saved(str(path), note)

        mtime = os.path.getmtime(path)
        result = cache.get_full(str(path), "zle_haslo", mtime)  # z cache, nie z dysku
        assert result.text == "świeżo"
    print("OK  test_note_saved_populates_note_cache")


def test_note_deleted_invalidates_note_cache():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = NoteCache()
        mgr = IndexManager(str(root), password=PASSWORD, note_cache=cache)
        mgr.load_or_rebuild()

        note = ImageNoteFile(text="do usunięcia", title="X", folder_path="", tags=[])
        path = root / ImageNoteFile.new_filename()
        note.save_to_file(str(path), password=PASSWORD)
        mgr.note_saved(str(path), note)

        mgr.note_deleted(str(path))
        assert str(path) not in cache._cache
    print("OK  test_note_deleted_invalidates_note_cache")


def main():
    test_rebuild_from_scratch_and_persists()
    test_detects_new_file_added_externally()
    test_detects_file_deleted_externally()
    test_detects_modified_file()
    test_note_saved_and_deleted_convenience()
    test_empty_root_folder()
    test_wrong_password_on_existing_index_raises()
    test_wrong_password_notes_only_no_index_raises()
    test_password_verified_true_when_index_exists()
    test_password_verified_true_when_notes_exist_but_no_index()
    test_password_verified_false_on_fresh_empty_vault()
    test_on_progress_called_for_each_decrypted_file()
    test_on_progress_not_called_when_nothing_to_decrypt()
    test_load_or_rebuild_populates_note_cache_for_free()
    test_load_or_rebuild_does_not_prepopulate_unchanged_files()
    test_note_saved_populates_note_cache()
    test_note_deleted_invalidates_note_cache()
    print("\nWszystkie testy przeszły.")


if __name__ == "__main__":
    main()
