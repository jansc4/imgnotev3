/**
 * progress_modal.js — jeden, generyczny popup postępu (Filar 4, krok 7).
 *
 * Używany przez KAŻDĄ długą operację backendu (dziś: pierwsze
 * indeksowanie/reperacja przy login(); w przyszłości: rename folderu,
 * operacje masowe na tagach) — jeden modal, jeden event z Pythona
 * (window.onLongOperationProgress), zamiast osobnego mechanizmu na każdą
 * operację (jak dawniej onTagScanProgress tylko dla skanu tagów).
 *
 * Świadomie BEZ osobnego eventu start/koniec z backendu — wywołujący sam
 * wie, kiedy operacja się zaczyna i kończy, bo obejmuje ją własnym await:
 *
 *   showProgressModal("Indeksowanie…");
 *   try {
 *     await callApi("login", folder, pw);
 *   } finally {
 *     hideProgressModal();
 *   }
 *
 * Zaczyna w trybie nieokreślonym (pasek animowany, bez %) — backend nie
 * zna `total` zanim nie policzy ile plików trzeba odszyfrować (np. w
 * trakcie taniego sprawdzenia hasła na samym indeksie). Pierwszy realny
 * push z total>0 przełącza na pasek z procentem.
 */
function showProgressModal(label) {
  const el = document.getElementById("progress-modal");
  const labelEl = document.getElementById("progress-modal-label");
  const fillEl = document.getElementById("progress-modal-bar-fill");
  const pctEl = document.getElementById("progress-modal-pct");
  if (!el) return;
  labelEl.textContent = label || "Proszę czekać…";
  fillEl.style.width = "";
  fillEl.classList.add("progress-modal-bar-indeterminate");
  pctEl.textContent = "";
  el.hidden = false;
}

function hideProgressModal() {
  const el = document.getElementById("progress-modal");
  if (el) el.hidden = true;
}

// Wołane przez backend (Api._push_progress) przez window.evaluate_js.
// Jeśli nikt nie otworzył modala (el.hidden), po prostu ignorujemy —
// operacja mogła się skończyć błyskawicznie, zanim jakikolwiek tick doszedł.
window.onLongOperationProgress = function (current, total) {
  const el = document.getElementById("progress-modal");
  if (!el || el.hidden || !total) return;
  const fillEl = document.getElementById("progress-modal-bar-fill");
  const pctEl = document.getElementById("progress-modal-pct");
  fillEl.classList.remove("progress-modal-bar-indeterminate");
  const pct = Math.min(100, Math.round((current / total) * 100));
  fillEl.style.width = pct + "%";
  pctEl.textContent = `${current} / ${total} (${pct}%)`;
};
