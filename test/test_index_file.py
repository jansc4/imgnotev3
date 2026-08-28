"""
test_index_file.py — testy formatu backendV4/index_file.py.

Uruchomienie:
    uv run python test/test_index_file.py
"""
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backendV4.index_file import IndexFile
from backendV4.image_note_file import ImageNoteFile
from backendV4.errors import WrongPasswordError


def test_roundtrip_encrypted():
    idx = IndexFile()
    idx.set_note("a.imgnote", "Tytuł A", "Klienci/X", 111.0, ["x", "y"])
    idx.set_note("b.imgnote", "Tytuł B", "", 222.0, ["y"])

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / IndexFile.FILENAME
        idx.save_to_file(str(path), password="haslo123")

        loaded = IndexFile.load_from_file(str(path), password="haslo123")

        assert loaded.notes == idx.notes
        assert loaded.tag_index == idx.tag_index
    print("OK  test_roundtrip_encrypted")


def test_roundtrip_plaintext():
    idx = IndexFile()
    idx.set_note("a.imgnote", "Tytuł A", "", 1.0, [])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / IndexFile.FILENAME
        idx.save_to_file(str(path), password=None)
        loaded = IndexFile.load_from_file(str(path), password=None)
        assert loaded.notes == idx.notes
    print("OK  test_roundtrip_plaintext")


def test_wrong_password_fails():
    idx = IndexFile()
    idx.set_note("a.imgnote", "X", "", 1.0, [])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / IndexFile.FILENAME
        idx.save_to_file(str(path), password="dobre")
        try:
            IndexFile.load_from_file(str(path), password="zle")
        except WrongPasswordError:
            pass
        else:
            raise AssertionError("złe hasło powinno rzucić WrongPasswordError")
    print("OK  test_wrong_password_fails")


def test_set_note_updates_tags_cleanly():
    """Zmiana tagów notatki -> stary tag znika z tag_index, jeśli nikt
    inny go nie używa; nowy tag się pojawia."""
    idx = IndexFile()
    idx.set_note("a.imgnote", "X", "", 1.0, ["stary"])
    assert idx.tag_index == {"stary": ["a.imgnote"]}

    idx.set_note("a.imgnote", "X", "", 2.0, ["nowy"])
    assert "stary" not in idx.tag_index, "pusta lista po usunięciu powinna zniknąć z tag_index"
    assert idx.tag_index == {"nowy": ["a.imgnote"]}
    print("OK  test_set_note_updates_tags_cleanly")


def test_remove_note_cleans_tag_index():
    idx = IndexFile()
    idx.set_note("a.imgnote", "X", "", 1.0, ["wspolny"])
    idx.set_note("b.imgnote", "Y", "", 2.0, ["wspolny"])

    idx.remove_note("a.imgnote")
    assert "a.imgnote" not in idx.notes
    assert idx.tag_index == {"wspolny": ["b.imgnote"]}, "b.imgnote nadal używa taga, tag zostaje"

    idx.remove_note("b.imgnote")
    assert idx.tag_index == {}, "ostatnia notatka z tagiem usunięta -> tag znika"
    print("OK  test_remove_note_cleans_tag_index")


def test_v2_wrong_version_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / IndexFile.FILENAME
        fake_header = struct.pack(
            IndexFile.HEADER_FORMAT,
            IndexFile.MAGIC, 99, 0, 0, 0, 0, 0,
        )
        path.write_bytes(fake_header)
        try:
            IndexFile.load_from_file(str(path))
        except ValueError as e:
            assert "VERSION" in str(e)
        else:
            raise AssertionError("nieznana wersja powinna zostać odrzucona")
    print("OK  test_v2_wrong_version_rejected")


def test_magic_mismatch_rejects_note_file():
    """Próba otwarcia zwykłej notatki .imgnote jako indeksu ma się jawnie
    wywalić na MAGIC, nie próbować deszyfrować jej jako indeks."""
    note = ImageNoteFile(text="nie jestem indeksem", title="X")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ImageNoteFile.new_filename()
        note.save_to_file(str(path), password="haslo")
        try:
            IndexFile.load_from_file(str(path), password="haslo")
        except ValueError as e:
            assert "magic" in str(e).lower()
        else:
            raise AssertionError("plik .imgnote nie powinien wczytać się jako indeks")
    print("OK  test_magic_mismatch_rejects_note_file")


def main():
    test_roundtrip_encrypted()
    test_roundtrip_plaintext()
    test_wrong_password_fails()
    test_set_note_updates_tags_cleanly()
    test_remove_note_cleans_tag_index()
    test_v2_wrong_version_rejected()
    test_magic_mismatch_rejects_note_file()
    print("\nWszystkie testy przeszły.")


if __name__ == "__main__":
    main()
