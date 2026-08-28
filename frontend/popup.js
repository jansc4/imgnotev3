/**
 * popup.js — generyczny, reużywalny modal (krok 8 planu V4, punkt 3).
 *
 * Jeden trwały kontener (#popup-root, poza #app) używany przez dowolny
 * widok, który ma się pokazać jako nakładka zamiast pełnoekranowego
 * widoku w routerze (core.js: registerView/showView) — cel: Tag Manager,
 * Ustawienia, później Editor. Kontrakt render(container) jest celowo
 * identyczny jak w registerView, żeby przeniesienie istniejącego widoku
 * do popupu nie wymagało przepisywania środka, tylko zmiany "opakowania".
 *
 * Współistnieje z #progress-modal — z-index popupu (900) jest NIŻSZY niż
 * progress-modal (1000, patrz style.css), więc pasek postępu zawsze
 * wypływa na wierzch nad otwartym popupem (np. Tag Manager w popupie +
 * trwająca w nim bulk-akcja z paskiem postępu naraz).
 *
 * Świadomie bez zależności od escapeHtml() z gallery.js (ten plik ładuje
 * się PRZED widokami w index.html) — własna, minimalna kopia niżej.
 *
 * Użycie:
 *   openPopup({
 *     title: "Zarządzanie tagami",
 *     render: (container) => { ...zbuduj DOM w container... },
 *     onClose: () => { ...opcjonalny cleanup (np. odpięcie listenerów)... },
 *   });
 *   closePopup(); // zamyka aktualnie otwarty popup, jeśli jest
 */

let _popupOnClose = null;
let _popupKeydownHandler = null;

function _popupEscapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function openPopup({ title, render, onClose }) {
  closePopup(); // tylko jeden popup naraz — poprzedni zamyka się bez pytania

  const root = document.getElementById("popup-root");
  if (!root) return;

  _popupOnClose = onClose || null;

  root.innerHTML = `
    <div class="popup-box">
      <div class="popup-header">
        <span class="popup-title">${_popupEscapeHtml(title || "")}</span>
        <button id="popup-close" class="btn btn-icon" title="Zamknij (Esc)">✕</button>
      </div>
      <div id="popup-body" class="popup-body"></div>
    </div>
  `;
  root.hidden = false;

  document.getElementById("popup-close").addEventListener("click", closePopup);

  // Klik w tło (poza kartą) zamyka — nasłuch na samym #popup-root
  // (overlay), nie na całym dokumencie, więc klik wewnątrz karty
  // (np. w select/input) nie zamyka popupu przez bąbelkowanie zdarzenia.
  root.addEventListener("mousedown", _popupOverlayMousedown);

  if (_popupKeydownHandler) document.removeEventListener("keydown", _popupKeydownHandler);
  _popupKeydownHandler = (e) => {
    if (e.key === "Escape") closePopup();
  };
  document.addEventListener("keydown", _popupKeydownHandler);

  render(document.getElementById("popup-body"));
}

function _popupOverlayMousedown(e) {
  if (e.target.id === "popup-root") closePopup();
}

function closePopup() {
  const root = document.getElementById("popup-root");
  if (!root || root.hidden) return;

  root.hidden = true;
  root.removeEventListener("mousedown", _popupOverlayMousedown);
  root.innerHTML = "";

  if (_popupKeydownHandler) {
    document.removeEventListener("keydown", _popupKeydownHandler);
    _popupKeydownHandler = null;
  }

  const cb = _popupOnClose;
  _popupOnClose = null;
  if (cb) cb();
}
