"""
test_note_cache.py — testy backendV4/note_cache.py na prawdziwych plikach v3.

Uruchomienie:
    uv run python test/test_note_cache.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backendV4.image_note_file import ImageNoteFile
from backendV4.note_cache import NoteCache

PASSWORD = "haslo123"

from _v4 import new_key  # noqa: E402  (szybkie parametry Argon2id w testach)
KEY = new_key(PASSWORD)
from backendV4.vault_key import KeyRing, VaultKey  # noqa: E402
WRONG = VaultKey(KeyRing("zle_haslo"), KEY.vault_salt, KEY.params)  # złe hasło, ten sam vault


def _make_note(root: Path, title: str, tags=None, image_data: bytes = b"") -> Path:
    note = ImageNoteFile(text=f"tresc {title}", title=title, folder_path="", tags=tags or [], image_data=image_data)
    path = root / ImageNoteFile.new_filename()
    note.save_to_file(str(path), KEY)
    return path


def _png_bytes(width=400, height=300) -> bytes:
    import io
    from PIL import Image
    img = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_get_metadata_returns_cheap_only():
    cache = NoteCache()
    result = cache.get_metadata("a.imgnote", "Tytuł", "Folder", mtime=1.0)
    assert result.title == "Tytuł"
    assert result.folder_path == "Folder"
    assert result.is_fully_hydrated is False
    assert result.text is None
    print("OK  test_get_metadata_returns_cheap_only")


def test_get_full_hydrates_and_caches():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _make_note(root, "Pełna", tags=["x"])
        mtime = os.path.getmtime(path)

        cache = NoteCache()
        result = cache.get_full(str(path), KEY, mtime)
        assert result.is_fully_hydrated is True
        assert result.text == "tresc Pełna"
        assert result.tags == ["x"]
    print("OK  test_get_full_hydrates_and_caches")


def test_get_full_second_call_uses_cache_not_disk():
    """Dowód, że drugie wywołanie NIE odszyfrowuje ponownie: podajemy złe
    hasło, ale mtime się nie zmienił -> jeśli cache faktycznie zadziałał,
    złe hasło nigdy nie dotrze do ImageNoteFile.load_from_file (bo w ogóle
    nie zostanie wołane), więc wyjątku nie będzie."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _make_note(root, "X")
        mtime = os.path.getmtime(path)

        cache = NoteCache()
        cache.get_full(str(path), KEY, mtime)              # poprawne hasło, hydratacja
        result = cache.get_full(str(path), WRONG, mtime)  # to samo mtime -> z cache
        assert result.text == "tresc X"
    print("OK  test_get_full_second_call_uses_cache_not_disk")


def test_get_full_refetches_after_mtime_change():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _make_note(root, "Stara")
        old_mtime = os.path.getmtime(path)

        cache = NoteCache()
        cache.get_full(str(path), KEY, old_mtime)

        edited = ImageNoteFile(text="nowa tresc", title="Nowa", folder_path="", tags=[])
        edited.save_to_file(str(path), KEY)
        os.utime(path, (old_mtime + 10, old_mtime + 10))
        new_mtime = os.path.getmtime(path)

        result = cache.get_full(str(path), KEY, new_mtime)
        assert result.text == "nowa tresc"
        assert result.title == "Nowa"
    print("OK  test_get_full_refetches_after_mtime_change")


def test_get_metadata_preserves_hydration_when_mtime_matches():
    """get_full hydratuje, potem get_metadata z tym samym mtime NIE
    czyści text/tags — bo mtime się zgadza, dane wciąż aktualne."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _make_note(root, "A", tags=["t"])
        mtime = os.path.getmtime(path)

        cache = NoteCache()
        cache.get_full(str(path), KEY, mtime)
        result = cache.get_metadata(str(path), "A", "", mtime)
        assert result.is_fully_hydrated is True
        assert result.text == "tresc A"
    print("OK  test_get_metadata_preserves_hydration_when_mtime_matches")


def test_get_metadata_drops_hydration_on_mtime_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _make_note(root, "A")
        mtime = os.path.getmtime(path)

        cache = NoteCache()
        cache.get_full(str(path), KEY, mtime)
        result = cache.get_metadata(str(path), "A", "", mtime=mtime + 999)
        assert result.is_fully_hydrated is False
    print("OK  test_get_metadata_drops_hydration_on_mtime_mismatch")


def test_invalidate_forces_refetch():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _make_note(root, "A")
        mtime = os.path.getmtime(path)

        cache = NoteCache()
        cache.get_full(str(path), KEY, mtime)
        cache.invalidate(str(path))

        # po inwalidacji złe hasło MUSI się wywalić - to dowód, że wpisu
        # faktycznie nie ma już w cache i get_full realnie sięgnie do dysku
        try:
            cache.get_full(str(path), WRONG, mtime)
        except Exception:
            pass
        else:
            raise AssertionError("po invalidate powinno próbować odczytać z dysku od nowa")
    print("OK  test_invalidate_forces_refetch")


def test_lru_eviction_drops_least_recently_used():
    """maxsize=2, trzy różne notatki -> ta dotknięta NAJDAWNIEJ znika sama,
    cachetools robi to wewnątrz __setitem__, bez żadnego ręcznego wywołania
    'posprzątaj teraz'."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path_a = _make_note(root, "A")
        path_b = _make_note(root, "B")
        path_c = _make_note(root, "C")

        cache = NoteCache(maxsize=2)
        cache.get_full(str(path_a), KEY, os.path.getmtime(path_a))
        cache.get_full(str(path_b), KEY, os.path.getmtime(path_b))
        assert len(cache) == 2

        cache.get_full(str(path_c), KEY, os.path.getmtime(path_c))  # A powinno wypaść
        assert len(cache) == 2
        assert str(path_a) not in cache._cache
        assert str(path_b) in cache._cache
        assert str(path_c) in cache._cache
    print("OK  test_lru_eviction_drops_least_recently_used")


