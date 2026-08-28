"""
test_search_engine.py — testy backendV4/search_engine.py.

Dwie grupy: (1) czyste zapytania o tagi na syntetycznym IndexFile, zero
plików na dysku, zero I/O — note_loader tam gdzie się nie powinien wołać
jest funkcją, która RZUCA, jeśli ktoś ją wywoła (dowód, że search() po
tagach faktycznie nie dotyka note_loader). (2) zapytania tekstowe na
prawdziwych plikach v3 w tempdir.

Uruchomienie:
    uv run python test/test_search_engine.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backendV4.image_note_file import ImageNoteFile
from backendV4.index_file import IndexFile
from backendV4.note_cache import NoteCache
from backendV4.search_engine import SearchEngine, match_text

PASSWORD = "haslo123"


def _never_call(path):
    raise AssertionError(f"note_loader nie powinien być wołany dla: {path}")


def _build_index(entries):
    """entries: [(filename, title, folder_path, tags), ...] -> IndexFile"""
    idx = IndexFile()
    for i, (filename, title, folder_path, tags) in enumerate(entries):
        idx.set_note(filename, title, folder_path, mtime=float(i), tags=tags)
    return idx


def _names(results):
    return {os.path.basename(r["filepath"]) for r in results}


def test_tag_only_query_never_calls_note_loader():
    idx = _build_index([
        ("a.imgnote", "A", "", ["projekt2024", "wazne"]),
        ("b.imgnote", "B", "", ["szkic"]),
        ("c.imgnote", "C", "", ["projekt2024"]),
    ])
    engine = SearchEngine(idx, "/root", note_loader=_never_call)
    results = engine.search(tag_query="projekt")  # częściowe dopasowanie
    assert _names(results) == {"a.imgnote", "c.imgnote"}
    print("OK  test_tag_only_query_never_calls_note_loader")


def test_empty_query_returns_everything_without_decrypting():
    idx = _build_index([
        ("a.imgnote", "A", "", ["x"]),
        ("b.imgnote", "B", "", []),
    ])
    engine = SearchEngine(idx, "/root", note_loader=_never_call)
    results = engine.search()
    assert _names(results) == {"a.imgnote", "b.imgnote"}
    print("OK  test_empty_query_returns_everything_without_decrypting")


def test_boolean_combinators():
    idx = _build_index([
        ("a.imgnote", "A", "", ["projekt", "wazne"]),
        ("b.imgnote", "B", "", ["projekt"]),
        ("c.imgnote", "C", "", ["wazne"]),
        ("d.imgnote", "D", "", ["archiwum"]),
    ])
    engine = SearchEngine(idx, "/root", note_loader=_never_call)

    def names(q):
        return _names(engine.search(tag_query=q))

    assert names("projekt AND wazne") == {"a.imgnote"}
    assert names("projekt wazne") == {"a.imgnote", "b.imgnote", "c.imgnote"}  # spacja = OR
    # UWAGA (własność oryginalnej gramatyki, nie mój dodatek): spacja bez
    # jawnego AND to zawsze OR, NAWET gdy prawa strona zaczyna się od NOT.
    # "projekt NOT wazne" = "projekt OR (NOT wazne)", nie "projekt AND NOT
    # wazne". Jawne AND trzeba napisać wprost.
    assert names("projekt NOT wazne") == {"a.imgnote", "b.imgnote", "d.imgnote"}
    assert names("projekt AND NOT wazne") == {"b.imgnote"}
    assert names("(projekt OR archiwum) AND NOT wazne") == {"b.imgnote", "d.imgnote"}
    print("OK  test_boolean_combinators")


def test_partial_case_insensitive_tag_match():
    idx = _build_index([
        ("a.imgnote", "A", "", ["Projekt2024"]),
        ("b.imgnote", "B", "", ["inny"]),
    ])
    engine = SearchEngine(idx, "/root", note_loader=_never_call)
    assert _names(engine.search(tag_query="PROJEKT")) == {"a.imgnote"}
    print("OK  test_partial_case_insensitive_tag_match")


def _make_note(root: Path, title: str, text: str, tags=None) -> Path:
    note = ImageNoteFile(text=text, title=title, folder_path="", tags=tags or [])
    path = root / ImageNoteFile.new_filename()
    note.save_to_file(str(path), password=PASSWORD)
    return path


def _real_loader(path: str) -> str:
    return ImageNoteFile.load_from_file(path, password=PASSWORD).text


def test_text_only_query_decrypts_all_candidates():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path_a = _make_note(root, "A", "hello world")
        path_b = _make_note(root, "B", "goodbye")

        idx = _build_index([
            (path_a.name, "A", "", []),
            (path_b.name, "B", "", []),
        ])
        called = []

        def loader(p):
            called.append(p)
            return _real_loader(p)

        engine = SearchEngine(idx, str(root), note_loader=loader)
        results = engine.search(text_query="hello")

        assert len(called) == 2  # brak tag_query -> kandydatami są wszyscy
        assert _names(results) == {path_a.name}
    print("OK  test_text_only_query_decrypts_all_candidates")


def test_combined_tag_and_text_narrows_before_decrypting():
    """Sedno wygranej: tag_query zawęża kandydatów, note_loader wołany
    TYLKO dla nich, nie dla całej biblioteki."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path_a = _make_note(root, "A", "hello world", tags=["projekt"])
        path_b = _make_note(root, "B", "hello world", tags=["szkic"])  # inny tag, ma pasujący tekst
        path_c = _make_note(root, "C", "coś innego", tags=["projekt"])

        idx = _build_index([
            (path_a.name, "A", "", ["projekt"]),
            (path_b.name, "B", "", ["szkic"]),
            (path_c.name, "C", "", ["projekt"]),
        ])
        called = []

        def loader(p):
            called.append(p)
            return _real_loader(p)

        engine = SearchEngine(idx, str(root), note_loader=loader)
        results = engine.search(tag_query="projekt", text_query="hello")

        assert set(called) == {str(path_a), str(path_c)}  # NIE path_b - odfiltrowany po tagu
        assert _names(results) == {path_a.name}
    print("OK  test_combined_tag_and_text_narrows_before_decrypting")


