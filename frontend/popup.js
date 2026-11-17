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

let _popupStack = []; // [{ layerEl, onClose }] — [0] najstarsza, ostatnia = wierzchnia
let _popupKeydownHandler = null;

function openPopup({ title, render, onClose, wide, fullscreen }) {
  const root = document.getElementById("popup-root");
  if (!root) return;

  const layer = document.createElement("div");
  layer.className = "popup-layer";
  const boxClass = fullscreen ? " popup-box-fullscreen" : wide ? " popup-box-wide" : "";
  layer.innerHTML = `
    <div class="popup-box${boxClass}">
      <div class="popup-header">
        <span class="popup-title">${escapeHtml(title || "")}</span>
        <button class="btn btn-icon popup-close-btn" title="Zamknij (Esc)">✕</button>
      </div>
      <div class="popup-body"></div>
    </div>
  `;
  root.appendChild(layer);
  root.hidden = false;

  layer.querySelector(".popup-close-btn").addEventListener("click", closePopup);

  // Klik w tło (poza kartą) zamyka TĘ warstwę — nasłuch na samej warstwie
  // (e.target === layer, czyli kliknięto dokładnie w overlay, nie w kartę
  // wewnątrz), więc klik w select/input nie zamyka niczego przez bąbelkowanie.
  layer.addEventListener("mousedown", (e) => {
    if (e.target === layer) closePopup();
  });

  _popupStack.push({ layerEl: layer, onClose: onClose || null });

  // Jeden globalny listener na Escape, niezależny od liczby warstw —
  // zakładany przy pierwszej warstwie, zdejmowany dopiero gdy stos wróci
  // do zera (nie przy każdym open/close pojedynczej warstwy).
  if (!_popupKeydownHandler) {
    _popupKeydownHandler = (e) => {
      if (e.key === "Escape") closePopup();
    };
    document.addEventListener("keydown", _popupKeydownHandler);
  }

  render(layer.querySelector(".popup-body"));
}

function closePopup() {
  const top = _popupStack.pop();
  if (!top) return;

  top.layerEl.remove();

  if (_popupStack.length === 0) {
    const root = document.getElementById("popup-root");
    if (root) root.hidden = true;
    if (_popupKeydownHandler) {
      document.removeEventListener("keydown", _popupKeydownHandler);
      _popupKeydownHandler = null;
    }
  }

  if (top.onClose) top.onClose();
}

/** Zamyka WSZYSTKIE warstwy naraz. Używane rzadko — normalne zamykanie
 * powinno iść przez closePopup() (tylko wierzchnia warstwa). */
function closeAllPopups() {
  while (_popupStack.length > 0) closePopup();
}
