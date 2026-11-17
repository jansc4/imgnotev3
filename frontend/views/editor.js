/**
 * views/editor.js — odpowiednik v2 add_page.py (skrócone MVP).
 * Obsługuje: nową notatkę i edycję istniejącej.
 *
 * Krok 8 (punkt 16): przerobione na popup (fundament: popup.js, wide:true —
 * potrzebuje realnej szerokości na dwie kolumny + drop zone'y). Zniknęło
 * "← Wróć" — zamykanie to teraz ✕/Escape/klik w tło z samego popupu.
 * Efekt uboczny architektury (nie osobna decyzja): dawniej "← Wróć" zawsze
 * lądował w Galerii, nawet gdy wszedłeś tu z Viewera (edycja metadanych),
 * bo showView("editor",...) podmieniał Viewer w routerze. W popupie Viewer
 * nigdy nie znika spod spodu, więc zamknięcie bez zapisu wraca dokładnie
 * tam, gdzie byłeś. Zapis ("Zapisz") celowo NIETKNIĘTY — nadal nawiguje do
 * Galerii jak dawniej, żeby nie zmieniać więcej niż trzeba w jednym kroku.
 *
 * DnD obrazka (priorytet — Etap 0a): przeciągnięcie z przeglądarki niesie
 * URL w dataTransfer, nie bajty — pobieramy przez fetch_url_image().
 * DnD z systemu (Etap 0b, drugorzędne): jeśli pywebview doda
 * pywebviewFullPath do pliku, czytamy go przez read_binary_file().
 */

let _editorImageBase64 = "";
let _editorPasteHandler = null;
let _editorDragGuardHandlers = null;
let _editorTags = [];              // lista dodanych tagów (chipy), jak QListWidget w v2
let _editorSelectedTags = new Set(); // zaznaczone chipy do usunięcia (multi-select)
// Zastępuje dawny _currentView !== "editor" — popup nie ustawia _currentView
// z core.js (widok pod spodem zostaje aktywny), więc guard w wirePasteHandler
// potrzebuje własnej flagi cyklu życia popupu.
let _editorPopupOpen = false;

function wireDragGuard() {
  if (_editorDragGuardHandlers) {
    document.removeEventListener("dragover", _editorDragGuardHandlers.dragover);
    document.removeEventListener("drop", _editorDragGuardHandlers.drop);
  }
  // Blokuje domyślną nawigację przeglądarki dla CAŁEGO okna podczas DnD
  // (nie tylko wewnątrz stref drop) — zapobiega "otwarciu" upuszczonego pliku
  // przez WebKit/GTK zamiast przekazania go do naszej strefy.
  const dragover = (e) => e.preventDefault();
  const drop = (e) => {
    if (!e.target.closest(".drop-zone")) e.preventDefault();
  };
  document.addEventListener("dragover", dragover);
  document.addEventListener("drop", drop);
  _editorDragGuardHandlers = { dragover, drop };
}

