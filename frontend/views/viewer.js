/**
 * views/viewer.js — odpowiednik v2 viewer_page.py (MVP: bez eksportu do
 * PNG/MD — oznaczone jako TODO w planie migracji).
 *
 * Krok 8 (runda folder rename/move, 2026-08-28): przerobione na popup —
 * spójność stylu z resztą nakładek (Tag Manager, Ustawienia, Editor) i
 * rozwiązuje "problem powrotu": Editor otwierany z Viewera (Edytuj
 * metadane) leży teraz NAD nim (prawdziwy stos w popup.js), więc
 * zamknięcie Edytora naturalnie wraca do Viewera, zamiast zawsze lądować
 * w Galerii. Zniknęło "← Wróć" — zamykanie to ✕/Escape/klik w tło.
 */

let _viewerFiles = [];
let _viewerIdx = 0;
let _viewerEditMode = false;
let _viewerCurrentText = "";
let _viewerSplitRatio = 0.5;      // udział panelu obrazu w szerokości (0.15–0.85), trzyma się między notatkami
let _viewerSplitterHandlers = null; // { move, up } — do odpięcia przy ponownym wejściu w viewer
let _viewerZoom = 1;
let _viewerPanX = 0;
let _viewerPanY = 0;
let _viewerPanHandlers = null;    // { move, up } — do odpięcia przy ponownym wejściu w viewer
let _viewerKeydownHandler = null; // do odpięcia przy ponownym wejściu w viewer
// Zastępuje dawny _currentView !== "viewer" — popup nie ustawia _currentView
// z core.js (widok pod spodem zostaje aktywny), więc guard w nawigacji
// klawiaturą potrzebuje własnej flagi cyklu życia popupu (ten sam wzorzec
// co _editorPopupOpen w editor.js).
let _viewerPopupOpen = false;
const ZOOM_MIN = 0.15;
const ZOOM_MAX = 10;
const ZOOM_STEP = 1.25;

function openViewerPopup(folder, filepath) {
  _viewerPopupOpen = true;

  openPopup({
    title: "Podgląd notatki",
    wide: true,
    onClose: () => {
      _viewerPopupOpen = false;
    },
    async render(container) {
      _viewerFiles = await callApi("collect_viewer_files", folder);
      if (_viewerFiles.length === 0) _viewerFiles = [{ filepath, title: "", folder }];
      _viewerIdx = Math.max(0, _viewerFiles.findIndex((f) => f.filepath === filepath));
      _viewerEditMode = false;

      container.innerHTML = `
        <div class="viewer">
          <div class="toolbar">
            <span id="v-title" class="app-title"></span>
            <span id="v-counter" class="viewer-counter"></span>
            <div class="toolbar-spacer"></div>
            <button id="v-toggle-edit" class="btn">✏️ Edytuj</button>
            <button id="v-edit-meta" class="btn">⚙️ Edytuj metadane</button>
            <button id="v-delete" class="btn">🗑️ Usuń</button>
          </div>
          <div class="viewer-body">
            <div id="v-file-list" class="viewer-file-list"></div>
            <div class="viewer-content" id="v-content">
              <div id="v-image" class="viewer-image">
                <div id="v-image-viewport" class="viewer-image-viewport"></div>
                <div class="viewer-zoom-controls">
                  <button id="v-zoom-out" class="btn btn-icon" title="Pomniejsz">−</button>
                  <span id="v-zoom-label" class="viewer-zoom-label">100%</span>
                  <button id="v-zoom-in" class="btn btn-icon" title="Powiększ">+</button>
                  <button id="v-zoom-reset" class="btn btn-icon" title="Resetuj widok">⤢</button>
                </div>
              </div>
              <div id="v-splitter" class="viewer-splitter" title="Przeciągnij, by zmienić proporcje"></div>
              <div class="viewer-text-panel">
                <div id="v-md-view" class="viewer-md-view"></div>
                <textarea id="v-md-editor" class="editor-textarea" style="display:none"></textarea>
                <div id="v-save-bar" class="viewer-save-bar" style="display:none">
                  <button id="v-save-inline" class="btn btn-primary">💾 Zapisz zmiany</button>
                  <button id="v-discard" class="btn">✕ Anuluj</button>
                </div>
              </div>
              <button id="v-prev" class="btn viewer-nav-btn viewer-nav-prev" title="Poprzedni (←)">◀</button>
              <button id="v-next" class="btn viewer-nav-btn viewer-nav-next" title="Następny (→)">▶</button>
            </div>
          </div>
          <div id="v-tags" class="viewer-tags-row"></div>
        </div>
      `;

      wireViewerEvents();
      renderFileList();
      await loadCurrentNote();
    },
  });
}

