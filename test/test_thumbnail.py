"""
test_thumbnail.py — testy backendV4/thumbnail.py, czystej funkcji.
Zero I/O na dysk, zero kryptografii — tylko obrazki zbudowane w pamięci.

Uruchomienie:
    uv run python test/test_thumbnail.py
"""
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from backendV4.thumbnail import generate_thumbnail, MAX_DIMENSION


def _solid(width, height, fmt="PNG", mode="RGB", color=(200, 30, 30)):
    img = Image.new(mode, (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _noisy(width, height, fmt="PNG"):
    """Losowe piksele — w odróżnieniu od jednolitego koloru, PNG/JPEG nie
    skompresuje tego niemal do zera niezależnie od rozmiaru, więc porównanie
    'mniejszy obrazek = mniej bajtów' jest tu wiarygodne."""
    raw = os.urandom(width * height * 3)
    img = Image.frombytes("RGB", (width, height), raw)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def test_shrinks_large_noisy_png():
    data = _noisy(800, 600, fmt="PNG")
    thumb = generate_thumbnail(data)
    assert thumb is not None
    img = Image.open(io.BytesIO(thumb))
    assert img.format == "PNG"
    assert max(img.size) <= MAX_DIMENSION
    assert len(thumb) < len(data)
    print("OK  test_shrinks_large_noisy_png")


def test_shrinks_large_jpeg_keeps_format():
    data = _solid(2000, 1500, fmt="JPEG")
    thumb = generate_thumbnail(data)
    assert thumb is not None
    img = Image.open(io.BytesIO(thumb))
    assert img.format == "JPEG"
    assert max(img.size) <= MAX_DIMENSION
    print("OK  test_shrinks_large_jpeg_keeps_format")


def test_does_not_upscale_small_image():
    data = _solid(50, 40, fmt="PNG")
    thumb = generate_thumbnail(data)
    img = Image.open(io.BytesIO(thumb))
    assert img.size == (50, 40)
    print("OK  test_does_not_upscale_small_image")


def test_preserves_aspect_ratio():
    data = _solid(1600, 400, fmt="PNG")  # 4:1
    thumb = generate_thumbnail(data)
    img = Image.open(io.BytesIO(thumb))
    assert img.size[0] == MAX_DIMENSION
    assert img.size[1] == MAX_DIMENSION // 4
    print("OK  test_preserves_aspect_ratio")


def test_preserves_transparency_in_png():
    data = _solid(500, 500, fmt="PNG", mode="RGBA", color=(10, 20, 30, 128))
    thumb = generate_thumbnail(data)
    img = Image.open(io.BytesIO(thumb))
    assert img.mode in ("RGBA", "LA")
    print("OK  test_preserves_transparency_in_png")


def test_empty_bytes_returns_none():
    assert generate_thumbnail(b"") is None
    print("OK  test_empty_bytes_returns_none")


def test_garbage_bytes_returns_none():
    assert generate_thumbnail(b"to nie jest obrazek, tylko zwykly tekst") is None
    print("OK  test_garbage_bytes_returns_none")


def main():
    test_shrinks_large_noisy_png()
    test_shrinks_large_jpeg_keeps_format()
    test_does_not_upscale_small_image()
    test_preserves_aspect_ratio()
    test_preserves_transparency_in_png()
    test_empty_bytes_returns_none()
    test_garbage_bytes_returns_none()
    print("\nWszystkie testy przeszły.")


if __name__ == "__main__":
    main()
