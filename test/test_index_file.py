"""
test_index_file.py — indeks .imgidx v2 (FORMAT_V4.md): roundtrip, P3 (zbiory
tagów, usuwanie dotyka tylko tagów notatki), manipulacje, stary indeks v1.

Uruchomienie:
    uv run python test/test_index_file.py
"""
import os
import tempfile
from pathlib import Path

from _v4 import FAST, PASSWORD, new_key

from backendV4.errors import WrongPasswordError
from backendV4.image_note_file import ImageNoteFile
from backendV4.index_file import IndexFile
from backendV4.vault_key import KeyRing, VaultKey


def expect(exc, fn, *a):
    try:
        fn(*a)
    except exc:
        return
    raise AssertionError(f"{fn.__name__} nie rzucił {exc.__name__}")


def test_roundtrip_and_vault_params():
    key = new_key()
    idx = IndexFile()
    idx.set_note("a.imgnote", "Tytuł A", "Klienci/X", 111.0, ["x", "y"])
    idx.set_note("b.imgnote", "Tytuł B", "", 222.0, ["y"])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / IndexFile.FILENAME
        idx.save_to_file(str(path), key)
        loaded = IndexFile.load_from_file(str(path), VaultKey(KeyRing(PASSWORD), os.urandom(16), FAST))
        assert loaded.notes == idx.notes
        assert loaded.tag_index == {"x": {"a.imgnote"}, "y": {"a.imgnote", "b.imgnote"}}
        assert (loaded.vault_salt, loaded.params) == (key.vault_salt, FAST)  # vault_salt z nagłówka
    print("OK  roundtrip + vault_salt/parametry z nagłówka")


def test_wrong_password_and_tampering():
    key = new_key()
    idx = IndexFile()
    idx.set_note("a.imgnote", "X", "", 1.0, [])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / IndexFile.FILENAME
        idx.save_to_file(str(path), key)
        expect(WrongPasswordError, IndexFile.load_from_file, str(path), VaultKey(KeyRing("zle"), key.vault_salt, FAST))
        raw = bytearray(path.read_bytes())
        raw[5] ^= 1  # flags w prefiksie (AAD)
        path.write_bytes(bytes(raw))
        expect(WrongPasswordError, IndexFile.load_from_file, str(path), key)
        path.write_bytes(bytes(raw[:-1]))
        expect(ValueError, IndexFile.load_from_file, str(path), key)
    print("OK  złe hasło i manipulacje wykryte")


def test_old_v1_index_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / IndexFile.FILENAME
        path.write_bytes(b"IMIX\x01" + b"\x00" * 60)
        expect(ValueError, IndexFile.read_header, str(path))
        path.write_bytes(b"IMNG\x04" + b"\x00" * 60)  # notatka zamiast indeksu
        expect(ValueError, IndexFile.read_header, str(path))
    print("OK  stary indeks v1 / zły magic -> ValueError (odbudowa)")


def test_set_note_rewrites_tags():
    idx = IndexFile()
    idx.set_note("a.imgnote", "A", "", 1.0, ["x", "y", "x"])
    assert idx.notes["a.imgnote"]["tags"] == ["x", "y"]  # bez duplikatów
    idx.set_note("a.imgnote", "A", "", 2.0, ["y"])
    assert idx.tag_index == {"y": {"a.imgnote"}}
    print("OK  set_note usuwa stare tagi i duplikaty")


def test_remove_note_touches_only_its_tags():
    idx = IndexFile()
    idx.set_note("a.imgnote", "A", "", 1.0, ["x"])
    idx.set_note("b.imgnote", "B", "", 1.0, ["x", "z"])
    idx.remove_note("b.imgnote")
    assert idx.tag_index == {"x": {"a.imgnote"}}  # pusty "z" usunięty
    idx.remove_note("nieistniejacy.imgnote")  # bez wyjątku
    print("OK  remove_note: tylko tagi tej notatki, puste klucze znikają")


def test_save_requires_key():
    expect(TypeError, IndexFile().save_to_file, "/tmp/x.imgidx", "haslo")
    print("OK  zapis wymaga VaultKey")


def test_filename_does_not_collide_with_note_glob():
    assert not IndexFile.FILENAME.endswith(".imgnote")
    assert ImageNoteFile.new_filename().endswith(".imgnote")
    print("OK  nazwa indeksu nie łapie się na *.imgnote")


def main():
    test_roundtrip_and_vault_params()
    test_wrong_password_and_tampering()
    test_old_v1_index_rejected()
    test_set_note_rewrites_tags()
    test_remove_note_touches_only_its_tags()
    test_save_requires_key()
    test_filename_does_not_collide_with_note_glob()
    print("\nWszystkie testy przeszły.")


if __name__ == "__main__":
    main()