function renderFileList() {
  const el = document.getElementById("v-file-list");
  if (!el) return;
  el.innerHTML = _viewerFiles.map((f, i) => {
    const name = f.title || "(bez tytułu)";
    const active = i === _viewerIdx ? " viewer-file-active" : "";
    return `<div class="viewer-file-item${active}" data-idx="${i}" title="${escapeAttr(name)}">${escapeHtml(name)}</div>`;
  }).join("");
  el.querySelectorAll(".viewer-file-item").forEach((item) => {
    item.addEventListener("click", async () => {
      _viewerIdx = parseInt(item.dataset.idx, 10);
      await loadCurrentNote();
    });
  });
}

async function loadCurrentNote() {
  if (_viewerEditMode) discardEdit();
  const filepath = _viewerFiles[_viewerIdx].filepath;
  const total = _viewerFiles.length;

  document.getElementById("v-counter").textContent = `${_viewerIdx + 1} / ${total}`;
  document.getElementById("v-prev").disabled = _viewerIdx === 0;
  document.getElementById("v-next").disabled = _viewerIdx === total - 1;

  let note;
  try {
    note = await callApi("get_note", filepath);
  } catch (err) {
    document.getElementById("v-md-view").textContent = "Błąd odczytu: " + err;
    document.getElementById("v-image-viewport").innerHTML = "";
    document.getElementById("v-tags").innerHTML = "";
    return;
  }

  document.getElementById("v-title").textContent = note.title;
  const viewportEl = document.getElementById("v-image-viewport");
  viewportEl.innerHTML = note.image_base64
    ? `<img src="data:image/png;base64,${note.image_base64}" alt="" draggable="false">`
    : `<span class="drop-hint">Brak obrazu</span>`;
  resetZoomPan();


  _viewerCurrentText = note.text;
  const html = await callApi("render_markdown", note.text);
  document.getElementById("v-md-view").innerHTML = html;

  document.getElementById("v-tags").innerHTML = note.tags
    .map((t) => `<span class="viewer-tag-chip">${escapeHtml(t)}</span>`)
    .join("");

  renderFileList();
}

function toggleEdit() {
  _viewerEditMode = !_viewerEditMode;
  const editorEl = document.getElementById("v-md-editor");
  const viewEl = document.getElementById("v-md-view");
  const saveBarEl = document.getElementById("v-save-bar");
  const btn = document.getElementById("v-toggle-edit");

  if (_viewerEditMode) {
    editorEl.value = _viewerCurrentText;
    editorEl.style.display = "block";
    viewEl.style.display = "none";
    saveBarEl.style.display = "flex";
    btn.textContent = "👁 Podgląd";
  } else {
    discardEdit();
  }
}

function discardEdit() {
  _viewerEditMode = false;
  document.getElementById("v-md-editor").style.display = "none";
  document.getElementById("v-md-view").style.display = "block";
  document.getElementById("v-save-bar").style.display = "none";
  document.getElementById("v-toggle-edit").textContent = "✏️ Edytuj";
}