function openEditorPopup(folder, filepath) {
  const isEdit = !!filepath;
  _editorPopupOpen = true;

  openPopup({
    title: isEdit ? "Edytuj notatkę" : "Nowa notatka",
    wide: true,
    onClose: () => {
      _editorPopupOpen = false;
    },
    async render(container) {
      let initial = { title: "", text: "", tags: [], image_base64: "" };
      if (isEdit) {
        initial = await callApi("get_note", filepath);
      }
      _editorImageBase64 = initial.image_base64 || "";

      container.innerHTML = `
        <div class="editor">
          <div class="toolbar">
            <span id="editor-title-label" class="app-title">${isEdit ? "Edytuj notatkę" : "Nowa notatka"}</span>
            <div class="toolbar-spacer"></div>
            <button id="btn-paste-note" class="btn" title="Wklej zserializowaną notatkę (JSON) ze schowka">📋 Wklej notatkę</button>
            <button id="btn-save" class="btn btn-primary">💾 Zapisz</button>
            <button id="btn-save-next" class="btn btn-primary" title="Zapisz i od razu zacznij kolejną notatkę w tym samym folderze">💾 Zapisz i następna</button>
          </div>
          <div class="editor-body">
            <div class="editor-left">
              <div id="image-drop" class="drop-zone" tabindex="0">
                <div id="image-preview" class="image-preview-inner">
                  ${_editorImageBase64
                    ? `<img src="data:image/png;base64,${_editorImageBase64}" alt="">`
                    : `<span class="drop-hint">Przeciągnij obraz z przeglądarki, wklej (Ctrl+V) obraz lub zserializowaną notatkę (JSON), albo kliknij, by wybrać plik</span>`}
                </div>
              </div>
              <div id="text-drop" class="drop-zone drop-zone-text">
                <span class="drop-hint">Przeciągnij plik tekstowy (.txt/.md/.pdf/.docx/…) lub link, albo kliknij, by wybrać plik — zastąpi treść</span>
              </div>
            </div>
            <div class="editor-right">
              <input id="note-title" class="editor-input" placeholder="Tytuł notatki" value="${escapeAttr(initial.title)}">
              <div class="tag-section-header">
                <label class="tag-section-label">Tagi: <span class="drop-hint">jeden tag lub lista: projekt, ważne, archiwum</span></label>
                <label class="carry-tags-label" title="Po 'Zapisz i następna' tagi zostaną zachowane">
                  <input type="checkbox" id="carry-tags-cb"> Zachowaj tagi do następnej
                </label>
              </div>
              <div class="tag-input-row">
                <input id="tag-input" class="editor-input" placeholder="Wpisz tag lub listę (Enter = dodaj)…" autocomplete="off">
                <button id="tag-add-btn" class="btn">+ Dodaj</button>
                <button id="tag-remove-btn" class="btn">− Usuń</button>
                <div id="tag-autocomplete" class="tag-autocomplete" hidden></div>
              </div>
              <div id="tag-chips" class="tag-chips"></div>
              <textarea id="note-text" class="editor-textarea" placeholder="Treść (Markdown)…">${escapeHtml(initial.text || "")}</textarea>
            </div>
          </div>
          <p id="editor-status" class="editor-status"></p>
        </div>
      `;

      _editorTags = [...(initial.tags || [])];
      _editorSelectedTags = new Set();
      renderTagChips();
      wireEditorEvents(folder, isEdit ? filepath : null);
    },
  });
}

function updateImagePreview() {
  const el = document.getElementById("image-preview");
  if (!el) return;
  el.innerHTML = _editorImageBase64
    ? `<img src="data:image/png;base64,${_editorImageBase64}" alt="">`
    : `<span class="drop-hint">Przeciągnij obraz z przeglądarki, wklej (Ctrl+V) lub kliknij, by wybrać plik</span>`;
}

// ── Tagi: chipy (odpowiednik v2 QListWidget MultiSelection) ──────────

function renderTagChips() {
  const el = document.getElementById("tag-chips");
  if (!el) return;
  el.innerHTML = _editorTags.map((t) => {
    const selected = _editorSelectedTags.has(t) ? " tag-chip-selected" : "";
    return `<span class="tag-chip${selected}" data-tag="${escapeAttr(t)}">${escapeHtml(t)}</span>`;
  }).join("");
  el.querySelectorAll(".tag-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const tag = chip.dataset.tag;
      if (_editorSelectedTags.has(tag)) _editorSelectedTags.delete(tag);
      else _editorSelectedTags.add(tag);
      renderTagChips();
    });
  });
}

function addTagItem(tag) {
  tag = tag.trim();
  if (!tag || _editorTags.includes(tag)) return;
  _editorTags.push(tag);
}

async function addTagsBulk(raw) {
  if (!raw.trim()) return;
  const parts = await callApi("parse_bulk_tags", raw);
  parts.forEach(addTagItem);
  renderTagChips();
}

