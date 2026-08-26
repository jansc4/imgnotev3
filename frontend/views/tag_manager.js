/**
 * views/tag_manager.js — odpowiednik v2 tag_manager.py.
 * Rename/scal/usuń tag (zapisuje do wszystkich plików), eksport/import JSON
 * (import aktualizuje tylko listę znanych tagów — tak jak w v2).
 */

let _tmSelectedTag = null;
let _tmFilter = "";

registerView("tag_manager", async function (container) {
  container.innerHTML = `
    <div class="tag-manager">
      <div class="toolbar">
        <button id="tm-back" class="btn">← Wróć</button>
        <span class="app-title">Zarządzanie tagami</span>
      </div>
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
          <button id="tm-import" class="btn">📥 Importuj JSON</button>
        </div>
        <p id="tm-status" class="editor-status"></p>
      </div>
    </div>
  `;

  AppState.knownTags = await callApi("get_known_tags");
  await refreshTagList();
  wireTagManagerEvents();
  wireTagManagerJsonActions();
});

async function refreshTagList() {
  // Upewnij się, że AppState.knownTags jest aktualne (mogło się zmienić w tle).
  const listEl = document.getElementById("tm-list");
  const infoEl = document.getElementById("tm-info");
  const tags = (AppState.knownTags || []).filter((t) =>
    t.toLowerCase().includes(_tmFilter.toLowerCase())
  );
  listEl.innerHTML = tags.length
    ? tags.map((t) => `<div class="tm-item${t === _tmSelectedTag ? " tm-item-active" : ""}" data-tag="${escapeAttr(t)}">${escapeHtml(t)}</div>`).join("")
    : `<p class="tree-empty">Brak tagów.</p>`;
  listEl.querySelectorAll(".tm-item").forEach((el) => {
    el.addEventListener("click", () => selectTag(el.dataset.tag));
  });
  infoEl.textContent = `Łącznie tagów: ${AppState.knownTags.length}`;
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

function wireTagManagerEvents() {
  document.getElementById("tm-back").addEventListener("click", () => showView("gallery"));

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
    const res = await callApi("rename_tag", _tmSelectedTag, newName.trim());
    _tmSelectedTag = null;
    statusEl.textContent = res.merged
      ? `✅ Scalono (tag już istniał) — zmieniono w ${res.changed} notatkach.`
      : `✅ Zmieniono nazwę w ${res.changed} notatkach.`;
    AppState.knownTags = await callApi("get_known_tags");
    await refreshTagList();
  });

  document.getElementById("tm-merge").addEventListener("click", async () => {
    if (!_tmSelectedTag) return;
    const others = AppState.knownTags.filter((t) => t !== _tmSelectedTag);
    const target = prompt(`Zastąp „${_tmSelectedTag}” tagiem (jeden z: ${others.join(", ")}):`);
    if (!target || !others.includes(target.trim())) return;
    const statusEl = document.getElementById("tm-status");
    statusEl.textContent = "⏳ Scalam…";
    const res = await callApi("merge_tag", _tmSelectedTag, target.trim());
    _tmSelectedTag = null;
    statusEl.textContent = `✅ Scalono w ${res.changed} notatkach.`;
    AppState.knownTags = await callApi("get_known_tags");
    await refreshTagList();
  });

  document.getElementById("tm-delete").addEventListener("click", async () => {
    if (!_tmSelectedTag) return;
    if (!confirm(`Usunąć „${_tmSelectedTag}” ze wszystkich notatek?\nNie można cofnąć.`)) return;
    const statusEl = document.getElementById("tm-status");
    statusEl.textContent = "⏳ Usuwam…";
    const res = await callApi("delete_tag", _tmSelectedTag);
    _tmSelectedTag = null;
    statusEl.textContent = `✅ Usunięto z ${res.changed} notatek.`;
    AppState.knownTags = await callApi("get_known_tags");
    await refreshTagList();
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

  document.getElementById("tm-import").addEventListener("click", async () => {
    const statusEl = document.getElementById("tm-status");
    const path = await callApi("open_tags_dialog");
    if (!path) return;
    let preview;
    try {
      preview = await callApi("preview_import_tags_json", path);
    } catch (err) {
      statusEl.textContent = "❌ Błąd odczytu: " + err;
      return;
    }
    const msg =
      `Plik zawiera ${preview.imported_total} tagów.\n` +
      `Nowe (do dodania): ${preview.new_tags.length}\n` +
      `Już istniejące: ${preview.existing_count}\n` +
      `Data eksportu: ${preview.exported_at}\n\n` +
      `Dodać nowe tagi?`;
    if (!confirm(msg)) return;
    await callApi("confirm_import_tags", preview.new_tags);
    AppState.knownTags = await callApi("get_known_tags");
    statusEl.textContent = `✅ Dodano ${preview.new_tags.length} nowych tagów.`;
    await refreshTagList();
  });
}