async function saveInlineEdit() {
  const filepath = _viewerFiles[_viewerIdx].filepath;
  const newText = document.getElementById("v-md-editor").value;
  const note = await callApi("get_note", filepath);
  try {
    await callApi("save_note", note.folder, note.title, newText, note.tags, note.image_base64, filepath);
    _viewerCurrentText = newText;
    document.getElementById("v-md-view").innerHTML = await callApi("render_markdown", newText);
    discardEdit();
  } catch (err) {
    alert("Błąd zapisu: " + err);
  }
}

function wireViewerEvents() {
  wireSplitter();
  wireImageZoomPan();
  wireViewerKeyboardNav();

  document.getElementById("v-prev").addEventListener("click", async () => {
    if (_viewerIdx > 0) { _viewerIdx--; await loadCurrentNote(); }
  });
  document.getElementById("v-next").addEventListener("click", async () => {
    if (_viewerIdx < _viewerFiles.length - 1) { _viewerIdx++; await loadCurrentNote(); }
  });

  document.getElementById("v-toggle-edit").addEventListener("click", toggleEdit);
  document.getElementById("v-save-inline").addEventListener("click", saveInlineEdit);
  document.getElementById("v-discard").addEventListener("click", discardEdit);

  document.getElementById("v-edit-meta").addEventListener("click", () => {
    const current = _viewerFiles[_viewerIdx];
    openEditorPopup(current.folder, current.filepath);
  });

  document.getElementById("v-delete").addEventListener("click", async () => {
    const current = _viewerFiles[_viewerIdx];
    if (!confirm(`Czy na pewno chcesz usunąć:\n${current.title}?`)) return;
    try {
      await callApi("delete_note", current.filepath);
    } catch (err) {
      alert("Błąd usuwania: " + err);
      return;
    }
    _viewerFiles.splice(_viewerIdx, 1);
    if (_viewerFiles.length === 0) { closePopup(); return; }
    _viewerIdx = Math.min(_viewerIdx, _viewerFiles.length - 1);
    await loadCurrentNote();
  });
}

// ── Splitter: przeciąganie granicy obraz/tekst (myszką) ──────────────

function applySplitRatio() {
  const imageEl = document.getElementById("v-image");
  if (imageEl) imageEl.style.flex = `0 0 ${_viewerSplitRatio * 100}%`;
}

function wireSplitter() {
  const splitter = document.getElementById("v-splitter");
  const content = document.getElementById("v-content");
  if (!splitter || !content) return;

  applySplitRatio();

  // Widok viewer może być otwierany wielokrotnie — odepnij listenery
  // z poprzedniego wywołania, żeby nie zaśmiecać document.
  if (_viewerSplitterHandlers) {
    document.removeEventListener("mousemove", _viewerSplitterHandlers.move);
    document.removeEventListener("mouseup", _viewerSplitterHandlers.up);
  }

  let dragging = false;

  const move = (e) => {
    if (!dragging) return;
    const rect = content.getBoundingClientRect();
    let ratio = (e.clientX - rect.left) / rect.width;
    ratio = Math.min(0.85, Math.max(0.15, ratio));
    _viewerSplitRatio = ratio;
    applySplitRatio();
  };
  const up = () => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove("viewer-resizing");
  };

  splitter.addEventListener("mousedown", (e) => {
    dragging = true;
    document.body.classList.add("viewer-resizing");
    e.preventDefault();
  });
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
  _viewerSplitterHandlers = { move, up };
}

// ── Zoom/pan obrazu (kółko myszy = zoom, przeciąganie = pan) ─────────

function resetZoomPan() {
  _viewerZoom = 1;
  _viewerPanX = 0;
  _viewerPanY = 0;
  applyZoomPan();
}

function applyZoomPan() {
  const img = document.querySelector("#v-image-viewport img");
  if (img) img.style.transform = `translate(${_viewerPanX}px, ${_viewerPanY}px) scale(${_viewerZoom})`;
  const label = document.getElementById("v-zoom-label");
  if (label) label.textContent = Math.round(_viewerZoom * 100) + "%";
}

