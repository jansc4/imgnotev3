"""Test etapu 2 audytu: P4 (update_note_text, image_base64=None), P6 (html w
get_note), S5 (równoległe zapisy + miniatury bez wyjątków i z kompletnym indeksem)."""
import base64
import io
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image
from backendV4.api import Api

PW = "haslo"


def png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 10, 10)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


with tempfile.TemporaryDirectory() as tmp:
    api = Api()
    api._password = PW
    api._root_folders.append(tmp)
    api._setup_root(tmp)
    img = png_b64()

    fp = api.save_note(tmp, "T", "**stary**", ["a"], img, None)
    res = api.update_note_text(fp, "# nowy")
    assert "<h1>nowy</h1>" in res["html"]
    note = api.get_note(fp)
    assert note["text"] == "# nowy" and note["image_base64"] == img and note["tags"] == ["a"]
    assert "<h1>nowy</h1>" in note["html"]

    api.save_note(tmp, "T2", "x", ["b"], None, fp)          # None = obraz bez zmian
    assert api.get_note(fp)["image_base64"] == img
    api.save_note(tmp, "T3", "x", ["b"], "", fp)            # "" = usuń obraz
    assert api.get_note(fp)["image_base64"] == ""
    print("OK  P4/P6")

    errors, paths = [], []

    def writer(i):
        try:
            paths.append(api.save_note(tmp, f"N{i}", "t", [f"t{i % 3}"], img, None))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def reader():
        try:
            for _ in range(20):
                for p in list(paths):
                    api.get_note_thumbnail(p)
                api.list_all_notes()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(24)]
    threads += [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    mgr = api._index_managers[tmp]
    assert len(mgr.index.notes) == 25, len(mgr.index.notes)
    print("OK  S5 równoległe zapisy/odczyty")
print("OK test_api_stage2")
