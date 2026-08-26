# 📋 Plan Migracji ImgNote v2 → v3 (pywebview)

## Cel
Przepisanie GUI z PyQt6 na pywebview (HTML/CSS/JS frontend + Python backend).
Powód: v2 ma niestabilne, mocno "ręcznie sklejone" GUI (zwłaszcza drag&drop
w `add_page.py`), które w web-technologiach jest prostsze i natywne.

Zasada: logika biznesowa (szyfrowanie, tagi, wyszukiwanie, ekstrakcja
dokumentów) przenoszona ~1:1. Cały GUI pisany od zera.

Źródło: `/home/jan/projects/imgnotev2/`

---

## Status ogólny: 🟡 W toku — Etapy 0–5 zrobione, zostaje Etap 7 (testy/polish)

---

## Etap 0 — Decyzja ws. DnD (bez osobnego PoC)

Zamiast dedykowanego etapu testowego: DnD obrazka z przeglądarki (transfer
URL, standardowe HTML5 `dataTransfer`) implementowane wprost w Etapie 4 jako
zwykła funkcjonalność — nie wymaga wcześniejszej weryfikacji, bo nie zależy
od natywnej integracji systemu plików.

DnD pliku z systemu (Eksplorator/Nautilus) — **pominięte jako osobny temat**.
Jeśli natywny DnD zadziała "za darmo" (pywebview ≥5.0, `pywebviewFullPath`),
dobrze. Jeśli nie — `window.create_file_dialog()` jako jedyna i wystarczająca
ścieżka dodawania pliku z dysku, bez dalszej pracy nad polishem DnD.

**Status:** ✅ Zdecydowane i **potwierdzone lokalnie** — Etap 0a (URL) i
schowek działają w 100%; Etap 0b (DnD z systemu) działa tylko dla .jpg, nie
dla .png na tym systemie (GTK/WebKit) — zaakceptowane jako znany limit,
fallback (dialog pliku) pokrywa wszystkie formaty

---

## Etap 1 — Szkielet projektu

- [x] `pyproject.toml` / `uv` init dla v3 (Python >=3.14, jak w v2)
- [x] Dodanie zależności: `pywebview`, `requests` (pobieranie obrazów z URL)
      + przeniesione z v2:
      `cryptography`, `markdown`, `odfpy`, `pymupdf`, `python-docx`, `striprtf`
- [x] Struktura katalogów:
  ```
  ImgnoteV3/
    backend/          # logika, bez UI (api.py — klasa Api dla js_api)
    frontend/          # html/css/js (index.html, style.css, app.js)
    main.py            # entry point, tworzy okno pywebview
  ```
- [x] Minimalne okno pywebview ładujące `index.html`
      (smoke-test: `Api.ping()` wywoływane z `app.js` po `pywebviewready`)

**Status:** ✅ Zrobione — `uv run python main.py` uruchamia okno
(nieprzetestowane wizualnie w tej sesji — uruchom lokalnie, by potwierdzić)

---

## Etap 2 — Port logiki backendowej (~640 linii, niska złożoność)

Przenoszone z minimalnymi zmianami — brak zależności od Qt:

- [x] `image_note_file.py` (109 linii) — format pliku `.imgnote`, AES-GCM/Scrypt
      → bez zmian, format pliku zostaje kompatybilny wstecz (przetestowano
      round-trip z hasłem i bez hasła)
- [x] `search_engine.py` (231 linii) — wyszukiwanie po notatkach
      → bez zmian architektonicznych (przetestowano match_tags/match_text)
- [x] `doc_extractor.py` (119 linii) — ekstrakcja tekstu z pdf/docx/odt/rtf
      → bez zmian, import OK
- [x] `natsort.py` (34 linii) — natural sort
      → bez zmian, przetestowano nsorted()