/** Zmienia zoom o `factor`, zachowując punkt (cx, cy) — względem środka
 * kontenera — nieruchomo pod kursorem (albo w centrum, dla przycisków). */
function zoomBy(factor, cx = 0, cy = 0) {
  const newZoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, _viewerZoom * factor));
  if (newZoom === _viewerZoom) return;
  const ratio = newZoom / _viewerZoom;
  _viewerPanX = cx - (cx - _viewerPanX) * ratio;
  _viewerPanY = cy - (cy - _viewerPanY) * ratio;
  _viewerZoom = newZoom;
  applyZoomPan();
}

function wireImageZoomPan() {
  const container = document.getElementById("v-image");
  if (!container) return;

  // Widok viewer może być otwierany wielokrotnie — odepnij listenery
  // z poprzedniego wywołania, żeby nie zaśmiecać document.
  if (_viewerPanHandlers) {
    document.removeEventListener("mousemove", _viewerPanHandlers.move);
    document.removeEventListener("mouseup", _viewerPanHandlers.up);
  }

  container.addEventListener("wheel", (e) => {
    if (!container.querySelector("img")) return;
    e.preventDefault();
    const rect = container.getBoundingClientRect();
    const cx = e.clientX - rect.left - rect.width / 2;
    const cy = e.clientY - rect.top - rect.height / 2;
    zoomBy(e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP, cx, cy);
  }, { passive: false });

  container.addEventListener("dblclick", () => resetZoomPan());

  let dragging = false;
  let startX = 0, startY = 0, startPanX = 0, startPanY = 0;

  container.addEventListener("mousedown", (e) => {
    if (e.target.tagName !== "IMG") return;
    dragging = true;
    startX = e.clientX;
    startY = e.clientY;
    startPanX = _viewerPanX;
    startPanY = _viewerPanY;
    container.classList.add("viewer-image-dragging");
    e.preventDefault();
  });

  const move = (e) => {
    if (!dragging) return;
    _viewerPanX = startPanX + (e.clientX - startX);
    _viewerPanY = startPanY + (e.clientY - startY);
    applyZoomPan();
  };
  const up = () => {
    if (!dragging) return;
    dragging = false;
    container.classList.remove("viewer-image-dragging");
  };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
  _viewerPanHandlers = { move, up };

  document.getElementById("v-zoom-in").addEventListener("click", () => zoomBy(ZOOM_STEP));
  document.getElementById("v-zoom-out").addEventListener("click", () => zoomBy(1 / ZOOM_STEP));
  document.getElementById("v-zoom-reset").addEventListener("click", () => resetZoomPan());
}

// ── Nawigacja strzałkami klawiatury (krok 8, punkt 9) ─────────────────

function wireViewerKeyboardNav() {
  // Widok viewer może być otwierany wielokrotnie — odepnij listener
  // z poprzedniego wywołania, żeby nie zaśmiecać document.
  if (_viewerKeydownHandler) {
    document.removeEventListener("keydown", _viewerKeydownHandler);
  }
  _viewerKeydownHandler = async (e) => {
    // _viewerPopupOpen — nie reaguj, gdy popup Viewera jest zamknięty
    // (listener mógłby przeżyć do następnego otwarcia, patrz komentarz
    // przy deklaracji zmiennej). _viewerEditMode — nie przechwytuj
    // strzałek, które akurat służą do poruszania kursorem w edycji.
    if (!_viewerPopupOpen || _viewerEditMode) return;
    if (e.key === "ArrowLeft" && _viewerIdx > 0) {
      e.preventDefault();
      _viewerIdx--;
      await loadCurrentNote();
    } else if (e.key === "ArrowRight" && _viewerIdx < _viewerFiles.length - 1) {
      e.preventDefault();
      _viewerIdx++;
      await loadCurrentNote();
    }
  };
  document.addEventListener("keydown", _viewerKeydownHandler);
}
