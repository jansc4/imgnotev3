/**
 * views/tag_manager.js — odpowiednik v2 tag_manager.py.
 * Rename/scal/usuń tag (zapisuje do wszystkich plików), eksport JSON.
 *
 * Krok 8 (punkt 12): przerobione na popup (fundament: popup.js) zamiast
 * osobnego widoku w routerze — otwierany z gallery.js, zamykany krzyżykiem
 * / Escape / klikiem w tło, bez własnego "← Wróć".
 */

let _tmSelectedTag = null;
let _tmFilter = "";

function openTagManagerPopup() {
  // Reset przy KAŻDYM otwarciu — inaczej pole filtra renderuje się puste,
  // a lista i tak filtruje starą wartością z poprzedniej sesji (bug
  // znaleziony przy przeglądzie, krok 8 punkt 14).
  _tmSelectedTag = null;
  _tmFilter = "";

  openPopup({
    title: "Zarządzanie tagami",
    async render(container) {
      container.innerHTML = `
        <div class="tm-body">
          <input id="tm-filter" class="editor-input" placeholder="Filtruj tagi…">
          <div id="tm-list" class="tm-list"></div>
          <p id="tm-info" class="tm-info"></p>
          <div class="tm-actions">
            <button id="tm-rename" class="btn" disabled>✏️ Zmień nazwę</button>
            <button id="tm-merge" class="btn" disabled>⇒ Scal z…</button>
            <button id="tm-delete" class="btn" disabled>🗑️ Usuń tag</button>
          </div>
          <div class="tm-json-actions">
            <button id="tm-export" class="btn">📤 Eksportuj JSON</button>
          </div>
          <p id="tm-status" class="editor-status"></p>
        </div>
      `;

      AppState.knownTags = await callApi("get_known_tags");
      refreshTagList();
      wireTagManagerEvents();
      wireTagManagerJsonActions();
    },
  });
}

function refreshTagList() {
  const listEl = document.getElementById("tm-list");
  const infoEl = document.getElementById("tm-info");
  if (!listEl) return; // popup mógł się zamknąć w międzyczasie (async gap)
  const tags = (AppState.knownTags || []).filter((t) =>
    t.toLowerCase().includes(_tmFilter.toLowerCase())
  );
  listEl.innerHTML = tags.length
    ? tags.map((t) => `<div class="tm-item${t === _tmSelectedTag ? " tm-item-active" : ""}" data-tag="${escapeAttr(t)}">${escapeHtml(t)}</div>`).join("")
    : `<p class="tree-empty">Brak tagów.</p>`;
  listEl.querySelectorAll(".tm-item").forEach((el) => {
    el.addEventListener("click", () => selectTag(el.dataset.tag));
  });
  if (!_tmSelectedTag) infoEl.textContent = `Łącznie tagów: ${AppState.knownTags.length}`;
}

async function selectTag(tag) {
  _tmSelectedTag = tag;
  const usage = await callApi("count_tag_usage", tag);
  document.getElementById("tm-info").textContent = `Tag „${tag}” użyty w ${usage} notatce/notatkach.`;
  document.getElementById("tm-rename").disabled = false;
  document.getElementById("tm-merge").disabled = AppState.knownTags.length <= 1;
  document.getElementById("tm-delete").disabled = false;
  refreshTagList();
}

// Bug znaleziony przy przeglądzie (krok 8, punkt 14): po udanej akcji
// przyciski zostawały enabled mimo braku zaznaczenia — klik nic nie robił
// (cichy return w handlerze), wyglądało jak zawieszenie. Jeden punkt
// resetu zamiast ręcznego "_tmSelectedTag = null" w trzech miejscach.
function deselectTag() {
  _tmSelectedTag = null;
  document.getElementById("tm-rename").disabled = true;
  document.getElementById("tm-merge").disabled = true;
  document.getElementById("tm-delete").disabled = true;
}

function wireTagManagerEvents() {
  document.getElementById("tm-filter").addEventListener("input", (e) => {
    _tmFilter = e.target.value;
    refreshTagList();
  });

  document.getElementById("tm-rename").addEventListener("click", async () => {
    if (!_tmSelectedTag) return;
    const newName = prompt(`Nowa nazwa dla „${_tmSelectedTag}”:`, _tmSelectedTag);
    if (!newName || !newName.trim() || newName.trim() === _tmSelectedTag) return;
    const statusEl = document.getElementById("tm-status");
    statusEl.textContent = "⏳ Zmieniam nazwę…";
    showProgressModal("Zmieniam nazwę taga…");
    let res;
    try {
      res = await callApi("rename_tag", _tmSelectedTag, newName.trim());
    } finally {
      hideProgressModal();
    }
    deselectTag();
    statusEl.textContent = res.merged
      ? `✅ Scalono (tag już istniał) — zmieniono w ${res.changed} notatkach.`
      : `✅ Zmieniono nazwę w ${res.changed} notatkach.`;
    AppState.knownTags = await callApi("get_known_tags");
    refreshTagList();
  });

  document.getElementById("tm-merge").addEventListener("click", async () => {
    if (!_tmSelectedTag) return;
    const others = AppState.knownTags.filter((t) => t !== _tmSelectedTag);
    const target = prompt(`Zastąp „${_tmSelectedTag}” tagiem (jeden z: ${others.join(", ")}):`);
    if (!target || !others.includes(target.trim())) return;
    const statusEl = document.getElementById("tm-status");
    statusEl.textContent = "⏳ Scalam…";
    showProgressModal("Scalam tagi…");
    let res;
    try {
      res = await callApi("merge_tag", _tmSelectedTag, target.trim());
    } finally {
      hideProgressModal();
    }
    deselectTag();
    statusEl.textContent = `✅ Scalono w ${res.changed} notatkach.`;
    AppState.knownTags = await callApi("get_known_tags");
    refreshTagList();
  });

  document.getElementById("tm-delete").addEventListener("click", async () => {
    if (!_tmSelectedTag) return;
    if (!confirm(`Usunąć „${_tmSelectedTag}” ze wszystkich notatek?\nNie można cofnąć.`)) return;
    const statusEl = document.getElementById("tm-status");
    statusEl.textContent = "⏳ Usuwam…";
    showProgressModal("Usuwam tag…");
    let res;
    try {
      res = await callApi("delete_tag", _tmSelectedTag);
    } finally {
      hideProgressModal();
    }
    deselectTag();
    statusEl.textContent = `✅ Usunięto z ${res.changed} notatek.`;
    AppState.knownTags = await callApi("get_known_tags");
    refreshTagList();
  });
}

function wireTagManagerJsonActions() {
  document.getElementById("tm-export").addEventListener("click", async () => {
    const statusEl = document.getElementById("tm-status");
    const path = await callApi("save_tags_dialog");
    if (!path) return;
    statusEl.textContent = "⏳ Eksportuję…";
    try {
      const res = await callApi("export_tags_json", path);
      statusEl.textContent = `✅ Zapisano ${res.tags_count} tagów do:\n${path}`;
    } catch (err) {
      statusEl.textContent = "❌ Błąd zapisu: " + err;
    }
  });
}
