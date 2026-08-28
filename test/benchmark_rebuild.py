"""
benchmark_rebuild.py — mierzy realny czas pełnego rebuildu IndexManager na
prawdziwym roocie z prawdziwymi notatkami v4. To NIE jest tylko pomiar —
na koniec ZAPISUJE wynikowy indeks (mgr.save()), więc to realne
zbudowanie indeksu dla tego roota. Kolejne uruchomienia (np. samej appki
po wpięciu Api) będą już robić reperację (szybko), nie pełny rebuild.

Użycie:
    uv run python test/benchmark_rebuild.py --root /ścieżka/do/v4 --password TWOJEHASLO
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backendV4.index_file import IndexFile
from backendV4.index_manager import IndexManager


def main():
    parser = argparse.ArgumentParser(description="Benchmark pełnego rebuildu indeksu v4")
    parser.add_argument("--root", required=True)
    parser.add_argument("--password", default=None)
    args = parser.parse_args()

    last_reported = [-1]

    def on_progress(current, total):
        step = max(1, total // 100)  # log co ~1%, nie przy każdym pliku
        if current - last_reported[0] >= step or current == total:
            last_reported[0] = current
            pct = 100 * current // total if total else 100
            print(f"\r  {current}/{total} ({pct}%)", end="", flush=True)

    mgr = IndexManager(args.root, password=args.password, on_progress=on_progress)

    start = time.monotonic()
    result = mgr.load_or_rebuild()
    elapsed = time.monotonic() - start

    print()
    print(f"orphaned:      {len(result.orphaned)}")
    print(f"changed:       {len(result.changed)}")
    print(f"new:           {len(result.new)}")
    print(f"unchanged:     {len(result.unchanged)}")
    print(f"pełny rebuild: {result.is_full_rebuild}")
    print(f"czas:          {elapsed:.1f}s  ({elapsed / 60:.1f} min)")

    mgr.save()
    print(f"\nZapisano indeks: {os.path.join(args.root, IndexFile.FILENAME)}")


if __name__ == "__main__":
    main()