def test_put_full_hydrates_without_any_decryption():
    """put_full bierze GOTOWY ImageNoteFile — zero I/O, zero hasła, zero
    pliku na dysku. To dowód, że hydratacja stąd naprawdę nie deszyfruje
    niczego (bo nie ma nawet czego deszyfrować — note istnieje tylko w RAM)."""
    note = ImageNoteFile(text="w pamięci", title="X", folder_path="F", tags=["t"])
    cache = NoteCache()
    result = cache.put_full("nigdy_nie_zapisany.imgnote", mtime=1.0, note=note)
    assert result.is_fully_hydrated is True
    assert result.text == "w pamięci"
    assert result.tags == ["t"]
    print("OK  test_put_full_hydrates_without_any_decryption")


def test_get_thumbnail_reads_only_thumb_section():
    """v4: miniatura z własnej sekcji pliku — bez obrazu i bez meta w cache;
    drugie wywołanie (złe hasło) idzie z cache miniatur."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        png = _png_bytes(400, 300)
        path = _make_note(root, "ZObrazkiem", image_data=png)
        mtime = os.path.getmtime(path)

        cache = NoteCache()
        thumb = cache.get_thumbnail(str(path), KEY, mtime)
        assert thumb is not None and thumb[8:12] == b"WEBP" and len(thumb) < len(png)
        assert len(cache._images) == 0 and len(cache._cache) == 0  # obraz ani meta nie dotknięte
        assert cache.get_thumbnail(str(path), WRONG, mtime) == thumb
    print("OK  test_get_thumbnail_reads_only_thumb_section")


def test_thumbnail_none_when_note_has_no_image():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _make_note(root, "BezObrazka")  # image_data domyślnie b""
        mtime = os.path.getmtime(path)

        cache = NoteCache()
        assert cache.get_thumbnail(str(path), KEY, mtime) is None
        # notatka bez obrazu nie wymusza ponownego deszyfrowania w get_full
        assert cache.get_full(str(path), KEY, mtime).image_data == b""
        assert cache.get_full(str(path), WRONG, mtime).image_data == b""
    print("OK  test_thumbnail_none_when_note_has_no_image")


def test_put_meta_keeps_no_image():
    """Reperacja (put_meta z load_meta): tekst w cache, obraz nie zajmuje budżetu,
    has_image z rozmiaru w nagłówku; get_full odszyfruje obraz dopiero na żądanie."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        png = _png_bytes(200, 200)
        path = _make_note(root, "M", image_data=png)
        mtime = os.path.getmtime(path)
        meta = ImageNoteFile.load_meta(str(path), KEY)
        cache = NoteCache()
        entry = cache.put_meta(str(path), mtime, meta)
        assert entry.text == "tresc M" and entry.has_image and len(cache._images) == 0
        assert cache.get_content(str(path), WRONG, mtime).text == "tresc M"  # z cache
        assert cache.get_full(str(path), KEY, mtime).image_data == png
    print("OK  test_put_meta_keeps_no_image")


def test_image_budget_evicts_images_keeps_text():
    """P2: budżet bajtów na pełne obrazy — stary obraz wypada, tekst zostaje
    (get_content nie deszyfruje), a get_full odszyfrowuje obraz ponownie."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        img = os.urandom(600)
        path_a = _make_note(root, "A", image_data=img)
        path_b = _make_note(root, "B", image_data=img)
        cache = NoteCache(image_budget=1000)
        cache.get_full(str(path_a), KEY, os.path.getmtime(path_a))
        cache.get_full(str(path_b), KEY, os.path.getmtime(path_b))
        assert str(path_a) not in cache._images and str(path_b) in cache._images
        assert cache.get_content(str(path_a), WRONG, os.path.getmtime(path_a)).text == "tresc A"
        assert cache.get_full(str(path_a), KEY, os.path.getmtime(path_a)).image_data == img
    print("OK  test_image_budget_evicts_images_keeps_text")


def main():
    test_get_metadata_returns_cheap_only()
    test_get_full_hydrates_and_caches()
    test_get_full_second_call_uses_cache_not_disk()
    test_get_full_refetches_after_mtime_change()
    test_get_metadata_preserves_hydration_when_mtime_matches()
    test_get_metadata_drops_hydration_on_mtime_mismatch()
    test_invalidate_forces_refetch()
    test_lru_eviction_drops_least_recently_used()
    test_put_full_hydrates_without_any_decryption()
    test_get_thumbnail_reads_only_thumb_section()
    test_thumbnail_none_when_note_has_no_image()
    test_put_meta_keeps_no_image()
    test_image_budget_evicts_images_keeps_text()
    print("\nWszystkie testy przeszły.")


if __name__ == "__main__":
    main()