- [x] `tag_registry.py` (83→107 linii) — skanowanie tagów w tle
      → **refaktor wykonany**: `QThread`/`pyqtSignal` zamienione na
        `threading.Thread` + callbacki `on_progress(current, total)` i
        `on_updated(tags)`. Podłączenie do `window.evaluate_js()` (raportowanie
        postępu do frontendu) zaplanowane na Etap 3 — na razie moduł czysty,
        bez zależności od pywebview/js_api. Przetestowano: skan 3 plików
        `.imgnote`, poprawne zliczanie tagów, wątek kończy się poprawnie.

**Status:** ✅ Zrobione — wszystkie moduły w `backend/`, testy przechodzą

---

## Etap 3 — Definicja `js_api` (kontrakt backend ↔ frontend)

Jedna klasa Python wystawiana do JS przez `webview.create_window(..., js_api=Api())`.
Zaimplementowane w `backend/api.py`, przetestowane end-to-end (save/list/get/
search/delete notatek, tagi w tle, zmiana tytułu = zmiana nazwy pliku):

- [x] `login(password) -> bool`
- [x] `list_root_folders() / add_root_folder() / remove_root_folder(folder)`
      (`add_root_folder` używa natywnego dialogu `window.create_file_dialog`)
- [x] `get_folder_tree() -> list[dict]` (rekurencyjne drzewo podfolderów)
- [x] `create_subfolder(parent, name) / delete_folder(folder)`
- [x] `list_notes(folder) -> list[dict]` (tytuł+ścieżka, bez obrazu — lekkie)
- [x] `collect_all_notes() -> list[str]` (do wyszukiwania po wszystkich folderach)
- [x] `get_note(filepath) -> dict` (pełna treść + tagi + obraz base64)
- [x] `get_note_thumbnail(filepath) -> str` (sam obraz, do lazy-loading miniatur)
- [x] `save_note(folder, title, text, tags, image_base64, original_filepath)`
      (tytuł = nazwa pliku jak w v2; zmiana tytułu = rename przez usunięcie
      starego pliku po udanym zapisie nowego)
- [x] `delete_note(filepath)`
- [x] `search_notes(tag_query, text_query) -> list[dict]`
      **UWAGA:** synchroniczne, MVP — v2 miał wątkowy `_SearchWorker` ze
      streamingiem postępu; do rozważenia w Etapie 7 (polish), jeśli
      wyszukiwanie po dużej liczbie plików okaże się zauważalnie wolne
- [x] `get_known_tags() / refresh_tags()` (refresh używa `TagRegistry` z
      Etapu 2; postęp/wynik pchane do frontendu przez `window.evaluate_js`
      → `window.onTagScanProgress` / `window.onTagsUpdated`)
- [x] `extract_doc(path) -> dict {text, info}`
- [x] `fetch_url_image(url) -> dict {image_base64, content_type}` — **Etap 0a**,
      obsługuje priorytetowy scenariusz DnD obrazka z przeglądarki
- [x] `fetch_url_text(url) -> dict {text}` — import tekstu z linku
- [x] `open_file_dialog() -> str|None` — fallback dla Etapu 0b (DnD z systemu)

Nie zaimplementowane w tym etapie (odłożone do Etapu 4, kiedy budujemy Tag
Manager UI): `export_tags_json` / `import_tags_json` / rename-merge-delete
tagów masowo po wszystkich plikach — wymaga dokładnego przejrzenia
`tag_manager.py` (362 linie), nie czytane jeszcze w szczegółach.

**Status:** ✅ Zrobione — `main.py` przekazuje referencję okna do `Api`
(`api.set_window(window)`) dla `evaluate_js` i natywnych dialogów

---

## Etap 4 — Frontend: strony (przepisanie ~2360 linii GUI Qt)

Odpowiedniki stron v2, kolejność wg priorytetu. Architektura: prosty router
SPA bez frameworka/bundlera (`core.js`: `AppState`, `callApi()`, `registerView`/
`showView`), pliki widoków ładowane jako zwykłe `<script>` (bez modułów ES —
bezpieczniejsze przy `file://` w pywebview).

- [x] **Login** (`frontend/views/login.js`) — formularz hasła, wywołuje
      `Api.login()`, przechodzi do galerii
