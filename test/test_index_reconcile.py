"""
test_index_reconcile.py — testy czystej funkcji backendV4/index_reconcile.py.
Zero plików, zero I/O — cały test to operacje na słownikach w pamięci.

Uruchomienie:
    uv run python test/test_index_reconcile.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backendV4.index_reconcile import reconcile


def test_all_new():
    """Pusty indeks, pliki na dysku -> wszystko w `new`, pełny rebuild."""
    result = reconcile(index_notes={}, files_on_disk={"a.imgnote": 1.0, "b.imgnote": 2.0})
    assert set(result.new) == {"a.imgnote", "b.imgnote"}
    assert result.orphaned == [] and result.changed == [] and result.unchanged == []
    assert result.is_full_rebuild is True
    print("OK  test_all_new")


def test_all_unchanged():
    """Te same mtime w indeksie i na dysku -> wszystko w `unchanged`."""
    index_notes = {"a.imgnote": {"mtime": 1.0}, "b.imgnote": {"mtime": 2.0}}
    files_on_disk = {"a.imgnote": 1.0, "b.imgnote": 2.0}
    result = reconcile(index_notes, files_on_disk)
    assert set(result.unchanged) == {"a.imgnote", "b.imgnote"}
    assert result.new == [] and result.orphaned == [] and result.changed == []
    assert result.is_full_rebuild is False
    print("OK  test_all_unchanged")


def test_orphaned_entry():
    """Wpis jest w indeksie, ale pliku już nie ma na dysku (usunięty
    poza appką) -> ląduje w `orphaned`."""
    index_notes = {"a.imgnote": {"mtime": 1.0}}
    result = reconcile(index_notes, files_on_disk={})
    assert result.orphaned == ["a.imgnote"]
    assert result.new == [] and result.changed == [] and result.unchanged == []
    print("OK  test_orphaned_entry")


def test_changed_mtime():
    """Plik istnieje w obu, ale mtime się różni -> `changed`, trzeba
    odczytać na nowo (przez przyszły IndexManager, nie tu)."""
    index_notes = {"a.imgnote": {"mtime": 1.0}}
    result = reconcile(index_notes, files_on_disk={"a.imgnote": 999.0})
    assert result.changed == ["a.imgnote"]
    assert result.new == [] and result.orphaned == [] and result.unchanged == []
    print("OK  test_changed_mtime")


def test_mixed_case():
    """Wszystkie 4 kategorie naraz w jednym wywołaniu."""
    index_notes = {
        "stale.imgnote": {"mtime": 1.0},     # zniknął z dysku -> orphaned
        "same.imgnote": {"mtime": 2.0},      # bez zmian -> unchanged
        "edited.imgnote": {"mtime": 3.0},    # zmieniony mtime -> changed
    }
    files_on_disk = {
        "same.imgnote": 2.0,
        "edited.imgnote": 30.0,
        "fresh.imgnote": 4.0,                # nowy plik -> new
    }
    result = reconcile(index_notes, files_on_disk)
    assert result.orphaned == ["stale.imgnote"]
    assert result.unchanged == ["same.imgnote"]
    assert result.changed == ["edited.imgnote"]
    assert result.new == ["fresh.imgnote"]
    assert result.is_full_rebuild is False
    print("OK  test_mixed_case")


def main():
    test_all_new()
    test_all_unchanged()
    test_orphaned_entry()
    test_changed_mtime()
    test_mixed_case()
    print("\nWszystkie testy przeszły.")


if __name__ == "__main__":
    main()
