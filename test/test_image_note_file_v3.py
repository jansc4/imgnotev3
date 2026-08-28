"""
test_image_note_file_v3.py — ręczny test roundtrip dla backendV4/image_note_file.py.

Nie pytest — proste funkcje z `assert`, wołane po kolei z main(). Każdy
`.imgnote` tworzony w trakcie testu żyje w tymczasowym katalogu
(`tempfile.TemporaryDirectory`) i znika zaraz po teście — folder `test/`
ma zawierać tylko ten skrypt, żadnych artefaktów po uruchomieniu.

Uruchomienie (z korzenia repo):
    uv run python test/test_image_note_file_v3.py
"""
import struct
import sys
import tempfile
from pathlib import Path

# korzeń repo na sys.path, niezależnie skąd skrypt jest odpalony
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backendV4.image_note_file import ImageNoteFile
from backendV4.errors import WrongPasswordError


def test_roundtrip_encrypted():
    """Zapis z hasłem -> odczyt tym samym hasłem -> wszystkie pola się zgadzają."""
    note = ImageNoteFile(
        text="Testowa notatka",
        image_data=b"\x89PNG\r\n fake bytes",
        tags=["tag1", "tag2"],
        title="Moja notatka",
        folder_path="Klienci/X/Umowy",
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ImageNoteFile.new_filename()
        note.save_to_file(str(path), password="haslo123")

        loaded = ImageNoteFile.load_from_file(str(path), password="haslo123")

        assert loaded.text == note.text
        assert loaded.image_data == note.image_data
        assert loaded.tags == note.tags
        assert loaded.title == note.title
        assert loaded.folder_path == note.folder_path
        assert loaded.encrypted is True
    print("OK  test_roundtrip_encrypted")


def test_wrong_password_fails():
    """Złe hasło = AES-GCM nie zweryfikuje taga -> wyjątek, nie śmieciowe dane."""
    note = ImageNoteFile(text="tajne", title="X", folder_path="")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ImageNoteFile.new_filename()
        note.save_to_file(str(path), password="dobre_haslo")

        try:
            ImageNoteFile.load_from_file(str(path), password="zle_haslo")
        except WrongPasswordError:
            pass
        else:
            raise AssertionError("złe hasło powinno rzucić WrongPasswordError")
    print("OK  test_wrong_password_fails")


def test_roundtrip_plaintext():
    """Zapis bez hasła (flags=0x00) -> odczyt bez hasła."""
    note = ImageNoteFile(
        text="bez szyfrowania", tags=["a"], title="Plaintext", folder_path="Foo",
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ImageNoteFile.new_filename()
        note.save_to_file(str(path), password=None)

        loaded = ImageNoteFile.load_from_file(str(path), password=None)

        assert loaded.text == note.text
        assert loaded.title == note.title
        assert loaded.folder_path == note.folder_path
        assert loaded.encrypted is False
    print("OK  test_roundtrip_plaintext")


def test_folder_path_normalization():
    """Różne warianty wejścia -> ten sam znormalizowany string."""
    variants = ["A/B", "/A/B/", "A//B", "\\A\\B", "A/B/"]
    results = {ImageNoteFile(folder_path=v).folder_path for v in variants}
    assert results == {"A/B"}, f"oczekiwano jednego wariantu, dostałem: {results}"

    assert ImageNoteFile(folder_path="").folder_path == ""
    assert ImageNoteFile(folder_path="/").folder_path == ""
    print("OK  test_folder_path_normalization")


def test_v2_file_rejected():
    """Plik z VERSION=2 (stary format) ma być jawnie odrzucony, nie po cichu źle odczytany."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "stary.imgnote"
        # ręcznie sklejony nagłówek v2: magic OK, version=2, reszta bez znaczenia
        fake_header = struct.pack(
            ImageNoteFile.HEADER_FORMAT,
            ImageNoteFile.MAGIC, 2, 0, 0, 0, 0, 0,
        )
        path.write_bytes(fake_header)

        try:
            ImageNoteFile.load_from_file(str(path))
        except ValueError as e:
            assert "VERSION" in str(e)
        else:
            raise AssertionError("plik VERSION=2 powinien zostać odrzucony")
    print("OK  test_v2_file_rejected")


def main():
    test_roundtrip_encrypted()
    test_wrong_password_fails()
    test_roundtrip_plaintext()
    test_folder_path_normalization()
    test_v2_file_rejected()
    print("\nWszystkie testy przeszły.")


if __name__ == "__main__":
    main()