- [x] **Main/Galeria** (`frontend/views/gallery.js`) — toolbar, pasek
      wyszukiwania (debounce 300ms), drzewo folderów (rekurencyjne renderowanie
      `get_folder_tree`), siatka notatek z lazy-loading miniatur (sekwencyjne
      wywołania `get_note_thumbnail` — odpowiednik `_ThumbBatchLoader` bez
      wątku Qt, bo w JS i tak działa asynchronicznie w event loopie).
      Podłączone: dodawanie/tworzenie folderów, wyszukiwanie, odświeżanie
      tagów w tle (`window.onTagsUpdated`/`onTagScanProgress` z Etapu 3).
      Przyciski do "+ Nowa notatka" i kliknięcie karty notatki nawigują do
      widoków `editor`/`viewer` — jeszcze nie zaimplementowanych (pokazują
      placeholder "nie zaimplementowany" z `core.js`, bez wywalania się).
- [x] **Add/Edytuj notatkę** (`frontend/views/editor.js`, odpowiednik
      `add_page.py`, 770 linii w v2 — MVP, bez pełnej maszyny stanów Qt):
      - strefa obrazu: **Etap 0a** (priorytet) — drop URL z przeglądarki →
        `fetch_url_image`; **Etap 0b** (fallback) — drop pliku z systemu
        (jeśli jest `pywebviewFullPath`) → `read_binary_file`, albo zwykły
        `FileReader` gdy path niedostępna; klik na strefę → natywny
        `open_file_dialog`
      - wklejanie obrazu ze schowka (Ctrl+V, Clipboard API) — prostsze niż
        w Qt, jeden listener na `document`, aktywny tylko gdy `editor` jest
        bieżącym widokiem
      - strefa tekstu: drop pliku (poprzez `extract_doc` + `pywebviewFullPath`)
        lub drop linku (`fetch_url_text`), z potwierdzeniem zamiany istniejącej
        treści
      - bulk tagi: `Api.parse_bulk_tags()` — port 1:1 `_parse_bulk_tags` z v2,
        przetestowany
      - **tagi jako chipy** (zaktualizowano po feedbacku): zielone bąbelki
        klikalne do zaznaczania/odznaczania (multi-select), przycisk "− Usuń"
        kasuje zaznaczone naraz, własne autopodpowiedzi filtrowane jak
        `QCompleter` w v2 (klik = natychmiastowe dodanie chipu) — pełny port
        UX z `add_page.py`, nie uproszczenie
      - zapis przez `save_note` (Etap 3), powrót do galerii z odświeżonym
        folderem
- [x] **Viewer** (`frontend/views/viewer.js`, odpowiednik `viewer_page.py`,
      548 linii — MVP): lista plików (folder + bezpośrednie podfoldery, przez
      nowy `Api.collect_viewer_files` — port 1:1 v2 `_collect_files`),
      podgląd obrazu (bez zoom/pan — **świadomie pominięte w MVP**, `viewer_page.py`
      miał niestandardowy `ZoomableImage` z przeciąganiem/scrollem, ~120 linii;
      do rozważenia w Etapie 7 jeśli okaże się potrzebne), render Markdown
      przez nowy `Api.render_markdown` (ten sam silnik/rozszerzenia co v2),
      edycja inline (toggle/zapisz/anuluj), nawigacja prev/next, usuwanie,
      "Edytuj metadane" → `editor`. **Świadomie pominięte:** eksport do
      PNG/MD na dysk (`_export` w v2) — nieczęsto używana funkcja, do Etapu 7
      jeśli będzie potrzebna. Uwaga naprawiona podczas budowy: zapis notatki
      musi używać folderu **zawierającego dany plik** (może być podfolder
      zebrany przez `collect_viewer_files`), nie zawsze folderu głównego —
      wyliczane z `filepath` przez string split, nie ze stałego stanu widoku.
