"""Test S2: metody js_api odrzucają ścieżki spoza vaulta i pliki nie-obrazy."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backendV4.api import Api


def expect_error(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return
    raise AssertionError(f"{fn.__name__}{args} nie rzucił ValueError")


with tempfile.TemporaryDirectory() as tmp:
    root = os.path.join(tmp, "vault")
    os.makedirs(root)
    victim = os.path.join(tmp, "secret.txt")
    open(victim, "w").write("tajne")

    api = Api()
    api._root_folders.append(root)
    fake = "0" * 32 + ".imgnote"

    expect_error(api.delete_note, victim)                                   # zła nazwa
    expect_error(api.delete_note, os.path.join(tmp, fake))                  # spoza roota
    expect_error(api.get_note, os.path.join(root, "..", fake))              # ucieczka przez ..
    expect_error(api.move_note, victim, root)
    expect_error(api.save_note, root, "t", "x", [], "", victim)             # nadpisanie obcego pliku
    expect_error(api.read_binary_file, victim)                              # nie-obraz
    expect_error(api.extract_doc, os.path.join(tmp, "x.sh"))
    expect_error(api.export_notes, [], tmp, "txt")                          # folder spoza dialogu
    expect_error(api.export_tags_json, victim)
    assert api.get_note_thumbnail(victim) == ""
    assert os.path.exists(victim), "plik ofiary usunięty"
    assert [n for n in dir(api) if not n.startswith("_") and not callable(getattr(api, n))] == []
print("OK test_api_paths")