function removeSelectedTags() {
  if (_editorSelectedTags.size === 0) return;
  _editorTags = _editorTags.filter((t) => !_editorSelectedTags.has(t));
  _editorSelectedTags.clear();
  renderTagChips();
}

// ── Autopodpowiedzi (odpowiednik v2 QCompleter, MatchContains) ───────

function updateTagAutocomplete(query) {
  const dropdown = document.getElementById("tag-autocomplete");
  const q = query.trim().toLowerCase();
  if (!q) {
    hideTagAutocomplete();
    return;
  }
  const matches = (AppState.knownTags || [])
    .filter((t) => t.toLowerCase().includes(q) && !_editorTags.includes(t))
    .slice(0, 10);
  if (matches.length === 0) {
    hideTagAutocomplete();
    return;
  }
  dropdown.innerHTML = matches
    .map((t) => `<div class="tag-autocomplete-item" data-tag="${escapeAttr(t)}">${escapeHtml(t)}</div>`)
    .join("");
  dropdown.hidden = false;
  dropdown.querySelectorAll(".tag-autocomplete-item").forEach((item) => {
    // mousedown (nie click) — żeby zdążyć zanim input straci focus i schowa dropdown
    item.addEventListener("mousedown", (e) => {
      e.preventDefault();
      addTagItem(item.dataset.tag);
      renderTagChips();
      hideTagAutocomplete();
      document.getElementById("tag-input").value = "";
      document.getElementById("tag-input").focus();
    });
  });
}

function hideTagAutocomplete() {
  const dropdown = document.getElementById("tag-autocomplete");
  if (dropdown) dropdown.hidden = true;
}



function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function wireImageDropZone() {
  const zone = document.getElementById("image-drop");
  const statusEl = document.getElementById("editor-status");

  // Niektóre kombinacje GTK/WebKit próbują natywnie "otworzyć" rozpoznane
  // typy obrazów (np. png) już na dragenter, zanim nasz kod obsłuży drop.
  // Łapiemy zdarzenie najwcześniej jak się da i blokujemy domyślną akcję.
  zone.addEventListener("dragenter", (e) => {
    e.preventDefault();
    e.stopPropagation();
    zone.classList.add("drop-zone-active");
  });

  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("drop-zone-active");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("drop-zone-active"));

  zone.addEventListener("drop", async (e) => {
    e.preventDefault();
    zone.classList.remove("drop-zone-active");
    const dt = e.dataTransfer;

    // Etap 0a (priorytet): obraz z przeglądarki = URL, nie bajty.
    const url = dt.getData("text/uri-list") || dt.getData("text/plain");
    if (url && /^https?:\/\//i.test(url.trim())) {
      statusEl.textContent = "⏳ Pobieram obraz z URL…";
      try {
        const res = await callApi("fetch_url_image", url.trim());
        _editorImageBase64 = res.image_base64;
        updateImagePreview();
        statusEl.textContent = "✅ Obraz pobrany.";
      } catch (err) {
        statusEl.textContent = "❌ Błąd pobierania obrazu: " + err;
      }
      return;
    }

    // Etap 0b (drugorzędne): plik z systemu.
    if (dt.files && dt.files.length > 0) {
      const file = dt.files[0];
      await loadImageFromDroppedFile(file, statusEl);
    }
  });

  zone.addEventListener("click", async () => {
    const path = await callApi("open_file_dialog");
    if (!path) return;
    statusEl.textContent = "⏳ Wczytuję plik…";
    try {
      _editorImageBase64 = await callApi("read_binary_file", path);
      updateImagePreview();
      statusEl.textContent = "✅ Obraz wczytany.";
    } catch (err) {
      statusEl.textContent = "❌ Błąd wczytywania: " + err;
    }
  });
}

