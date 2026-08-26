"""
tag_registry.py — globalny rejestr tagów ze wszystkich .imgnote.

Refaktor z v2 (imgnotev2/tag_registry.py): zamiast QThread/pyqtSignal
używa zwykłego threading.Thread + callbacków (on_progress, on_updated).
Podłączenie callbacków do window.evaluate_js() należy do Etapu 3 (Api).

Logika skanowania (count_files, run/_run) bez zmian względem v2.
"""
import os
import threading
from typing import Callable, Optional

from .image_note_file import ImageNoteFile


class TagRegistry:
    def __init__(
        self,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_updated: Optional[Callable[[list[str]], None]] = None,
        note_loader: Optional[Callable[[str], "ImageNoteFile"]] = None,
    ):
        self._folders: list[str] = []
        self._password: str = ""
        self._tags: list[str] = []
        self._cancelled = False
        self._thread: Optional[threading.Thread] = None

        # Odpowiedniki sygnałów Qt updated(list) / progress(int, int).
        self.on_progress = on_progress
        self.on_updated = on_updated
        # Opcjonalny loader z cache po mtime (patrz Api._load_note_cached) —
        # bez niego każdy refresh() odszyfrowuje ponownie WSZYSTKIE pliki,
        # nawet te niezmienione od ostatniego skanu (Scrypt KDF na plik jest
        # kosztowny), co czyni skan zauważalnie wolnym przy większej liczbie
        # notatek. Domyślnie (brak loadera, np. użycie poza Api) zachowanie
        # jak dawniej — zawsze świeży odczyt z hasłem self._password.
        self._note_loader = note_loader or (
            lambda fp: ImageNoteFile.load_from_file(fp, password=self._password or None)
        )

    def cancel(self):
        self._cancelled = True

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_tags(self) -> list[str]:
        return list(self._tags)

    def set_tags(self, tags: list[str]):
        self._tags = list(tags)
        if self.on_updated:
            self.on_updated(self._tags)

    def count_files(self, folders: list[str]) -> int:
        """Zlicz .imgnote w folderach (do progress bar). Bez zmian z v2."""
        total = 0
        for root in folders:
            try:
                for _, _, filenames in os.walk(root):
                    total += sum(1 for f in filenames if f.endswith(".imgnote"))
            except Exception:
                pass
        return max(1, total)

    def refresh(self, folders: list[str], password: str):
        """
        Zatrzymaj poprzedni skan i zacznij nowy w tle.
        Odpowiednik v2: `if self.isRunning(): self.cancel(); self.quit(); self.wait(1000)`.
        """
        if self.is_running():
            self.cancel()
            self._thread.join(timeout=1.0)

        self._cancelled = False
        self._folders = list(folders)
        self._password = password
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        """Odpowiednik v2 `run()` — logika skanowania bez zmian, poza emit → callback."""
        seen: set[str] = set()
        total = self.count_files(self._folders)
        current = 0

        for root in self._folders:
            if self._cancelled:
                return
            try:
                for dirpath, _, filenames in os.walk(root):
                    if self._cancelled:
                        return
                    for fn in filenames:
                        if not fn.endswith(".imgnote"):
                            continue
                        current += 1
                        if self.on_progress:
                            self.on_progress(current, total)
                        fp = os.path.join(dirpath, fn)
                        try:
                            note = self._note_loader(fp)
                            for t in note.tags:
                                seen.add(t.strip())
                        except Exception:
                            pass
            except PermissionError:
                pass

        if not self._cancelled:
            self._tags = sorted(seen, key=str.lower)
            if self.on_updated:
                self.on_updated(self._tags)
