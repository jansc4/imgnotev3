"""
inspect_v3.py — czyta pliki .imgnote v3 i pokazuje ich zawartość do
wizualnej oceny. Tylko odczyt oryginałów; jedyne co ewentualnie zapisuje
to odszyfrowane obrazki, i tylko jeśli poda się --export-images.

Użycie:
    uv run python test/inspect_v3.py --dir /ścieżka/z/plikami/v3 --password TWOJEHASLO
    uv run python test/inspect_v3.py --dir ... --password ... --export-images /tmp/podglad
    (pomiń --password jeśli notatki są bez hasła)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backendV4.image_note_file import ImageNoteFile


def detect_image_ext(data: bytes) -> str:
    """Rozpoznanie formatu po magic bytes — image_data nie ma rozszerzenia,
    bo w payloadzie to czysty hex string bajtów obrazka."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "bin"


def sanitize(name: str, max_len: int = 40) -> str:
    safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in name)
    return safe.strip()[:max_len] or "bez_tytulu"


def main():
    parser = argparse.ArgumentParser(description="Podgląd plików .imgnote v3")
    parser.add_argument("--dir", required=True, help="katalog z plikami v3 (przeszukiwany rekurencyjnie)")
    parser.add_argument("--password", default=None, help="hasło (pomiń jeśli notatki bez hasła)")
    parser.add_argument("--export-images", default=None, help="katalog na odszyfrowane obrazki")
    args = parser.parse_args()

    source = Path(args.dir).resolve()
    if not source.is_dir():
        print(f"Błąd: {source} nie jest katalogiem.")
        sys.exit(1)

    export_dir = None
    if args.export_images:
        export_dir = Path(args.export_images).resolve()
        export_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(source.rglob("*.imgnote"))
    if not files:
        print(f"Nie znaleziono żadnych .imgnote w {source}")
        sys.exit(1)

    print(f"Znaleziono {len(files)} plik(ów) w {source}\n")
    print("=" * 70)

    for path in files:
        try:
            note = ImageNoteFile.load_from_file(str(path), password=args.password)
        except Exception as e:
            print(f"BŁĄD  {path.name}: {e}")
            print("-" * 70)
            continue

        text_preview = note.text[:120].replace("\n", " ")
        if len(note.text) > 120:
            text_preview += "…"

        print(f"plik:        {path.name}")
        print(f"title:       {note.title!r}")
        print(f"folder_path: {note.folder_path or '(root)'!r}")
        print(f"tags:        {note.tags}")
        print(f"text:        {text_preview!r}")
        print(f"encrypted:   {note.encrypted}")
        print(f"image:       {len(note.image_data)} B", end="")

        if note.image_data:
            ext = detect_image_ext(note.image_data)
            print(f" ({ext})")
            if export_dir:
                out_name = f"{sanitize(note.title)}__{path.stem[:8]}.{ext}"
                (export_dir / out_name).write_bytes(note.image_data)
                print(f"             -> {export_dir / out_name}")
        else:
            print(" (brak obrazka)")

        print("-" * 70)

    if export_dir:
        print(f"\nObrazki do podglądu w: {export_dir}")


if __name__ == "__main__":
    main()