async function loadImageFromDroppedFile(file, statusEl) {
  statusEl.textContent = "⏳ Wczytuję plik…";
  try {
    // pywebview ≥5.0 dodaje pełną ścieżkę — pozwala czytać przez backend.
    const fullPath = file.pywebviewFullPath;
    _editorImageBase64 = fullPath
      ? await callApi("read_binary_file", fullPath)
      : await fileToBase64(file); // fallback: czysty odczyt w przeglądarce
    updateImagePreview();
    statusEl.textContent = "✅ Obraz wczytany.";
  } catch (err) {
    statusEl.textContent = "❌ Błąd wczytywania obrazu: " + err;
  }
}

function wirePasteHandler() {
  // Usuń poprzedni listener (widok mógł być otwierany wielokrotnie).
  if (_editorPasteHandler) {
    document.removeEventListener("paste", _editorPasteHandler);
  }
  _editorPasteHandler = async (e) => {
    if (!_editorPopupOpen) return;
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;

    // 1) Obraz w schowku (np. Ctrl+C na screenie) — bez zmian.
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        const blob = item.getAsFile();
        _editorImageBase64 = await fileToBase64(blob);
        updateImagePreview();
        const statusEl = document.getElementById("editor-status");
        if (statusEl) statusEl.textContent = "✅ Obraz wklejony ze schowka.";
        e.preventDefault();
        return;
      }
    }

    // 2) Zserializowana notatka (JSON z rozszerzenia ImgNote Tag Extractor).
    // UWAGA: getData() jest synchroniczne — trzeba to sprawdzić przed
    // ewentualnym await, żeby preventDefault() zdążył zablokować wstawienie
    // surowego JSON-a do pola tekstowego, jeśli tam akurat mamy focus.
    const plainText = e.clipboardData.getData("text/plain");
    const note = tryParseNoteExport(plainText);
    if (note) {
      e.preventDefault();
      await importNoteFromClipboard(note);
      return;
    }

    // 3) Fallback: QtWebEngine (zweryfikowane na Wayland) potrafi nie oddać
    // image/png w JS clipboardData, mimo że obraz naprawdę jest w schowku
    // systemowym (widoczny np. w `wl-paste -l`) — JS Clipboard API bywa
    // tam ślepe na dane obrazkowe. Czytamy wtedy przez Qt (backend),
    // z pominięciem ograniczeń JS-owego API.
    try {
      const b64 = await callApi("read_clipboard_image");
      if (b64) {
        e.preventDefault();
        _editorImageBase64 = b64;
        updateImagePreview();
        const statusEl = document.getElementById("editor-status");
        if (statusEl) statusEl.textContent = "✅ Obraz wklejony ze schowka (Qt).";
      }
    } catch (_) {
      // brak obrazu w schowku albo backend jeszcze niegotowy — cicho pomijamy
    }
  };
  document.addEventListener("paste", _editorPasteHandler);
}

// ── Import zserializowanej notatki (deserializacja kontraktu rozszerzenia) ──
//
// Kontrakt JSON (rozszerzenie ImgNote Tag Extractor → schowek):
//   {
//     "title":     string,          // tytuł notatki
//     "text":      string,          // treść strony (opcjonalnie)
//     "url":       string,          // adres strony źródłowej (nie obrazka!)
//     "image_url": string,          // opcjonalnie: bezpośredni link do obrazka
//     "author":    string,          // opcjonalnie
//     "tags":      string[]         // lista tagów
//   }
//
// `url` to strona, na której znaleziono obrazek — nie musi wskazywać na sam
// plik graficzny. `image_url` (gdy dodasz go w rozszerzeniu po ustaleniu
// scrapingu) to faktyczny link do pobrania. Jeśli `image_url` brak, próbujemy
// jeszcze rozpoznać po rozszerzeniu pliku w `url`.

