# Uruchomienie: QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" uv run python test/e2e_offscreen.py
"""E2E etapu 2 (offscreen): prawdziwy pywebview + Api na tymczasowym vaulcie.
Sprawdza: CSP nie psuje mostu, miniatury się ładują (G1), viewer używa blob: (G2),
html z get_note (P6), szybkie next nie zostawia starej notatki, edycja inline (P4)."""
import io
import json
import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import webview  # noqa: E402
from PIL import Image  # noqa: E402
from backendV4.api import Api  # noqa: E402
from backendV4.image_note_file import ImageNoteFile  # noqa: E402

PW = "haslo-e2e"
root = tempfile.mkdtemp(prefix="imgnote_e2e_")
paths = []
for i in range(12):
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (i * 20, 100, 200 - i * 10)).save(buf, format="PNG")
    p = os.path.join(root, ImageNoteFile.new_filename())
    ImageNoteFile(f"# Nagłówek {i}\n\n| a | b |\n|-|-|\n| 1 | 2 |", buf.getvalue(), [f"t{i % 3}"],
                  f"Notatka {i:02d}", "").save_to_file(p, PW)
    paths.append(p)

api = Api()
window = webview.create_window("e2e", os.path.join(REPO, "frontend", "index.html"), js_api=api)
api.set_window(window)
results, failures = [], []


def js(code, timeout=10):
    return window.evaluate_js(code)


def wait_for(expr, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if js(expr):
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def check(name, cond):
    (results if cond else failures).append(name)


def run():
    try:
        check("most gotowy", wait_for("!!(window.pywebview && window.pywebview.api && window.pywebview.api.login)", 20))
        js("""window.__errs=[]; window.addEventListener('error',e=>__errs.push('err:'+e.message));
              document.addEventListener('securitypolicyviolation',e=>__errs.push('csp:'+e.violatedDirective+' '+e.blockedURI));
              const ce=console.error; console.error=(...a)=>{__errs.push('console:'+a.join(' ')); ce(...a)}; 1""")
        js(f"""window.__login=null; callApi('login', {json.dumps(root)}, {json.dumps(PW)})
               .then(r=>{{window.__login=r; showView('gallery');}}).catch(e=>window.__login='ERR '+e); 1""")
        check("login", wait_for("window.__login && window.__login.password_verified === true", 30))
        js("document.getElementById('home-node') && document.getElementById('home-node').click(); 1")
        check("karty w siatce", wait_for("document.querySelectorAll('.thumb-card:not(.folder-tile)').length === 12", 15))
        check("miniatury załadowane", wait_for("document.querySelectorAll('.thumb-card .thumb-img img').length >= 6", 30))
        check("G3 content-visibility", js("getComputedStyle(document.querySelector('.thumb-card')).contentVisibility === 'auto'"))

        js("document.querySelector('.thumb-card:not(.folder-tile)').click(); 1")
        check("viewer obraz blob:", wait_for("(document.querySelector('#v-image-viewport img')||{}).src?.startsWith('blob:')", 15))
        check("viewer html (P6)", wait_for("document.querySelector('#v-md-view table') !== null", 10))
        # szybkie next x5: na końcu ma być notatka o indeksie _viewerIdx
        js("(async()=>{for(let k=0;k<5;k++){ if(_viewerIdx<_viewerFiles.length-1){_viewerIdx++; loadCurrentNote();} } })(); 1")
        time.sleep(3)
        dbg = js("JSON.stringify({shown: document.getElementById('v-title').textContent, idx: _viewerIdx, files: _viewerFiles.map(f=>f.title), counter: document.getElementById('v-counter').textContent})")
        d = json.loads(dbg)
        check("G2 brak nadpisania starszą odpowiedzią", d["shown"] == d["files"][d["idx"]])
        check("blob URL po przełączeniu", bool(js("(document.querySelector('#v-image-viewport img')||{}).src?.startsWith('blob:')")))

        cur = js("_viewerFiles[_viewerIdx].filepath")
        js("toggleEdit(); document.getElementById('v-md-editor').value='## Po edycji <img src=x onerror=\"window.__xss=1\">'; window.__saved=false; saveInlineEdit().then(()=>window.__saved=true); 1")
        check("edycja inline zapisana", wait_for("window.__saved === true", 15))
        check("edycja: html odświeżony", bool(js("document.querySelector('#v-md-view h2')?.textContent.trim() === 'Po edycji'")))
        note = api.get_note(cur)
        check("edycja: tekst na dysku + obraz zachowany", note["text"].startswith("## Po edycji") and len(note["image_base64"]) > 100)
        time.sleep(0.5)
        check("XSS nie wykonany", not js("window.__xss === 1"))
        js("closePopup(); 1")
        errs = js("window.__errs") or []
        check(f"brak błędów JS/CSP {errs}", len(errs) == 0)
    except Exception as e:  # noqa: BLE001
        failures.append(f"wyjątek: {e!r}")
    finally:
        print("PASS:", *results, sep="\n  ")
        print("FAIL:", *failures, sep="\n  ")
        window.destroy()


webview.start(run, gui="qt")
sys.exit(1 if failures else 0)
