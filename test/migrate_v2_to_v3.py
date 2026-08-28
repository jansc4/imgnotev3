"""
migrate_v2_to_v3.py — jednorazowa migracja plików .imgnote z formatu v2 na v3.

Robi KOPIĘ, nie rusza oryginałów: czyta rekurencyjnie z --source, zapisuje
nowe pliki (losowe nazwy) do --dest. Bezpieczne testowanie na kopii danych
przed użyciem na żywej bibliotece.

Co się dzieje z każdym plikiem:
- title       = dzisiejsza nazwa pliku bez rozszerzenia (dokładnie tak, jak
                dziś interpretuje ją api.py: title == nazwa pliku)
- folder_path = ścieżka względna katalogu pliku wobec --source
                (podfoldery na dysku -> wirtualna hierarchia w payloadzie)
- nazwa nowego pliku = losowa (ImageNoteFile.new_filename())
- tekst i tagi bez zmian

Użycie:
    uv run python test/migrate_v2_to_v3.py \\
        --source /ścieżka/do/KOPII/starych/notatek \\
        --dest /ścieżka/wyjściowa \\
        --password TWOJEHASLO
    (pomiń --password jeśli notatki są bez hasła)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.image_note_file import ImageNoteFile as ImageNoteFileV2
from backendV4.image_note_file import ImageNoteFile as ImageNoteFileV3


def migrate_one(src_path: Path, source_root: Path, dest_root: Path, password: str | None):
    """Migruje jeden plik. Zwraca (title, new_filename, folder_path) do logu."""
    old = ImageNoteFileV2.load_from_file(str(src_path), password=password)

    title = src_path.stem
    rel_dir = src_path.parent.relative_to(source_root)
    folder_path = "" if rel_dir == Path(".") else str(rel_dir)

    new = ImageNoteFileV3(
        text=old.text,
        image_data=old.image_data,
        tags=old.tags,
        title=title,
        folder_path=folder_path,
    )

    new_filename = ImageNoteFileV3.new_filename()
    new.save_to_file(str(dest_root / new_filename), password=password)

    return title, new_filename, new.folder_path


def main():
    parser = argparse.ArgumentParser(description="Migracja .imgnote v2 -> v3")
    parser.add_argument("--source", required=True, help="katalog z KOPIĄ starych plików v2")
    parser.add_argument("--dest", required=True, help="katalog docelowy na nowe pliki v3")
    parser.add_argument("--password", default=None, help="hasło (pomiń jeśli notatki bez hasła)")
    args = parser.parse_args()

    source_root = Path(args.source).resolve()
    dest_root = Path(args.dest).resolve()

    if not source_root.is_dir():
        print(f"Błąd: {source_root} nie jest katalogiem.")
        sys.exit(1)
    if source_root == dest_root:
        print("Błąd: --source i --dest nie mogą być tym samym katalogiem "
              "(nie nadpisujemy oryginałów).")
        sys.exit(1)

    imgnote_files = sorted(source_root.rglob("*.imgnote"))
    if not imgnote_files:
        print(f"Nie znaleziono żadnych .imgnote w {source_root}")
        sys.exit(1)

    dest_root.mkdir(parents=True, exist_ok=True)
    print(f"Znaleziono {len(imgnote_files)} plik(ów) w {source_root}\n")

    ok, failed = 0, []
    for src in imgnote_files:
        try:
            title, new_filename, folder_path = migrate_one(src, source_root, dest_root, args.password)
            print(f"OK   {src.relative_to(source_root)}")
            print(f"     -> {new_filename}  title={title!r}  folder_path={folder_path or '(root)'!r}")
            ok += 1
        except Exception as e:
            print(f"BŁĄD {src.relative_to(source_root)}: {e}")
            failed.append(src)

    print(f"\nZmigrowano {ok}/{len(imgnote_files)}.")
    if failed:
        print("Nieudane:")
        for f in failed:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