const IMAGE_URL_RE = /\.(png|jpe?g|gif|webp|bmp|svg)(\?|#|$)/i;

function tryParseNoteExport(text) {
  if (!text) return null;
  const trimmed = text.trim();
  if (!trimmed.startsWith("{")) return null;

  let obj;
  try {
    obj = JSON.parse(trimmed);
  } catch (_) {
    return null;
  }
  if (typeof obj !== "object" || obj === null || Array.isArray(obj)) return null;

  const hasTags = Array.isArray(obj.tags);
  const hasText = typeof obj.text === "string";
  const hasTitle = typeof obj.title === "string";
  // Nie każdy skopiowany JSON to eksport notatki — wymagamy przynajmniej
  // jednego z rozpoznawalnych pól, żeby nie łapać przypadkowego wklejenia.
  if (!hasTags && !hasText && !hasTitle) return null;

  return {
    title: hasTitle ? obj.title : "",
    text: hasText ? obj.text : "",
    url: typeof obj.url === "string" ? obj.url : "",
    imageUrl: typeof obj.image_url === "string" ? obj.image_url : "",
    // Ścieżka do pliku pobranego przez chrome.downloads w rozszerzeniu —
    // najpewniejsza droga (to prawdziwy download, nie fetch, więc omija
    // WAF-y typu Cloudflare rozróżniające nawigację od requestów JS).
    // Ma pierwszeństwo przed imageBase64 i imageUrl.
    imageLocalPath: typeof obj.image_local_path === "string" ? obj.image_local_path : "",
    // Gotowe bajty obrazka (base64), pobrane w rozszerzeniu bezpośrednio
    // w kontekście strony — jeśli obecne, wygrywają nad pobieraniem przez
    // backend (fetch_url_image), bo omijają problemy z fingerprintingiem
    // (np. Cloudflare) i CORS-em, których requests.get() w Pythonie nie obejdzie.
    imageBase64: typeof obj.image_base64 === "string" ? obj.image_base64 : "",
    author: typeof obj.author === "string" ? obj.author : "",
    tags: hasTags ? obj.tags.filter((t) => typeof t === "string" && t.trim()) : [],
  };
}

function pickImageUrl(note) {
  if (note.imageUrl) return note.imageUrl;
  if (note.url && IMAGE_URL_RE.test(note.url)) return note.url;
  return "";
}

function composeImportedText(note) {
  const segments = [];
  if (note.text) segments.push(note.text.trim());
  if (note.author) segments.push(`Autor: ${note.author}`);
  if (note.url) segments.push(`Źródło: ${note.url}`);
  return segments.join("\n\n");
}

async function importNoteFromClipboard(note) {
  const statusEl = document.getElementById("editor-status");
  const titleEl = document.getElementById("note-title");
  const textEl = document.getElementById("note-text");

  const hasExistingContent =
    titleEl.value.trim() || textEl.value.trim() || _editorTags.length || _editorImageBase64;
  if (hasExistingContent && !confirm("Formularz nie jest pusty. Zastąpić dane zaimportowaną notatką?")) {
    return;
  }

  if (statusEl) statusEl.textContent = "⏳ Importuję notatkę ze schowka…";

  if (note.title) titleEl.value = note.title;
  textEl.value = composeImportedText(note);

  _editorTags = [...new Set(note.tags)];
  _editorSelectedTags = new Set();
  renderTagChips();

  // 1) Najlepsza droga: plik pobrany przez chrome.downloads w rozszerzeniu
  //    (prawdziwy download, nie fetch — omija WAF-y typu Cloudflare).
  //    Czytamy go tą samą metodą co przy ręcznym wyborze pliku.
  if (note.imageLocalPath) {
    try {
      _editorImageBase64 = await callApi("read_binary_file", note.imageLocalPath);
      updateImagePreview();
      if (statusEl) statusEl.textContent = "✅ Zaimportowano notatkę wraz z obrazem.";
      return;
    } catch (err) {
      if (statusEl) {
        statusEl.textContent = `⚠️ Nie udało się odczytać pobranego pliku (${note.imageLocalPath}): ${err}`;
      }
      // spadamy do kolejnych fallbacków zamiast przerywać cały import
    }
  }

  // 2) Gotowe bajty obrazka z rozszerzenia (fetch w kontekście rozszerzenia
  //    lub strony — omija CORS, ale nadal może oberwać tym samym WAF-em
  //    co zwykły fetch, więc to słabszy fallback niż chrome.downloads).
  if (note.imageBase64) {
    _editorImageBase64 = note.imageBase64;
    updateImagePreview();
    if (statusEl) statusEl.textContent = "✅ Zaimportowano notatkę wraz z obrazem.";
    return;
  }

  // 3) Ostatni fallback: brak obu powyższych (starsza wersja rozszerzenia) —
  //    próbujemy pobrać obrazek przez backend. To może nie przejść przez tę
  //    samą ochronę (Cloudflare itp.), której backend nie obejdzie.
  const imageUrl = pickImageUrl(note);
  if (imageUrl) {
    try {
      // note.url to prawdziwy adres strony ze zdjęciem (nie domena CDN-a) —
      // to jest właściwy Referer, jakiego oczekują serwery z ochroną
      // przed hotlinkowaniem.
      const res = await callApi("fetch_url_image", imageUrl, note.url || undefined);
      _editorImageBase64 = res.image_base64;
      updateImagePreview();
      if (statusEl) statusEl.textContent = "✅ Zaimportowano notatkę wraz z obrazem.";
    } catch (err) {
      if (statusEl) {
        statusEl.textContent = `⚠️ Zaimportowano tekst i tagi, ale nie udało się pobrać obrazu: ${err}`;
      }
    }
  } else if (statusEl) {
    statusEl.textContent = note.url
      ? "✅ Zaimportowano notatkę (link nie wygląda na bezpośredni obraz — dodaj go ręcznie)."
      : "✅ Zaimportowano notatkę ze schowka.";
  }
}

function wireTextDropZone() {
  const zone = document.getElementById("text-drop");
  const textEl = document.getElementById("note-text");
  const statusEl = document.getElementById("editor-status");

  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("drop-zone-active");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("drop-zone-active"));

  zone.addEventListener("drop", async (e) => {
    e.preventDefault();
    zone.classList.remove("drop-zone-active");
    const dt = e.dataTransfer;

    const url = dt.getData("text/uri-list") || dt.getData("text/plain");
    if (url && /^https?:\/\//i.test(url.trim())) {
      await importTextResult(
        () => callApi("fetch_url_text", url.trim()).then((r) => ({ text: r.text, info: "URL" })),
        textEl, statusEl
      );
      return;
    }

    if (dt.files && dt.files.length > 0) {
      const file = dt.files[0];
      const fullPath = file.pywebviewFullPath;
      if (!fullPath) {
        statusEl.textContent = "❌ Brak pełnej ścieżki pliku (DnD z systemu niedostępny tutaj).";
        return;
      }
      await importTextResult(() => callApi("extract_doc", fullPath), textEl, statusEl);
    }
  });

  zone.addEventListener("click", async () => {
    const path = await callApi("open_file_dialog");
    if (!path) return;
    await importTextResult(() => callApi("extract_doc", path), textEl, statusEl);
  });
}

