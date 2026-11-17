/**
 * popup.js — generyczny, warstwowy modal (krok 8, punkt 3; rozbudowa do
 * prawdziwego STOSU 2026-08-28, runda folder rename/move + context menu).
 *
 * PRAWDZIWY STOS warstw (nie jeden slot) — potrzebne odkąd Viewer i Editor
 * oba są popupami: "Edytuj metadane" w Viewerze otwiera Editor NAD
 * Viewerem, nie ZAMIAST niego. Escape/✕/klik w tło zamyka TYLKO wierzchnią
 * warstwę, ujawniając tę pod spodem — jak w każdym systemie z zagnieżdżonymi
 * modalami (np. "na pewno zamknąć?" nad już otwartym dialogiem).
 *
 * Każda warstwa to osobny <div class="popup-layer"> wewnątrz #popup-root,
 * position:fixed;inset:0 — kolejność w DOM = kolejność malowania (późniejsza
 * warstwa rysuje się na wierzchu), więc NIE trzeba ręcznie liczyć z-index
 * między warstwami. #progress-modal (z-index:1000, patrz style.css) zawsze
 * zostaje NAD wszystkimi warstwami popupu (te mają wspólne z-index:900).
 *
 * Kontrakt identyczny jak wcześniej — istniejący kod (Settings, Tag
 * Manager, Editor) działa bez zmian:
 *   openPopup({
 *     title: "Zarządzanie tagami",
 *     render: (container) => { ...zbuduj DOM w container... },
 *     onClose: () => { ...opcjonalny cleanup... },
 *     wide: true, // opcjonalnie: szerszy .popup-box
 *     fullscreen: true, // opcjonalnie: prawie cała wielkość appki (Viewer)
 *   });
 *   closePopup(); // zamyka TYLKO wierzchnią warstwę
 *   closeAllPopups(); // zamyka wszystkie naraz (rzadkie — patrz niżej)
 */

// [{ layerEl, onClose, beforeClose, returnFocus }] — [0] najstarsza, ostatnia = wierzchnia
let _popupStack = [];
let _popupSeq = 0;

const _FOCUSABLE = 'input:not([type=hidden]):not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])';

// beforeClose (G8): opcjonalne () => bool | Promise<bool>; false = nie zamykaj.
// Pytane tylko przy zamknięciu przez użytkownika (✕/Esc/tło), nie przy closePopup() z kodu.
function openPopup({ title, render, onClose, beforeClose, wide, fullscreen }) {
  const root = document.getElementById("popup-root");
  if (!root) return;

  const layer = document.createElement("div");
  layer.className = "popup-layer";
  const boxClass = fullscreen ? " popup-box-fullscreen" : wide ? " popup-box-wide" : "";
  const titleId = `popup-title-${++_popupSeq}`;
  // G5: role="dialog" + aria-modal, tytuł jako etykieta okna
  layer.innerHTML = `
    <div class="popup-box${boxClass}" role="dialog" aria-modal="true" aria-labelledby="${titleId}">
      <div class="popup-header">
        <span class="popup-title" id="${titleId}">${escapeHtml(title || "")}</span>
        <button class="btn btn-icon popup-close-btn" title="Zamknij (Esc)" aria-label="Zamknij">✕</button>
      </div>
      <div class="popup-body"></div>
    </div>
  `;
  root.appendChild(layer);
  root.hidden = false;

  layer.querySelector(".popup-close-btn").addEventListener("click", requestClosePopup);

  // Klik w tło (poza kartą) zamyka TĘ warstwę — nasłuch na samej warstwie
  // (e.target === layer, czyli kliknięto dokładnie w overlay, nie w kartę
  // wewnątrz), więc klik w select/input nie zamyka niczego przez bąbelkowanie.
  layer.addEventListener("mousedown", (e) => {
    if (e.target === layer) requestClosePopup();
  });

  _popupStack.push({
    layerEl: layer,
    onClose: onClose || null,
    beforeClose: beforeClose || null,
    returnFocus: document.activeElement,
  });

  // Escape obsługuje jeden router w core.js (G6) — tu nie ma już własnego listenera.
  const body = layer.querySelector(".popup-body");
  Promise.resolve(render(body)).then(() => focusFirstIn(body, layer));
}

