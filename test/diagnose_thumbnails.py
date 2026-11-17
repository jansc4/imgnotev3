"""
diagnose_thumbnails.py — notatki z obrazem, ale bez miniatury: co to za plik
i dlaczego Pillow go nie przetworzył. Z --fix generuje brakujące miniatury
(po dodaniu obsługi formatu), zachowując mtime i resztę notatki bez zmian.

Użycie (hasło pytane interaktywnie):
    uv run python test/diagnose_thumbnails.py --vault /ścieżka/do/vaulta_v4
    uv run python test/diagnose_thumbnails.py --vault /ścieżka/do/vaulta_v4 --fix
Wypisuje tylko nazwy plików, typ i błąd — bez tytułów i treści notatek.
"""
import argparse
import getpass
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from backendV4 import thumbnail  # noqa: E402
from backendV4.image_note_file import ImageNoteFile  # noqa: E402
from backendV4.index_manager import IndexManager  # noqa: E402

SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "PNG"), (b"\xff\xd8\xff", "JPEG"), (b"GIF8", "GIF"),
    (b"BM", "BMP"), (b"II*\x00", "TIFF"), (b"MM\x00*", "TIFF"), (b"%PDF", "PDF (dokument, nie obraz)"),
    (b"\x00\x00\x00\x0cjP  ", "JPEG 2000"),
)


def detect(data: bytes) -> str:
    """Typ po magic bytes — niezależnie od tego, czy Pillow go zna."""
    for sig, name in SIGNATURES:
        if data.startswith(sig):
            return name
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data[4:8] == b"ftyp":
        brand = data[8:12].decode("ascii", "replace")
        if brand in ("heic", "heix", "hevc", "hevx", "mif1", "msf1", "heim", "heis"):
            return f"HEIC/HEIF ({brand})"
        if brand in ("avif", "avis"):
            return f"AVIF ({brand})"
        return f"kontener ISO ({brand})"
    head = data[:512].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "SVG (grafika wektorowa)"
    if head.startswith((b"<!doctype html", b"<html")) or b"<html" in head[:200]:
        return "HTML (strona zamiast obrazu)"
    return f"nieznany (pierwsze bajty: {data[:12].hex()})"


def png_chunks(data: bytes) -> str:
    """Lista bloków PNG: nazwa, długość, poprawność CRC (bez treści bloków)."""
    import struct
    import zlib
    out, pos = [], 8
    while pos + 8 <= len(data) and len(out) < 40:
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        crc_raw = data[pos + 8 + length:pos + 12 + length]
        ok = len(crc_raw) == 4 and struct.unpack(">I", crc_raw)[0] == zlib.crc32(ctype + body) & 0xFFFFFFFF
        name = ctype.decode("latin-1", "replace")
        out.append(f"{name}({length}{'' if ok else ', ZŁE CRC'})")
        if ctype == b"IEND" or len(crc_raw) < 4:
            break
        pos += 12 + length
    return " ".join(out)


def raw_png_error(data: bytes) -> str:
    """Prawdziwy wyjątek wtyczki PNG (Image.open zamienia go na 'cannot identify')."""
    from PIL import PngImagePlugin
    try:
        PngImagePlugin.PngImageFile(io.BytesIO(data)).load()
        return "wtyczka PNG wprost: OK"
    except Exception as e:  # noqa: BLE001
        return f"wtyczka PNG wprost: {type(e).__name__}: {e}"


def try_fixes(data: bytes) -> list[str]:
    """Które ustawienie Pillow pozwala wygenerować miniaturę."""
    from PIL import ImageFile, PngImagePlugin
    results = []
    for label, apply, undo in (
        ("LOAD_TRUNCATED_IMAGES", lambda: setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True),
         lambda: setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", False)),
        ("MAX_TEXT_CHUNK x100", lambda: setattr(PngImagePlugin, "MAX_TEXT_CHUNK", PngImagePlugin.MAX_TEXT_CHUNK * 100),
         lambda: setattr(PngImagePlugin, "MAX_TEXT_CHUNK", PngImagePlugin.MAX_TEXT_CHUNK // 100)),
    ):
        apply()
        try:
            ok = thumbnail._generate(data) is not None  # bez fallbacku, żeby wynik coś mówił
        finally:
            undo()
        results.append(f"{label}: {'POMAGA' if ok else 'nie pomaga'}")
    return results


def why(data: bytes) -> str:
    """Powtórzenie kroków generate_thumbnail z widocznym błędem."""
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        return f"Image.open: {type(e).__name__}: {e}"
    desc = f"{img.format} {img.mode} {img.width}x{img.height}"
    if img.width * img.height > thumbnail.MAX_PIXELS:
        return f"{desc} — powyżej limitu {thumbnail.MAX_PIXELS // 1_000_000} MPx"
    for step, fn in (("load", lambda: img.load()),
                     ("convert", lambda: img.convert("RGBA")),
                     ("save", lambda: img.convert("RGBA").save(io.BytesIO(), format="WEBP"))):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            return f"{desc} — {step}: {type(e).__name__}: {e}"
    return f"{desc} — teraz przechodzi (miniaturę da się wygenerować)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--fix", action="store_true", help="wygeneruj brakujące miniatury, gdzie się da")
    args = parser.parse_args()

    mgr = IndexManager(str(args.vault), password=getpass.getpass("Hasło vaulta: "))
    mgr.load_or_rebuild()
    if not mgr.password_verified:
        print("Nie udało się zweryfikować hasła (pusty vault?).")
        return 2

    found, fixed = 0, 0
    for filename in sorted(mgr.index.notes):
        path = os.path.join(str(args.vault), filename)
        meta = ImageNoteFile.load_meta(path, mgr.key)
        if meta.image_size == 0 or ImageNoteFile.load_thumbnail(path, mgr.key) is not None:
            continue
        found += 1
        note = ImageNoteFile.load_from_file(path, mgr.key)
        print(f"{filename}  {len(note.image_data) / 1024:8.0f} KB  {detect(note.image_data)}")
        print(f"    {why(note.image_data)}")
        if note.image_data.startswith(b"\x89PNG"):
            print(f"    bloki: {png_chunks(note.image_data)}")
            print(f"    {raw_png_error(note.image_data)}")
            print(f"    {'; '.join(try_fixes(note.image_data))}")
        ok = thumbnail.generate_thumbnail(note.image_data) is not None
        print(f"    generate_thumbnail (z odchudzaniem PNG): {'OK — --fix doda miniaturę' if ok else 'nadal brak'}")
        if args.fix:
            thumb = thumbnail.generate_thumbnail(note.image_data)
            if thumb is not None:
                st = os.stat(path)
                note.thumbnail_data = thumb
                note.save_to_file(path, mgr.key)  # pełny zapis: obraz bez zmian, nowa miniatura
                os.utime(path, (st.st_atime, st.st_mtime))
                mgr.note_saved(path, note)
                fixed += 1
                print("    -> miniatura dodana")
    if args.fix and fixed:
        mgr.save()
    print(f"\nNotatek z obrazem bez miniatury: {found}" + (f", naprawiono: {fixed}" if args.fix else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