- [x] **Tag Manager** (`frontend/views/tag_manager.js`, odpowiednik
      `tag_manager.py`, 362 linie): rename/scal/usuń tag — zapisuje
      bezpośrednio do wszystkich plików `.imgnote` (`Api._apply_tag_transform`,
      port `_apply` z v2, bez `QProgressDialog` — MVP), licznik użycia tagu,
      eksport JSON (ten sam format co v2: version/exported_at/tags/
      usage_count/total_files_scanned) z natywnym `save_tags_dialog`, import
      JSON z podglądem podsumowania przed potwierdzeniem
      (`preview_import_tags_json`/`confirm_import_tags` — **tylko aktualizuje
      listę znanych tagów, nie zapisuje do plików**, zgodnie z zachowaniem v2).
      Wszystkie operacje na tagach przetestowane end-to-end na realnych
      plikach `.imgnote` (rename→merge→delete→export→import do drugiej
      instancji Api) — testy przechodzą.

**Status:** ✅ **Etap 4 zamknięty w całości** — Login, Galeria, Editor,
Viewer, Tag Manager gotowe

---

## Etap 5 — Styl / motyw

- [x] Przenieść motyw z `main.py` (QSS: ciemne tło #1e1e1e + zielone akcenty
      #4caf50) na CSS — kompletne dla wszystkich 5 widoków (login, galeria,
      editor, viewer, tag manager), zmienne CSS w `:root`

**Status:** ✅ Zrobione — dokończone razem z Tag Managerem (ostatnim widokiem)

---

## Etap 6 — Rozszerzenie do przeglądarki

**Poza zakresem v3.** Osobny pomysł na kolejną wersję — `browser-extension`
zostaje nietknięte, temat wraca gdy pomysł dojrzeje (osobny plan/dyskusja).

**Status:** ⏸️ Odłożone, poza zakresem tego planu

---

## Etap 7 — Testy i polish

- [ ] Round-trip formatu pliku: notatka zapisana w v2 czyta się w v3 i odwrotnie
- [ ] Testy DnD na docelowym systemie operacyjnym
- [ ] Testy szyfrowania/hasła (błędne hasło, brak hasła, duże obrazy)
- [ ] Testy importu/eksportu tagów JSON
- [ ] Sprawdzić `_patch.py` / `_patch_fetch.py` z v2 — do zweryfikowania, co
      łatają i czy dotyczy to również v3
- [ ] (opcjonalnie) Zoom/pan obrazu w Viewerze — pominięte w MVP Etapu 4,
      dodać jeśli okaże się potrzebne w praktyce
- [ ] (opcjonalnie) Eksport notatki do PNG+MD na dysk — pominięte w MVP
      Viewera, dodać jeśli będzie potrzebne
- [ ] (opcjonalnie) Streaming postępu wyszukiwania — `search_notes` w Etapie 3
      jest synchroniczne; dodać jeśli wyszukiwanie po dużej liczbie plików
      okaże się zauważalnie wolne

**Status:** 🔴 Nierozpoczęte

---

## Decyzje podjęte

- **Format pliku `.imgnote`:** bez zmian, zostaje identyczny jak w v2
  (`VERSION = 2` w `image_note_file.py`) — pełna kompatybilność wsteczna,
  moduł przenoszony 1:1 (patrz Etap 2).
- **`browser-extension`:** odłożone na później — osobny pomysł na kolejną
  wersję (poza zakresem v3). Zostaje na razie nietknięte w v2, nie wchodzi
  do zakresu tej migracji. Etap 6 w tym planie — nieaktualny, do usunięcia
  lub przeniesienia do osobnego planu gdy temat dojrzeje.

---

## Log zmian

- **2026-07-08** — Utworzono plan na bazie analizy `imgnotev2` (~2900 linii,
  PyQt6). v3 na razie pusty katalog, brak kodu.
- **2026-07-08** — Doprecyzowano Etap 0: priorytetem jest DnD obrazka z
  przeglądarki (transfer URL, niskie ryzyko, spójne na Win/Linux), a nie DnD
  pliku z systemu (transfer pliku, wyższe ryzyko, różnice GTK/QT i X11/Wayland
  na Linuxie). DnD z systemu jako drugorzędne z fallbackiem na file dialog.