// G5: fokus na pierwszym polu treści (render może być async — wołane po nim)
function focusFirstIn(body, layer) {
  if (!layer.isConnected || layer.contains(document.activeElement)) return;
  const el = body.querySelector("[autofocus]") || body.querySelector(_FOCUSABLE)
    || layer.querySelector(".popup-close-btn");
  if (el) el.focus();
}

/** Zamknięcie przez użytkownika: pyta beforeClose wierzchniej warstwy. */
async function requestClosePopup() {
  const top = _popupStack[_popupStack.length - 1];
  if (!top) return;
  if (top.beforeClose && !(await top.beforeClose())) return;
  if (_popupStack[_popupStack.length - 1] === top) closePopup();
}

/** Zamknięcie z kodu (np. po zapisie) — bez pytania beforeClose. */
function closePopup() {
  const top = _popupStack.pop();
  if (!top) return;

  top.layerEl.remove();

  if (_popupStack.length === 0) {
    const root = document.getElementById("popup-root");
    if (root) root.hidden = true;
  }

  if (top.onClose) top.onClose();
  // G5: powrót fokusu tam, skąd popup otwarto (o ile element jeszcze istnieje)
  if (top.returnFocus && top.returnFocus.isConnected && typeof top.returnFocus.focus === "function") {
    top.returnFocus.focus();
  }
}

/** Zamyka WSZYSTKIE warstwy naraz. Używane rzadko — normalne zamykanie
 * powinno iść przez closePopup() (tylko wierzchnia warstwa). */
function closeAllPopups() {
  while (_popupStack.length > 0) closePopup();
}

// ── G4: zamienniki confirm()/prompt() na stosie popupów (nie blokują wątku JS) ──

/** confirmPopup(tekst, {okLabel, danger}) -> Promise<boolean> */
function confirmPopup(message, { title = "Potwierdź", okLabel = "OK", danger = false } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => {
      if (done) return;
      done = true;
      resolve(v);
    };
    openPopup({
      title,
      onClose: () => finish(false),
      render(body) {
        body.innerHTML = `
          <p class="dialog-message"></p>
          <div class="dialog-buttons">
            <button class="btn dialog-cancel">Anuluj</button>
            <button class="btn ${danger ? "btn-danger" : "btn-primary"} dialog-ok" autofocus></button>
          </div>`;
        body.querySelector(".dialog-message").textContent = message;
        body.querySelector(".dialog-ok").textContent = okLabel;
        body.querySelector(".dialog-ok").addEventListener("click", () => { finish(true); closePopup(); });
        body.querySelector(".dialog-cancel").addEventListener("click", () => { finish(false); closePopup(); });
      },
    });
  });
}

/** promptPopup(tekst, wartość) -> Promise<string|null> (null = anulowano) */
function promptPopup(message, defaultValue = "", { title = "Podaj wartość", okLabel = "OK" } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => {
      if (done) return;
      done = true;
      resolve(v);
    };
    openPopup({
      title,
      onClose: () => finish(null),
      render(body) {
        body.innerHTML = `
          <p class="dialog-message"></p>
          <input class="editor-input dialog-input" autofocus>
          <div class="dialog-buttons">
            <button class="btn dialog-cancel">Anuluj</button>
            <button class="btn btn-primary dialog-ok"></button>
          </div>`;
        body.querySelector(".dialog-message").textContent = message;
        body.querySelector(".dialog-ok").textContent = okLabel;
        const input = body.querySelector(".dialog-input");
        input.value = defaultValue ?? "";
        const ok = () => { finish(input.value); closePopup(); };
        body.querySelector(".dialog-ok").addEventListener("click", ok);
        body.querySelector(".dialog-cancel").addEventListener("click", () => { finish(null); closePopup(); });
        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter") { e.preventDefault(); ok(); }
        });
        setTimeout(() => input.select(), 0);
      },
    });
  });
}

// ── G9: jeden sposób informowania o wyniku/błędzie zamiast alert() ──

/** toast(tekst, typ) — typ: "info" | "success" | "error"; znika sam (błąd dłużej) */
function toast(message, type = "info") {
  let host = document.getElementById("toast-host");
  if (!host) {
    host = document.createElement("div");
    host.id = "toast-host";
    host.className = "toast-host";
    host.setAttribute("role", "status");
    host.setAttribute("aria-live", "polite");
    document.body.appendChild(host);
  }
  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  el.textContent = String(message);
  el.addEventListener("click", () => el.remove());
  host.appendChild(el);
  setTimeout(() => el.remove(), type === "error" ? 8000 : 3500);
}
