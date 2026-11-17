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

        # ── etap 4 ──
        esc = "document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); 1"
        # G6: Esc zamyka tylko popup, zaznaczenie w galerii zostaje
        js("(()=>{const c=document.querySelector('.thumb-card:not(.folder-tile)'); c.dispatchEvent(new MouseEvent('click',{bubbles:true,ctrlKey:true}));})(); 1")
        check("zaznaczenie Ctrl+klik", wait_for("AppState.selectedNotes.size === 1", 5))
        js("document.querySelectorAll('.thumb-card:not(.folder-tile)')[1].click(); 1")
        check("viewer otwarty", wait_for("_popupStack.length === 1 && !!document.querySelector('#v-md-view table')", 10))
        check("G5 fokus w popupie", bool(js("!!document.activeElement.closest('.popup-layer')")))
        js(esc)
        check("G6 Esc zamknął popup", wait_for("_popupStack.length === 0", 5))
        check("G6 zaznaczenie przetrwało Esc", bool(js("AppState.selectedNotes.size === 1")))
        js(esc)
        check("G6 drugi Esc czyści zaznaczenie", wait_for("AppState.selectedNotes.size === 0", 5))

        # G5: Enter na karcie otwiera viewer; G8: niezapisana edycja pyta przed zamknięciem
        js("(()=>{const c=document.querySelectorAll('.thumb-card:not(.folder-tile)')[2]; c.focus(); c.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})(); 1")
        check("G5 Enter na karcie", wait_for("_popupStack.length === 1 && !!document.querySelector('#v-md-view table')", 10))
        js("toggleEdit(); document.getElementById('v-md-editor').value += ' zmiana'; 1")
        js(esc)
        check("G8 pytanie o niezapisane zmiany", wait_for("_popupStack.length === 2 && !!document.querySelector('.dialog-ok')", 5))
        js(esc)  # Esc w pytaniu = anuluj
        check("G8 anuluj zostawia viewer", wait_for("_popupStack.length === 1", 5) and bool(js("_viewerEditMode === true")))
        js(esc)
        wait_for("_popupStack.length === 2", 5)
        js("document.querySelector('.dialog-ok').click(); 1")  # Zamknij bez zapisu
        check("G8 potwierdzenie zamyka", wait_for("_popupStack.length === 0", 5))

        # G4: confirmPopup / promptPopup
        js("window.__c=null; confirmPopup('Test?').then(v=>window.__c=v); 1")
        wait_for("!!document.querySelector('.dialog-ok')", 5)
        js("document.querySelector('.dialog-ok').click(); 1")
        check("G4 confirmPopup -> true", wait_for("window.__c === true", 5))
        js("window.__p=undefined; promptPopup('Nazwa?', 'abc').then(v=>window.__p=v); 1")
        wait_for("!!document.querySelector('.dialog-input')", 5)
        js("const i=document.querySelector('.dialog-input'); i.value='xyz'; i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true})); 1")
        check("G4 promptPopup Enter -> wartość", wait_for("window.__p === 'xyz'", 5))
        js("window.__p=undefined; promptPopup('Nazwa?', 'abc').then(v=>window.__p=v); 1")
        wait_for("!!document.querySelector('.dialog-input')", 5)
        js(esc)
        check("G4 promptPopup Esc -> null", wait_for("window.__p === null", 5))
        js("toast('test', 'error'); 1")
        check("G9 toast", bool(js("!!document.querySelector('.toast.toast-error')")))

        # G10: wyszukiwanie po tagu
        js("const t=document.getElementById('search-tag'); t.value='t0'; t.dispatchEvent(new Event('input',{bubbles:true})); 1")
        check("G10 wyniki wyszukiwania", wait_for("document.getElementById('search-status').textContent.includes('wynik') && document.querySelectorAll('.thumb-card:not(.folder-tile)').length === 4", 10))
        check("G10 brak wskaźnika po zakończeniu", bool(js("!document.getElementById('search-status').classList.contains('busy')")))

        # Edytor: G8 (bez zmian zamyka od razu, ze zmianą pyta), P4 (zapis bez zmiany obrazu go zachowuje)
        ed_fp = paths[5]
        ed_folder = js(f"(_viewerFiles.find(f=>f.filepath==={json.dumps(ed_fp)})||{{}}).folder") or js("AppState.notes[0].folder")
        js(f"openEditorPopup({json.dumps(ed_folder)}, {json.dumps(ed_fp)}); 1")
        check("edytor otwarty", wait_for("!!document.getElementById('note-title') && document.getElementById('note-title').value !== ''", 10))
        check("edytor: fokus na tytule", bool(js("document.activeElement && document.activeElement.id === 'note-title'")))
        js(esc)
        check("edytor bez zmian zamyka się od razu", wait_for("_popupStack.length === 0", 5))
        js(f"openEditorPopup({json.dumps(ed_folder)}, {json.dumps(ed_fp)}); 1")
        wait_for("!!document.getElementById('note-title') && document.getElementById('note-title').value !== ''", 10)
        js("document.getElementById('note-title').value = 'Zmieniony tytuł'; 1")
        js(esc)
        check("edytor ze zmianą pyta", wait_for("_popupStack.length === 2", 5))
        js(esc)
        wait_for("_popupStack.length === 1", 5)
        img_before = api.get_note(ed_fp)["image_base64"]
        js("document.getElementById('btn-save').click(); 1")
        check("edytor zapisał", wait_for("_popupStack.length === 0", 10))
        after = api.get_note(ed_fp)
        check("P4 edytor: tytuł zmieniony, obraz zachowany", after["title"] == "Zmieniony tytuł" and after["image_base64"] == img_before and len(img_before) > 100)

        # S7: blokada sesji i ponowne wejście
        js("document.getElementById('btn-lock').click(); 1")
        check("S7 widok logowania po blokadzie", wait_for("_currentView === 'login'", 10))
        check("S7 backend zapomniał hasło", api._password == "" and not api._index_managers)
        check("S7 folder podstawiony", bool(js(f"document.getElementById('login-folder-path').textContent === {json.dumps(root)}")))
        js(f"document.getElementById('login-pw').value={json.dumps(PW)}; document.getElementById('login-btn').click(); 1")
        check("S7 ponowne logowanie", wait_for("_currentView === 'gallery'", 20))

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