async function importTextResult(fetchFn, textEl, statusEl) {
  statusEl.textContent = "⏳ Importuję tekst…";
  try {
    const { text, info } = await fetchFn();
    if (textEl.value.trim() && !confirm("Pole tekstu nie jest puste. Zastąpić treść?")) {
      return;
    }
    textEl.value = text;
    statusEl.textContent = `✅ Zaimportowano (${info}).`;
  } catch (err) {
    statusEl.textContent = "❌ Błąd importu: " + err;
  }
}

function wireEditorEvents(folder, originalFilepath) {
  // Po "Zapisz i następna" zostajemy w tym samym widoku tworząc kolejną,
  // nową notatkę — od tego momentu originalFilepath musi być null (inaczej
  // kolejny zapis nadpisałby poprzednio utworzoną notatkę zamiast założyć
  // nową), stąd zmienna zamiast bezpośredniego użycia parametru.
  let currentOriginalFilepath = originalFilepath;

  wireDragGuard();
  wireImageDropZone();
  wireTextDropZone();
  wirePasteHandler();
  wireTagSection();

  document.getElementById("btn-paste-note").addEventListener("click", async () => {
    const statusEl = document.getElementById("editor-status");
    let clipboardText;
    try {
      clipboardText = await callApi("read_clipboard_text");
    } catch (err) {
      statusEl.textContent = `❌ Nie udało się odczytać schowka: ${err}`;
      return;
    }
    const note = tryParseNoteExport(clipboardText);
    if (!note) {
      statusEl.textContent = "❌ Schowek nie zawiera rozpoznawalnej notatki (JSON z rozszerzenia).";
      return;
    }
    await importNoteFromClipboard(note);
  });

  /**
   * Wspólna walidacja + zapis dla "Zapisz" i "Zapisz i następna".
   * Zwraca true przy sukcesie (żeby wywołujący wiedział, czy kontynuować).
   */
  async function doSave() {
    const statusEl = document.getElementById("editor-status");
    const title = document.getElementById("note-title").value.trim();
    const text = document.getElementById("note-text").value;

    if (!title) {
      statusEl.textContent = "❌ Tytuł nie może być pusty.";
      return false;
    }
    if (!folder) {
      statusEl.textContent = "❌ Brak wybranego folderu — wróć do galerii i wybierz folder.";
      return false;
    }

    statusEl.textContent = "⏳ Zapisuję…";
    try {
      await callApi(
        "save_note", folder, title, text, _editorTags, _editorImageBase64, currentOriginalFilepath
      );
      return true;
    } catch (err) {
      statusEl.textContent = "❌ Błąd zapisu: " + err;
      return false;
    }
  }

  document.getElementById("btn-save").addEventListener("click", async () => {
    if (!(await doSave())) return;
    closePopup();
    showView("gallery");
    await selectFolder(folder);
  });

  document.getElementById("btn-save-next").addEventListener("click", async () => {
    if (!(await doSave())) return;

    // Od teraz to nowa notatka — kolejny zapis nie powinien nadpisać
    // dopiero co utworzonego pliku.
    currentOriginalFilepath = null;
    const titleLabel = document.getElementById("editor-title-label");
    if (titleLabel) titleLabel.textContent = "Nowa notatka";
    // Bez globalnego id="popup-title" (stos warstw — mogłoby ich być kilka
    // naraz) — dobieramy tytuł TEJ konkretnej warstwy przez najbliższego
    // przodka .popup-layer, względem znanego elementu treści.
    const layerTitle = titleLabel && titleLabel.closest(".popup-layer")?.querySelector(".popup-title");
    if (layerTitle) layerTitle.textContent = "Nowa notatka";

    const keepTags = document.getElementById("carry-tags-cb").checked;
    document.getElementById("note-title").value = "";
    document.getElementById("note-text").value = "";
    _editorImageBase64 = "";
    updateImagePreview();
    _editorTags = keepTags ? [..._editorTags] : [];
    _editorSelectedTags = new Set();
    renderTagChips();

    document.getElementById("editor-status").textContent = "✅ Zapisano. Dodaj kolejną notatkę.";
    document.getElementById("note-title").focus();
  });
}

function wireTagSection() {
  const input = document.getElementById("tag-input");

  input.addEventListener("keydown", async (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      await addTagsBulk(input.value);
      input.value = "";
      hideTagAutocomplete();
    }
  });
  input.addEventListener("input", () => updateTagAutocomplete(input.value));
  input.addEventListener("blur", () => setTimeout(hideTagAutocomplete, 150));

  document.getElementById("tag-add-btn").addEventListener("click", async () => {
    await addTagsBulk(input.value);
    input.value = "";
    hideTagAutocomplete();
  });

  document.getElementById("tag-remove-btn").addEventListener("click", removeSelectedTags);
}
