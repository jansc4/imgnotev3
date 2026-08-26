"""
natsort.py — naturalne sortowanie ścieżek i nazw plików.
"notatka_2" < "notatka_10" zamiast "notatka_10" < "notatka_2"

Port 1:1 z v2 (imgnotev2/natsort.py) — brak zależności od Qt.
"""
import re


def natural_key(s: str):
    """
    Klucz sortowania: rozbija string na fragmenty tekstowe i liczbowe.
    Fragmenty liczbowe porównywane jako int, reszta case-insensitive.

    Przykład:
      "notatka_9"  → ["notatka_", 9]
      "notatka_10" → ["notatka_", 10]
      → 9 < 10 ✓
    """
    parts = re.split(r'(\d+)', s.lower())
    return [int(p) if p.isdigit() else p for p in parts]


def nsorted(iterable, key=None, reverse=False):
    """
    Jak sorted(), ale używa natural_key na wartości key(item) lub str(item).
    """
    if key is None:
        return sorted(iterable, key=lambda x: natural_key(str(x)), reverse=reverse)
    return sorted(iterable, key=lambda x: natural_key(str(key(x))), reverse=reverse)


def nsorted_paths(paths: list[str]) -> list[str]:
    """Sortuje listę ścieżek po samej nazwie pliku (basename), naturalnie."""
    import os
    return sorted(paths, key=lambda p: natural_key(os.path.basename(p)))
