"""Test S4 (zapis atomowy) + odporność IndexManagera v4 na złe pliki:
uszkodzona notatka pomijana, uszkodzony indeks odbudowywany, złe hasło
zgłaszane tylko gdy nic się nie odszyfrowało, plik v3 = komunikat o migracji."""
import os
import struct
import tempfile
from unittest import mock

from _v4 import PASSWORD, new_key

from backendV4.errors import LegacyFormatError, WrongPasswordError
from backendV4.image_note_file import ImageNoteFile
from backendV4.index_file import IndexFile
from backendV4.index_manager import IndexManager


def expect(exc, fn):
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"oczekiwano {exc.__name__}")


def note(d, key, title, **kw):
    path = os.path.join(d, ImageNoteFile.new_filename())
    ImageNoteFile(title=title, text=kw.get("text", ""), image_data=kw.get("image", b"")).save_to_file(path, key)
    return path


def test_atomic_write():
    key = new_key()
    with tempfile.TemporaryDirectory() as d:
        path = note(d, key, "Tytul", text="stary tekst")
        before = open(path, "rb").read()
        with mock.patch("os.fsync", side_effect=OSError("brak miejsca")):
            expect(OSError, lambda: ImageNoteFile(text="nowy", title="T").save_to_file(path, key))
        assert open(path, "rb").read() == before, "stary plik zmieniony"
        assert [n for n in os.listdir(d) if n.endswith(".tmp")] == [], "osierocony .tmp"
        # rewrite_meta też atomowy
        with mock.patch("os.fsync", side_effect=OSError("brak miejsca")):
            expect(OSError, lambda: ImageNoteFile.rewrite_meta(path, key, title="X", folder_path="", tags=[], text=""))
        assert open(path, "rb").read() == before
        assert ImageNoteFile.load_from_file(path, key).text == "stary tekst"
    print("OK  S4: zapis i rewrite_meta atomowe")


def test_corrupt_note_skipped_and_corrupt_index_rebuilt():
    key = new_key()
    with tempfile.TemporaryDirectory() as d:
        good = note(d, key, "Dobra")
        bad = note(d, key, "Zła")
        with open(bad, "r+b") as f:  # uszkodzona sekcja meta
            f.seek(100)
            f.write(b"\xff\xff")
        short = os.path.join(d, ImageNoteFile.new_filename())
        open(short, "wb").write(b"IMNG\x04")
        mgr = IndexManager(d, password=PASSWORD)
        mgr.load_or_rebuild()
        assert mgr.password_verified and set(mgr.index.notes) == {os.path.basename(good)}
        mgr.save()
        # uszkodzony indeks przy dobrym haśle -> odbudowa z notatek, nie "złe hasło"
        idx = os.path.join(d, IndexFile.FILENAME)
        raw = bytearray(open(idx, "rb").read())
        raw[-1] ^= 1
        open(idx, "wb").write(bytes(raw))
        mgr2 = IndexManager(d, password=PASSWORD)
        mgr2.load_or_rebuild()
        assert mgr2.password_verified and os.path.basename(good) in mgr2.index.notes
        assert mgr2.key.vault_salt == key.vault_salt  # vault_salt odkryty z notatki
    print("OK  uszkodzona notatka pominięta, uszkodzony indeks odbudowany")


def test_wrong_password_detected():
    key = new_key()
    with tempfile.TemporaryDirectory() as d:
        note(d, key, "A")
        mgr = IndexManager(d, password=PASSWORD)
        mgr.load_or_rebuild()
        mgr.save()
        expect(WrongPasswordError, lambda: IndexManager(d, password="zle").load_or_rebuild())
        os.remove(os.path.join(d, IndexFile.FILENAME))  # bez indeksu: test hasła na notatkach
        expect(WrongPasswordError, lambda: IndexManager(d, password="zle").load_or_rebuild())
    with tempfile.TemporaryDirectory() as d:
        IndexManager(d, password=PASSWORD).load_or_rebuild()  # pusty vault: brak weryfikacji
        mgr = IndexManager(d, password=PASSWORD)
        mgr.load_or_rebuild()
        assert mgr.password_verified is False
        mgr.save()
        # sam indeks, bez notatek, złe hasło -> nie wolno potraktować jak świeżego vaulta
        expect(WrongPasswordError, lambda: IndexManager(d, password="zle").load_or_rebuild())
    print("OK  złe hasło wykryte (z indeksem, bez indeksu, sam indeks)")


def test_legacy_v3_vault():
    key = new_key()
    with tempfile.TemporaryDirectory() as d:
        p = note(d, key, "A")
        raw = bytearray(open(p, "rb").read())
        raw[4] = 3
        open(p, "wb").write(bytes(raw))
        expect(LegacyFormatError, lambda: IndexManager(d, password=PASSWORD).load_or_rebuild())
    print("OK  vault v3 -> LegacyFormatError (komunikat o migracji, nie pusty vault)")


def test_orphan_tmp_cleanup():
    with tempfile.TemporaryDirectory() as d:
        tmp = os.path.join(d, "." + "a" * 32 + ".tmp")
        open(tmp, "wb").write(b"x")
        os.utime(tmp, (1, 1))
        fresh = os.path.join(d, "." + "b" * 32 + ".tmp")
        open(fresh, "wb").write(b"x")
        IndexManager(d, password=PASSWORD).load_or_rebuild()
        assert not os.path.exists(tmp) and os.path.exists(fresh)
    print("OK  stare .tmp sprzątane, świeże (zapis w toku) zostają")


def main():
    test_atomic_write()
    test_corrupt_note_skipped_and_corrupt_index_rebuilt()
    test_wrong_password_detected()
    test_legacy_v3_vault()
    test_orphan_tmp_cleanup()
    print("OK test_atomic_and_header")


if __name__ == "__main__":
    main()
