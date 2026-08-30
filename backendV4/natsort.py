"""
natsort.py — naturalne sortowanie nazw (folderów, tytułów notatek).
"notatka 2" < "notatka 10" zamiast "notatka 10" < "notatka 2", case-insensitive.

Port z backend/natsort.py (usuniętego w porządkach po V4 jako "nieużywany
po przepisaniu na FolderManager" — błędnie: stary backend/api.py go
używał wszędzie przy budowie drzewa i list notatek, `sorted()` bez tego
modułu nie jest jego zamiennikiem, tylko regresem — zgłoszone przez Jana
2026-08-28, "straciliśmy po drodze sortowanie w tree").
"""
import re


def natural_key(s: str):
    """
    Klucz sortowania: rozbija string na fragmenty tekstowe i liczbowe.
    Fragmenty liczbowe porównywane jako int, reszta case-insensitive.

    Przykład:
      "notatka 9"  → ["notatka ", 9]
      "notatka 10" → ["notatka ", 10]
      → 9 < 10 ✓
    """
    parts = re.split(r'(\d+)', s.lower())
    return [int(p) if p.isdigit() else p for p in parts]


def nsorted(iterable, key=None, reverse=False):
    """Jak sorted(), ale używa natural_key na wartości key(item) lub str(item)."""
    if key is None:
        return sorted(iterable, key=lambda x: natural_key(str(x)), reverse=reverse)
    return sorted(iterable, key=lambda x: natural_key(str(key(x))), reverse=reverse)