def test_bad_note_skipped_gracefully():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path_a = _make_note(root, "A", "hello")
        path_b = _make_note(root, "B", "hello")

        idx = _build_index([
            (path_a.name, "A", "", []),
            (path_b.name, "B", "", []),
        ])

        def flaky_loader(p):
            if p == str(path_a):
                raise ValueError("uszkodzony plik")
            return _real_loader(p)

        engine = SearchEngine(idx, str(root), note_loader=flaky_loader)
        results = engine.search(text_query="hello")
        assert _names(results) == {path_b.name}
    print("OK  test_bad_note_skipped_gracefully")


def test_result_shape_includes_folder_path():
    idx = _build_index([("a.imgnote", "Tytuł A", "Klienci/X", [])])
    engine = SearchEngine(idx, "/root", note_loader=_never_call)
    [result] = engine.search()
    assert result == {"filepath": "/root/a.imgnote", "title": "Tytuł A", "folder_path": "Klienci/X"}
    print("OK  test_result_shape_includes_folder_path")


def test_note_loader_wired_through_note_cache_reuses_cache():
    """Odpowiedź na pytanie 'czy nie sprawdzamy najpierw cache?': note_loader
    spięty z NoteCache.get_full sprawdza cache SAM, SearchEngine nic o tym
    nie wie. Dowód: notatka już w cache zostaje znaleziona mimo złego hasła
    zaszytego w loaderze — bo dla niej loader w ogóle nie sięga do
    ImageNoteFile.load_from_file. Nie-cache'owana notatka z tym samym złym
    hasłem zostaje po cichu pominięta (jak każdy błąd loadera w search())."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path_cached = _make_note(root, "Cached", "hello world")
        path_uncached = _make_note(root, "Uncached", "hello world")

        cache = NoteCache()
        # symulacja: ta notatka była już otwarta wcześniej w sesji (albo
        # dostała darmową hydratację z IndexManager podczas reperacji)
        cache.get_full(str(path_cached), PASSWORD, os.path.getmtime(path_cached))

        idx = _build_index([
            (path_cached.name, "Cached", "", []),
            (path_uncached.name, "Uncached", "", []),
        ])

        def loader(p):
            return cache.get_full(p, "zle_haslo", os.path.getmtime(p)).text

        engine = SearchEngine(idx, str(root), note_loader=loader)
        results = engine.search(text_query="hello")

        assert _names(results) == {path_cached.name}
    print("OK  test_note_loader_wired_through_note_cache_reuses_cache")


def test_match_text_modes():
    assert match_text("", "cokolwiek") is True
    assert match_text('"dokładna fraza"', "to jest dokładna fraza tutaj") is True
    assert match_text('"dokładna fraza"', "dokładna inna fraza") is False
    assert match_text("ala, kot", "ala ma kota") is True
    assert match_text("ala, pies", "ala ma kota") is False
    assert match_text("KOT", "ala ma kota") is True  # case-insensitive substring
    print("OK  test_match_text_modes")


def main():
    test_tag_only_query_never_calls_note_loader()
    test_empty_query_returns_everything_without_decrypting()
    test_boolean_combinators()
    test_partial_case_insensitive_tag_match()
    test_text_only_query_decrypts_all_candidates()
    test_combined_tag_and_text_narrows_before_decrypting()
    test_bad_note_skipped_gracefully()
    test_result_shape_includes_folder_path()
    test_note_loader_wired_through_note_cache_reuses_cache()
    test_match_text_modes()
    print("\nWszystkie testy przeszły.")


if __name__ == "__main__":
    main()