- **2026-07-08** — Zdecydowano: pomijamy osobny PoC dla DnD. URL-based DnD
  (browser → obraz) implementowany bezpośrednio w Etapie 4 jako zwykła
  funkcja, bez wcześniejszego testu. DnD z systemu plików bez dedykowanej
  pracy — `create_file_dialog()` jako w pełni wystarczający fallback. Etap 0
  zamknięty, start prac od Etapu 1.
- **2026-07-08** — Rozstrzygnięto otwarte pytania: format `.imgnote` bez
  zmian (pełna kompatybilność z v2). `browser-extension` odłożone — osobny
  pomysł na kolejną wersję, poza zakresem v3. Etap 6 oznaczony jako odłożony.
- **2026-07-08** — Etap 1 zrobiony: `uv init`, zależności zainstalowane
  (pywebview 6.2.1, requests, cryptography 49, markdown, odfpy, pymupdf,
  python-docx, striprtf). Struktura `backend/` (`api.py` z `Api.ping()`) +
  `frontend/` (`index.html`, `style.css` z motywem ciemny+zielony z v2,
  `app.js` ze smoke-testem `js_api`). `main.py` tworzy okno pywebview.
  Kompilacja OK. Do zrobienia lokalnie: uruchomić `uv run python main.py`
  i wizualnie potwierdzić, że okno się otwiera i status pokazuje odpowiedź
  backendu.
- **2026-07-08** — Backend GUI na Linuksie: GTK niedostępne w środowisku,
  wybrano **Qt** (`pywebview[qt]`, extra doinstalowuje `pyqt6-webengine`).
  PyQt6 6.11.0 już było obecne jako zależność pywebview; teraz dołożony
  silnik webengine potrzebny do renderowania. Uzasadnione też tym, że v2
  już używało PyQt6 — sprawdzona biblioteka na tym systemie. Import
  `webview` działa poprawnie.
- **2026-07-08** — Etap 2 zrobiony: przeniesiono `image_note_file.py`,
  `search_engine.py`, `doc_extractor.py`, `natsort.py` 1:1 do `backend/`.
  Zrefaktorowano `tag_registry.py`: `QThread`/`pyqtSignal` → `threading.Thread`
  + callbacki `on_progress`/`on_updated`. Wszystkie moduły przetestowane
  (round-trip formatu pliku z/bez hasła, parser wyszukiwania, natural sort,
  skan tagów w wątku w tle) — testy przechodzą.
- **2026-07-08** — Etap 3 zrobiony: pełny `Api` w `backend/api.py` (sesja,
  foldery główne + drzewo podfolderów, notatki: list/get/save/delete/
  thumbnail, wyszukiwanie, tagi podpięte pod `TagRegistry` z `evaluate_js`,
  import dokumentów, pobieranie obrazu/tekstu z URL — Etap 0a, dialog pliku
  jako fallback dla Etapu 0b). `main.py` przekazuje referencję okna do `Api`.
  Przetestowano end-to-end na tymczasowym folderze: zapis→lista→odczyt→
  zmiana tytułu (rename)→wyszukiwanie→tagi w tle→usunięcie. Odłożone do
  Etapu 4: masowe operacje na tagach (export/import JSON, rename/merge/
  delete po wszystkich plikach) — wymagają przejrzenia `tag_manager.py`
  (362 linie, jeszcze nie czytane w szczegółach).
- **2026-07-08** — Etap 4 rozpoczęty: architektura frontendu — `core.js`
  (stan, router `registerView`/`showView`, wrapper `callApi`), widoki jako
  zwykłe `<script>` (bez modułów ES, bezpieczniejsze przy `file://`).
  Zaimplementowano i przetestowano składniowo: **Login** (`views/login.js`)
  i **Main/Galeria** (`views/gallery.js` — toolbar, wyszukiwanie z debounce,
  drzewo folderów, siatka notatek z lazy-loading miniatur, podłączone tagi
  w tle z Etapu 3). Nawigacja do `editor`/`viewer` bezpiecznie pokazuje
  placeholder do czasu zbudowania tych widoków. Motyw CSS (Etap 5) rozwijany
  na bieżąco przy każdym widoku, nie osobnym blokiem na końcu.
