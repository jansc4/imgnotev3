"""
test_image_note_file_v4.py — format .imgnote v4 (FORMAT_V4.md): roundtrip,
odczyty częściowe, rewrite_meta, odporność na manipulacje i złe dane.

Uruchomienie:  uv run python test/test_image_note_file_v4.py
"""
import io
import os
import struct
import tempfile

from _v4 import FAST, PASSWORD, new_key
from PIL import Image

from backendV4.errors import LegacyFormatError, WrongPasswordError
from backendV4.image_note_file import ImageNoteFile, _Header
from backendV4.vault_key import KdfParams, KeyRing, VaultKey


def png(w=640, h=480, alpha=False) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA" if alpha else "RGB", (w, h), (10, 120, 200, 128) if alpha else (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def expect(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return
    raise AssertionError(f"{fn.__name__} nie rzucił {exc.__name__}")


def write(tmp, key, **kw) -> str:
    path = os.path.join(tmp, ImageNoteFile.new_filename())
    ImageNoteFile(**kw).save_to_file(path, key)
    return path


def section_bounds(path, idx):
    with open(path, "rb") as f:
        data = f.read()
    entries = [struct.unpack_from("<12sI", data, 48 + i * 16) for i in range(3)]
    start = 96 + sum(length for _, length in entries[:idx])
    return data, start, start + entries[idx][1]


def flip(path, offset):
    with open(path, "r+b") as f:
        f.seek(offset)
        b = f.read(1)
        f.seek(offset)
        f.write(bytes([b[0] ^ 0x01]))


def test_roundtrip_and_partial_reads():
    key, img = new_key(), png()
    with tempfile.TemporaryDirectory() as tmp:
        p = write(tmp, key, text="Treść **md**", image_data=img, tags=["a", "b"], title="Tytuł", folder_path="/X/Y/")
        full = ImageNoteFile.load_from_file(p, key)
        assert (full.title, full.folder_path, full.text, full.tags) == ("Tytuł", "X/Y", "Treść **md**", ["a", "b"])
        assert full.image_data == img and full.image_size == len(img)
        assert full.thumbnail_data[:4] == b"RIFF" and full.thumbnail_data[8:12] == b"WEBP"
        meta = ImageNoteFile.load_meta(p, key)
        assert meta.meta_only and meta.image_data == b"" and meta.image_size == len(img) and meta.title == "Tytuł"
        assert ImageNoteFile.load_thumbnail(p, key) == full.thumbnail_data
        assert ImageNoteFile.peek_vault(p) == (key.vault_salt, FAST)
        # notatka bez obrazu: puste sekcje, brak miniatury
        p2 = write(tmp, key, text="sam tekst", title="T")
        assert ImageNoteFile.load_thumbnail(p2, key) is None
        assert ImageNoteFile.load_from_file(p2, key).image_data == b""
    print("OK  roundtrip + odczyty częściowe")


def test_thumbnail_webp_keeps_alpha():
    key = new_key()
    with tempfile.TemporaryDirectory() as tmp:
        p = write(tmp, key, image_data=png(800, 400, alpha=True), title="A")
        thumb = Image.open(io.BytesIO(ImageNoteFile.load_thumbnail(p, key)))
        assert thumb.format == "WEBP" and thumb.mode == "RGBA" and max(thumb.size) == 320
    print("OK  miniatura WebP z alfą, 320 px")


def test_rewrite_meta_copies_image_bytes():
    key, img = new_key(), png()
    with tempfile.TemporaryDirectory() as tmp:
        p = write(tmp, key, text="stary", image_data=img, tags=["x"], title="T", folder_path="F")
        before_img = section_bounds(p, 2)
        before_thumb = section_bounds(p, 1)
        note = ImageNoteFile.rewrite_meta(p, key, title="Nowy", folder_path="G/H", tags=["y", "z"], text="nowy")
        assert note.meta_only and note.image_size == len(img)
        after_img = section_bounds(p, 2)
        after_thumb = section_bounds(p, 1)
        assert before_img[0][before_img[1]:before_img[2]] == after_img[0][after_img[1]:after_img[2]]
        assert before_thumb[0][before_thumb[1]:before_thumb[2]] == after_thumb[0][after_thumb[1]:after_thumb[2]]
        full = ImageNoteFile.load_from_file(p, key)
        assert (full.title, full.folder_path, full.tags, full.text) == ("Nowy", "G/H", ["y", "z"], "nowy")
        assert full.image_data == img
    print("OK  rewrite_meta: obraz i miniatura kopiowane bajt w bajt")


