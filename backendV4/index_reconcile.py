"""
index_reconcile.py — trójstronne porównanie indeksu ze stanem dysku
(patrz "Decyzje podjęte" w V4_REDESIGN_PLAN.md). Czysta funkcja: dostaje
i zwraca zwykłe słowniki/dataclass, zero I/O poza tym, co przekazano jako
argumenty, zero kryptografii, zero hasła — dlatego testowalna w izolacji
zwykłymi słownikami, bez tworzenia jednego pliku na dysku.

To NIE jest miejsce, gdzie coś się odszyfrowuje. Ten moduł tylko mówi
"co się zmieniło" (3 kategorie + "nic się nie zmieniło" dla kompletności).
Decyzję co z tym zrobić — odszyfrować zmienione/nowe pliki, zaktualizować
IndexFile, dorzucić do cache miniatur — podejmuje przyszły IndexManager
(krok 3), na podstawie wyniku stąd.
"""
from dataclasses import dataclass, field


@dataclass
class ReconcileResult:
    orphaned: list[str] = field(default_factory=list)   # w indeksie, brak pliku na dysku
    changed: list[str] = field(default_factory=list)     # mtime się różni -> trzeba odczytać
    new: list[str] = field(default_factory=list)         # na dysku, brak wpisu w indeksie
    unchanged: list[str] = field(default_factory=list)   # mtime się zgadza -> nic do roboty

    @property
    def is_full_rebuild(self) -> bool:
        """Zdegenerowany przypadek z planu: indeks pusty albo w całości
        nieaktualny -> reperacja i tak robi to samo co pełny rebuild,
        bo wszystko ląduje w `new`, nic w `unchanged`/`orphaned`."""
        return bool(self.new) and not self.unchanged and not self.orphaned


def reconcile(index_notes: dict, files_on_disk: dict) -> ReconcileResult:
    """
    index_notes:   IndexFile.notes -> filename -> {..., "mtime": float}
    files_on_disk: filename -> mtime (np. z os.path.getmtime na każdym
                   pliku .imgnote znalezionym w root folderze)
    """
    result = ReconcileResult()

    for filename in index_notes:
        if filename not in files_on_disk:
            result.orphaned.append(filename)

    for filename, disk_mtime in files_on_disk.items():
        if filename not in index_notes:
            result.new.append(filename)
        elif index_notes[filename]["mtime"] != disk_mtime:
            result.changed.append(filename)
        else:
            result.unchanged.append(filename)

    return result