- **2026-07-08** — Editor gotowy (`views/editor.js`): dopisano do `Api`
  dwie metody uzupełniające kontrakt z Etapu 3 — `read_binary_file(path)`
  (odczyt dowolnego pliku jako base64, do DnD z systemu i dialogu plików)
  i `parse_bulk_tags(raw)` (port `_parse_bulk_tags` z v2). Obie przetestowane.
  Zaimplementowano priorytetowy scenariusz Etap 0a (drop obrazka z
  przeglądarki = URL → `fetch_url_image`) oraz fallback Etap 0b (drop/dialog
  pliku z systemu → `read_binary_file`, z degradacją do `FileReader` gdy
  `pywebviewFullPath` niedostępne). Dodano wklejanie obrazu ze schowka i
  drop strefę tekstu (dokument/URL). Składnia JS i cały backend
  zweryfikowane (`node --check`, `py_compile`).
- **2026-07-08** — Test manualny Etap 0 (lokalnie, przez użytkownika):
  ✅ obraz z URL (Etap 0a, priorytet) — działa. ✅ wklejanie ze schowka —
  działa. ⚠️ DnD z systemu (Etap 0b) — działał dla .jpg, nie działał dla
  .png (prawdopodobnie GTK/WebKit próbuje natywnie "otworzyć" rozpoznany
  format obrazu na `dragenter`, zanim JS przechwyci zdarzenie). Poprawka:
  dodano `dragenter` z `preventDefault`/`stopPropagation` na strefie drop
  oraz globalny "guard" na `document` (`dragover`/`drop`) blokujący domyślną
  nawigację przeglądarki dla całego okna, aktywny tylko gdy widok `editor`
  jest otwarty (podpinany/odpinany jak `_editorPasteHandler`). Do
  potwierdzenia lokalnie: czy png teraz działa przez DnD z systemu.
- **2026-07-08** — Potwierdzone: poprawka (`dragenter` + document-level guard)
  **nie rozwiązała** problemu z DnD .png z systemu na tym Linuksie/GTK.
  **Decyzja: zamykamy temat, zgodnie z pierwotną filozofią Etapu 0b** — to
  drugorzędny, opcjonalny kanał, nie blocker. Fallback (klik na strefę →
  `open_file_dialog` → `read_binary_file`) działa niezależnie od formatu
  pliku i pozostaje głównym sposobem dodawania obrazu z dysku. DnD z systemu
  zostaje jako "bonus, gdy działa" (potwierdzone dla .jpg), bez dalszych
  prac diagnostycznych nad .png — nie inwestujemy więcej czasu w ten kanał.
- **2026-07-08** — Viewer gotowy (`views/viewer.js`): dopisano do `Api`
  `collect_viewer_files` (port `_collect_files` z v2) i `render_markdown`
  (ten sam silnik co v2, przetestowane). MVP bez zoom/pan obrazu i bez
  eksportu do PNG/MD — świadomie odłożone do Etapu 7. Naprawiono podczas
  budowy: zapis notatki musi liczyć folder z `filepath`, nie z góry przyjętego
  folderu widoku (bo pliki mogą być w podfolderze). JS i backend
  zweryfikowane (`node --check`, `py_compile`).
- **2026-07-08** — Tag Manager gotowy (`views/tag_manager.js`) — **Etap 4
  zamknięty w całości, wszystkie 5 widoków gotowe**. Dopisano do `Api`:
  `_apply_tag_transform`, `count_tag_usage`, `rename_tag`, `merge_tag`,
  `delete_tag` (zapisują do plików `.imgnote`), `export_tags_json`/
  `preview_import_tags_json`/`confirm_import_tags` (import aktualizuje tylko
  listę znanych tagów, bez zapisu do plików — zgodnie z v2),
  `save_tags_dialog`/`open_tags_dialog`. Przetestowano end-to-end na
  realnych plikach: rename→merge→delete→export→import do drugiej instancji
  `Api` — wszystko się zgadza. Etap 5 (styl) też zamknięty przy okazji —
  CSS kompletne dla wszystkich widoków.
