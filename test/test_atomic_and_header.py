"""Test S4 (zapis atomowy) i S6 (walidacja nagłówka, ochrona przed downgrade)."""
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backendV4.image_note_file import ImageNoteFile
from backendV4.index_manager import IndexManager

PW = "haslo"


def main():
    d = tempfile.mkdtemp()
    path = os.path.join(d, ImageNoteFile.new_filename())
    ImageNoteFile("stary tekst", b"IMG", ["t"], "Tytul", "/").save_to_file(path, PW)
    before = open(path, "rb").read()

    # S4: wyjątek w trakcie zapisu zostawia stary plik i nie zostawia .tmp
    with mock.patch("os.fsync", side_effect=OSError("brak miejsca")):
        try:
            ImageNoteFile("nowy", b"X", [], "T", "/").save_to_file(path, PW)
            raise AssertionError("oczekiwano wyjątku")
        except OSError:
            pass
    assert open(path, "rb").read() == before, "stary plik zmieniony"
    assert [n for n in os.listdir(d) if n.endswith(".tmp")] == [], "osierocony .tmp"
    assert ImageNoteFile.load_from_file(path, PW).text == "stary tekst"

    # S6: jawny plik odrzucony przy podanym haśle
    plain = os.path.join(d, ImageNoteFile.new_filename())
    ImageNoteFile("jawny", b"", [], "P", "/").save_to_file(plain, None)
    try:
        ImageNoteFile.load_from_file(plain, PW)
        raise AssertionError("downgrade przepuszczony")
    except ValueError:
        pass

    # S6: ucięty plik -> ValueError, nie struct.error
    short = os.path.join(d, ImageNoteFile.new_filename())
    open(short, "wb").write(b"IMNG\x03")
    try:
        ImageNoteFile.load_from_file(short, PW)
        raise AssertionError("krótki plik przepuszczony")
    except ValueError:
        pass

    # IndexManager: zepsute pliki nie blokują startu, dobra notatka jest w indeksie
    mgr = IndexManager(d, password=PW)
    mgr.load_or_rebuild()
    assert os.path.basename(path) in mgr.index.notes
    assert os.path.basename(plain) not in mgr.index.notes
    mgr.save()
    print("OK test_atomic_and_header")


if __name__ == "__main__":
    main()