def test_tampering_detected():
    key = new_key()
    with tempfile.TemporaryDirectory() as tmp:
        base = write(tmp, key, text="t", image_data=png(), title="T")
        original = open(base, "rb").read()

        def fresh():
            with open(base, "wb") as f:
                f.write(original)

        # bajt prefiksu (flags) -> AAD meta się nie zgadza
        fresh(); flip(base, 5)
        expect(WrongPasswordError, ImageNoteFile.load_meta, base, key)
        # bajt file_salt -> inny klucz pliku
        fresh(); flip(base, 40)
        expect(WrongPasswordError, ImageNoteFile.load_meta, base, key)
        # bajt w każdej sekcji
        for idx, exc in ((0, WrongPasswordError), (1, ValueError), (2, ValueError)):
            fresh()
            _, start, end = section_bounds(base, idx)
            flip(base, (start + end) // 2)
            expect(exc, ImageNoteFile.load_from_file, base, key)
        # zamiana sekcji miejscami (miniatura <-> obraz razem z wpisami tablicy) -> AAD z id sekcji
        fresh()
        data = bytearray(original)
        e = [bytes(data[48 + i * 16: 64 + i * 16]) for i in range(3)]
        _, s1, e1 = section_bounds(base, 1)
        _, s2, e2 = section_bounds(base, 2)
        swapped = bytes(data[:48]) + e[0] + e[2] + e[1] + bytes(data[96:s1]) + bytes(data[s2:e2]) + bytes(data[s1:e1])
        with open(base, "wb") as f:
            f.write(swapped)
        expect(ValueError, ImageNoteFile.load_from_file, base, key)
        # ucięty plik / dopisany bajt -> rozmiar nie zgadza się z nagłówkiem
        fresh()
        with open(base, "wb") as f:
            f.write(original[:-1])
        expect(ValueError, ImageNoteFile.load_meta, base, key)
        with open(base, "wb") as f:
            f.write(original + b"\x00")
        expect(ValueError, ImageNoteFile.load_meta, base, key)
        with open(base, "wb") as f:
            f.write(original[:3])
        expect(ValueError, ImageNoteFile.load_meta, base, key)
    print("OK  manipulacje wykryte (prefiks, salt, każda sekcja, zamiana sekcji, rozmiar)")


def test_section_spliced_from_other_file():
    key = new_key()
    with tempfile.TemporaryDirectory() as tmp:
        a = write(tmp, key, image_data=png(), title="A")
        b = write(tmp, key, image_data=png(300, 300), title="B")
        da, sa, ea = section_bounds(a, 2)
        db, sb, eb = section_bounds(b, 2)
        # obraz z B wklejony do A (z wpisem tablicy) -> inny file_salt = inny klucz
        spliced = da[:80] + db[80:96] + da[96:sa] + db[sb:eb]
        with open(a, "wb") as f:
            f.write(spliced)
        expect(ValueError, ImageNoteFile.load_from_file, a, key)
    print("OK  sekcja wklejona z innej notatki wykryta")


def test_wrong_password_and_foreign_vault():
    key = new_key()
    with tempfile.TemporaryDirectory() as tmp:
        p = write(tmp, key, text="t", title="T")
        wrong = VaultKey(KeyRing("inne"), key.vault_salt, FAST)
        expect(WrongPasswordError, ImageNoteFile.load_meta, p, wrong)
        # plik z innego vaulta (inny vault_salt), to samo hasło -> czytelny przez KeyRing
        other_vault = VaultKey(KeyRing(PASSWORD), os.urandom(16), FAST)
        assert ImageNoteFile.load_meta(p, other_vault).title == "T"
    print("OK  złe hasło -> WrongPasswordError; obcy vault_salt z tym samym hasłem czytelny")


def test_header_limits_and_legacy():
    key = new_key()
    with tempfile.TemporaryDirectory() as tmp:
        p = write(tmp, key, title="T")
        raw = bytearray(open(p, "rb").read())
        bad = bytearray(raw)
        struct.pack_into("<I", bad, 8, 64 * 1024 * 1024)  # 64 GiB pamięci w nagłówku
        with open(p, "wb") as f:
            f.write(bad)
        expect(ValueError, ImageNoteFile.peek_vault, p)
        expect(ValueError, ImageNoteFile.load_meta, p, key)
        bad = bytearray(raw)
        bad[6] = 9  # nieznany KDF
        with open(p, "wb") as f:
            f.write(bad)
        expect(ValueError, ImageNoteFile.load_meta, p, key)
        bad = bytearray(raw)
        bad[4] = 3  # plik v3
        with open(p, "wb") as f:
            f.write(bad)
        expect(LegacyFormatError, ImageNoteFile.load_meta, p, key)
        expect(LegacyFormatError, ImageNoteFile.peek_vault, p)
    expect(ValueError, KdfParams(4, 1, 1).validate)
    expect(TypeError, ImageNoteFile().save_to_file, "/tmp/x.imgnote", "haslo")
    print("OK  limity parametrów, nieznany KDF, v3 -> LegacyFormatError, brak trybu bez hasła")


def test_header_layout():
    assert _Header.PREFIX_SIZE == 48 and _Header.ENTRY_SIZE == 16
    print("OK  układ nagłówka 48 + 3x16")


def main():
    test_header_layout()
    test_roundtrip_and_partial_reads()
    test_thumbnail_webp_keeps_alpha()
    test_rewrite_meta_copies_image_bytes()
    test_tampering_detected()
    test_section_spliced_from_other_file()
    test_wrong_password_and_foreign_vault()
    test_header_limits_and_legacy()
    print("\nWszystkie testy przeszły.")


if __name__ == "__main__":
    main()