- **2026-07-08** — Szlifowanie (feedback użytkownika, podobał się mechanizm
  z v2 `add_page.py`): zamieniono prosty input tagów w Editorze na wierny
  port UX z v2 — **chipy** (zielone bąbelki, `.tag-chip`, klik = toggle
  zaznaczenia zamiast Qt multi-select), **bulk auto-separacja** po Enter/
  przycisku "+ Dodaj" (przez `Api.parse_bulk_tags`, już istniejące z Etapu 4),
  **własne autopodpowiedzi** (`updateTagAutocomplete`, filtrowanie
  `MatchContains` jak `QCompleter` w v2, klik na podpowiedź = natychmiastowe
  dodanie chipu i wyczyszczenie pola — użyty `mousedown` zamiast `click`,
  żeby zdążyć przed `blur` chowającym dropdown), **multi-select + "− Usuń"**
  do usuwania kilku tagów naraz. Stan `_editorTags`/`_editorSelectedTags`
  zastąpił poprzednie parsowanie jednego pola tekstowego przy zapisie.
  Zweryfikowane `node --check`, brak martwych odwołań do starego `#note-tags`.
- **2026-07-18** — Poprawki po testach użytkownika (4 zgłoszenia):
  1) **Scroll całej aplikacji** — `#app` miał `padding: 24px` podczas gdy
     widoki liczą `height: 100vh`, więc treść zawsze wystawała ~48px poza
     okno. Naprawiono: `html,body{height:100%;overflow:hidden}`,
     `#app{height:100%;box-sizing:border-box}`, wszystkie `100vh` → `100%`
     + `box-sizing:border-box` na `.login-screen/.gallery/.editor/.viewer/
     .tag-manager`. `.editor-left` i `.tm-body` dostały `overflow-y:auto`
     jako zabezpieczenie na wypadek zbyt małego okna.
  2) **Wyszukiwanie bardzo wolne** — `search_notes`/`TagRegistry._run`/
     `count_tag_usage`/`_apply_tag_transform`/`export_tags_json` odszyfrowywały
     KAŻDY plik `.imgnote` od zera przy każdym wywołaniu (Scrypt KDF na
     plik to dziesiątki ms — przy typowaniu w polu szukania to się mnożyło).
     Naprawiono: `Api._load_note_cached(fp)` — cache `filepath -> (mtime,
     ImageNoteFile)` współdzielony przez wszystkie te metody + wstrzyknięty
     do `TagRegistry` jako `note_loader`. Cache inwalidowany w `save_note`/
     `delete_note`/`_apply_tag_transform` po zapisie. Pierwsze przejście
     nadal odszyfrowuje wszystko (nieuniknione), ale kolejne wyszukiwania/
     skany tagów dla niezmienionych plików są już praktycznie darmowe.
  3) **Brak "Zapisz i następna" + checkbox "zachowaj tagi"** — funkcja z v2
     `add_page.py` (`_save_and_clear` + `carry_tags_cb`) nie została
     przeniesiona do V3. Dodano: przycisk `#btn-save-next` obok `#btn-save`
     i checkbox `#carry-tags-cb` przy etykiecie sekcji tagów. Po zapisie
     formularz czyści tytuł/tekst/obraz, zostaje w Editorze (nie wraca do
     galerii), `currentOriginalFilepath` resetuje się do `null` (kolejny
     zapis tworzy nowy plik, nie nadpisuje poprzedniego), tagi zachowane
     tylko gdy checkbox zaznaczony.
  4) **DnD z obcej strony: "pobrano obraz" ale nic się nie pokazało** —
     `fetch_url_image` nie sprawdzał, czy odpowiedź serwera faktycznie jest
     obrazem (v2 `_UrlFetcher` to robił). Strona z ochroną przed
     hotlinkowaniem (Cloudflare itp.) mogła oddać 200 OK z HTML-em
     (challenge/błąd) zamiast pliku — backend meldował sukces, a `<img>`
     dostawał base64 zakodowanego HTML-a i nic nie renderował. Naprawiono:
     walidacja magic bytes (JPEG/PNG/GIF/WEBP/BMP) po pobraniu, z czytelnym
     błędem do frontendu jeśli to nie obraz.

- **2026-07-30** — Dodano widok **Home** (przycisk 🏠 nad drzewem folderów w
  `gallery.js`): płaska lista notatek ze wszystkich folderów naraz, z paskiem
  sortowania (📁 wg folderu z nagłówkami sekcji / 🕒 najnowsze wg mtime /
  🎲 losowo). Backend: `Api.list_all_notes(sort_by)` w `api.py` — nie dotyka
  `_note_cache`/Scrypt (tytuł = nazwa pliku jak w `list_notes`, koszt czysto
  na poziomie systemu plików). Otwieranie notatki z Home przekazuje do
  Viewera realny folder notatki (nie sentinel `__HOME__`), żeby prev/next
  (`collect_viewer_files`) działało poprawnie. Dodawanie notatki/podfolderu
  zablokowane, gdy aktywny jest Home (trzeba wybrać konkretny folder).
  Przy okazji: miniatury w siatce ładowane były sekwencyjnie dla WSZYSTKICH
  notatek naraz (`loadThumbnailsSequentially`), niezależnie od widoczności —
  przy Home z dużą liczbą notatek to zbędny narzut. Zamieniono na
  `IntersectionObserver` (`setupLazyThumbnails`/`loadOneThumbnail`):
  miniatura ładuje się dopiero gdy karta wjeżdża w obszar siatki (z
  `rootMargin: 400px` na wyprzedzenie), obserwator odpinany per-karta po
  załadowaniu i resetowany przy każdym renderze siatki.


- **2026-08-25** — Nawigacja w drzewie folderów (`gallery.js` + `style.css`,
  bez zmian w backendzie — `Api.get_folder_tree`/`_build_tree_node` już
  zwracał pełne, rekurencyjne drzewo):
  1) **Drzewo domyślnie zwinięte.** `AppState.expandedFolders` (Set ścieżek)
     trzyma, które węzły są rozwinięte — pusty na starcie = wszystko zwinięte.
     Każdy folder z dziećmi dostaje strzałkę `▸`/`▾` (`.tree-toggle`) klikalną
     niezależnie od etykiety folderu (osobny listener, `stopPropagation`, żeby
     nie wywoływał wyboru folderu). `expandAncestors(path)` rozwija w drzewie
     ścieżkę do aktualnie wybranego folderu (przy `selectFolder` i przy
     przywracaniu widoku) — resztę gałęzi zostawia zwiniętą.
  2) **Breadcrumb w headerze.** Nowy pasek `#breadcrumb-bar` pod toolbarem
     galerii, aktualizowany w `renderFolderTree()` → `renderBreadcrumb()`.
     Segmenty liczone od folderu głównego (root z `AppState.rootFolders`) w
     dół (`breadcrumbSegments`), każdy poza ostatnim to link (`selectFolder`),
     plus 🏠 na start (`selectHome`) — pozwala wyjść 1–2 poziomy wyżej bez
     szukania w (teraz zwiniętym) drzewie.
  3) **Podfoldery widoczne w siatce notatek.** Folder bez bezpośrednich
     notatek, ale z podfolderami, przestał wyglądać na "pusty": `showFolderNotes`
     doszukuje węzeł drzewa (`findTreeNode`) i przekazuje jego `children` do
     `renderNotesGrid`, która renderuje je jako klikalne kafelki `.folder-tile`
     (📁 + nazwa) na górze siatki, przed notatkami — klik nawiguje przez
     `selectFolder`. Pusty komunikat "Brak notatek…" pokazuje się tylko gdy
     ZARÓWNO notatek, jak i podfolderów jest zero. Kafelki folderów wyłączone
     z lazy-loadingu miniatur (`.thumb-card:not(.folder-tile)`), bo nie mają
     `data-idx`/miniatury.
