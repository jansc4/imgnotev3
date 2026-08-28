/**
 * views/gallery.js — odpowiednik v2 main_page.py.
 * Toolbar + pasek wyszukiwania + drzewo folderów (lewo) + siatka notatek (prawo).
 */
const HOME_SENTINEL = "__HOME__";

registerView("gallery", async function (container) {
  container.innerHTML = `
    <div class="gallery">
      <div class="toolbar">
        <span class="app-title">ImgNote v3</span>
        <div class="toolbar-spacer"></div>
        <button id="btn-add-folder" class="btn">+ Dodaj folder</button>
        <button id="btn-add-subfolder" class="btn">📁 Utwórz podfolder</button>
        <button id="btn-manage-tags" class="btn">🏷️ Zarządzaj tagami</button>
        <button id="btn-settings" class="btn" title="Ustawienia">⚙️</button>
        <button id="btn-add-note" class="btn btn-primary">+ Nowa notatka</button>
      </div>
      <div id="breadcrumb-bar" class="breadcrumb-bar"></div>
      <div class="search-bar">
        <label class="search-label">🏷 Tagi:</label>
        <div class="search-tag-wrap">
          <input id="search-tag" class="search-input" placeholder="projekt AND ważne | a OR b | x NOT archiwum" autocomplete="off">
          <div id="search-tag-autocomplete" class="tag-autocomplete search-tag-autocomplete" hidden></div>
        </div>
        <label class="search-label">🔤 Tekst:</label>
        <input id="search-text" class="search-input" placeholder='"fraza dokładna" lub słowo1, słowo2'>
        <button id="btn-clear-search" class="btn btn-icon" title="Wyczyść wyszukiwanie">✕</button>
        <span id="search-status" class="search-status"></span>
      </div>
      <div class="gallery-body">
        <div class="folder-panel">
          <div id="home-node" class="tree-node tree-node-home" data-path="${HOME_SENTINEL}">🏠 Home</div>
          <div id="folder-tree" class="folder-tree"></div>
        </div>
        <div class="notes-panel">
          <div id="home-sort-bar" class="home-sort-bar" hidden>
            <span class="sort-label">Sortuj:</span>
            <button class="sort-btn" data-sort="folder">📁 Wg folderu</button>
            <button class="sort-btn" data-sort="newest">🕒 Najnowsze</button>
            <button class="sort-btn" data-sort="random">🎲 Losowo</button>
          </div>
          <div id="notes-grid" class="notes-grid"></div>
        </div>
      </div>
    </div>
  `;

  await refreshFolderTree();
  wireGalleryEvents();
  await refreshKnownTags();

  // Powrót z edytora/viewera (albo ponowne wejście w gallery) — jeśli
  // mieliśmy już wybrany folder/Home, przeładuj jego zawartość zamiast
  // zostawiać pustą siatkę do czasu ponownego kliknięcia w drzewie.
  if (AppState.currentFolder === HOME_SENTINEL) {
    await selectHome();
  } else if (AppState.currentFolder) {
    expandAncestors(AppState.currentFolder); // pokaż w drzewie, gdzie jesteśmy
    renderFolderTree(); // podświetl aktywny folder + breadcrumb
    await showFolderNotes(AppState.currentFolder);
  }
});

// ── Drzewo folderów ────────────────────────────────────────────────

async function refreshFolderTree() {
  AppState.rootFolders = await callApi("list_root_folders");
  AppState.folderTree = await callApi("get_folder_tree");
  renderFolderTree();
}

function renderFolderTree() {
  const el = document.getElementById("folder-tree");
  const homeEl = document.getElementById("home-node");
  if (homeEl) {
    homeEl.classList.toggle("tree-node-active", AppState.currentFolder === HOME_SENTINEL);
  }
  renderBreadcrumb();
  if (!el) return;
  if (AppState.folderTree.length === 0) {
    el.innerHTML = `<p class="tree-empty">Brak folderów. Kliknij "+ Dodaj folder".</p>`;
    return;
  }
  el.innerHTML = AppState.folderTree.map(renderTreeNode).join("");
  el.querySelectorAll(".tree-node").forEach((node) => {
    node.addEventListener("click", (e) => {
      e.stopPropagation();
      selectFolder(node.dataset.path);
    });
  });
  el.querySelectorAll(".tree-toggle:not(.tree-toggle-empty)").forEach((toggle) => {
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleFolderExpanded(toggle.dataset.path);
    });
  });
  el.querySelectorAll(".tree-delete").forEach((del) => {
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteFolderWithConfirm(del.dataset.path);
    });
  });
}

async function deleteFolderWithConfirm(path) {
  let count;
  try {
    count = await callApi("count_notes_in_folder", path);
  } catch (err) {
    alert("Błąd odczytu zawartości folderu: " + err);
    return;
  }
  const name = basename(path);
  const msg = count > 0
    ? `Usuniesz "${name}" i ${count} notatek wewnątrz (razem z podfolderami). Tej operacji nie da się cofnąć.\n\nNa pewno?`
    : `Folder "${name}" jest pusty. Usunąć go?`;
  if (!confirm(msg)) return;

  showProgressModal("Usuwam folder…");
  try {
    await callApi("delete_folder", path);
  } catch (err) {
    alert("Błąd usuwania folderu: " + err);
    return;
  } finally {
    hideProgressModal();
  }
  if (AppState.currentFolder === path || AppState.currentFolder.startsWith(path + "/")) {
    await selectFolder(HOME_SENTINEL);
  } else {
    await refreshFolderTree();
  }
}

// Foldery są domyślnie zwinięte — rozwinięte trzymamy w AppState.expandedFolders
// (Set ścieżek), żeby stan przetrwał odświeżenia drzewa (np. po dodaniu notatki).
function toggleFolderExpanded(path) {
  if (AppState.expandedFolders.has(path)) {
    AppState.expandedFolders.delete(path);
  } else {
    AppState.expandedFolders.add(path);
  }
  renderFolderTree();
}

// Rozwija w drzewie wszystkich przodków danej ścieżki (i ją samą), żeby po
// wejściu do folderu (np. z breadcrumbów albo przywróceniu widoku) było
// widać, gdzie się aktualnie jesteśmy — nie zwija nic innego.
function expandAncestors(path) {
  if (!path || path === HOME_SENTINEL) return;
  let p = path;
  while (p) {
    AppState.expandedFolders.add(p);
    if (AppState.rootFolders.includes(p)) break;
    const idx = p.lastIndexOf("/");
    if (idx <= 0) break;
    p = p.slice(0, idx);
  }
}

function renderTreeNode(node) {
  const hasChildren = node.children.length > 0;
  const isExpanded = AppState.expandedFolders.has(node.path);
  const childrenHtml = hasChildren && isExpanded
    ? `<div class="tree-children">${node.children.map(renderTreeNode).join("")}</div>`
    : "";
  const toggleHtml = hasChildren
    ? `<span class="tree-toggle" data-path="${escapeAttr(node.path)}">${isExpanded ? "▾" : "▸"}</span>`
    : `<span class="tree-toggle tree-toggle-empty"></span>`;
  const active = node.path === AppState.currentFolder ? " tree-node-active" : "";
  // Root folderów (AppState.rootFolders) nie da się usunąć przez
  // delete_folder — trzeba je odpiąć przez remove_root_folder (inna
  // operacja) — więc ikonka usuwania pojawia się tylko dla podfolderów.
  const isRoot = AppState.rootFolders.includes(node.path);
  const deleteHtml = isRoot
    ? ""
    : `<span class="tree-delete" data-path="${escapeAttr(node.path)}" title="Usuń folder">🗑</span>`;
  return `
    <div class="tree-item">
      <div class="tree-row">
        ${toggleHtml}
        <div class="tree-node${active}" data-path="${escapeAttr(node.path)}">${escapeHtml(node.name)}</div>
        ${deleteHtml}
      </div>
      ${childrenHtml}
    </div>
  `;
}

// ── Breadcrumb (ścieżka w headerze) ─────────────────────────────────

function basename(p) {
  const i = p.lastIndexOf("/");
  return i === -1 ? p : p.slice(i + 1);
}

// Zamienia ścieżkę bezwzględną na segmenty od folderu głównego (root,
// widoczny jako korzeń w drzewie) w dół, żeby breadcrumb odpowiadał temu,
// co użytkownik widzi w panelu z lewej.
function breadcrumbSegments(path) {
  const root = AppState.rootFolders.find((r) => path === r || path.startsWith(r + "/"));
  if (!root) return [{ name: path, path }];
  const rest = path.slice(root.length).split("/").filter(Boolean);
  const segments = [{ name: basename(root), path: root }];
  let acc = root;
  for (const part of rest) {
    acc += "/" + part;
    segments.push({ name: part, path: acc });
  }
  return segments;
}

function renderBreadcrumb() {
  const el = document.getElementById("breadcrumb-bar");
  if (!el) return;

  if (!AppState.currentFolder || AppState.currentFolder === HOME_SENTINEL) {
    el.innerHTML = `<span class="breadcrumb-current">🏠 Home</span>`;
    return;
  }

  const segments = breadcrumbSegments(AppState.currentFolder);
  const linksHtml = segments
    .map((seg, i) => {
      if (i === segments.length - 1) {
        return `<span class="breadcrumb-current">${escapeHtml(seg.name)}</span>`;
      }
      return `<span class="breadcrumb-link" data-path="${escapeAttr(seg.path)}">${escapeHtml(seg.name)}</span>`;
    })
    .join(`<span class="breadcrumb-sep">›</span>`);

  el.innerHTML =
    `<span class="breadcrumb-link" data-path="${HOME_SENTINEL}">🏠</span><span class="breadcrumb-sep">›</span>` +
    linksHtml;

  el.querySelectorAll(".breadcrumb-link").forEach((link) => {
    link.addEventListener("click", async () => {
      const p = link.dataset.path;
      if (p === HOME_SENTINEL) await selectHome();
      else await selectFolder(p);
    });
  });
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, "&quot;");
}

// ── Siatka notatek ─────────────────────────────────────────────────

async function selectFolder(folder) {
  AppState.currentFolder = folder;
  clearSearchInputs();
  toggleHomeSortBar(false);
  expandAncestors(folder); // pokaż w drzewie, gdzie jesteśmy
  renderFolderTree(); // podświetl aktywny folder + breadcrumb
  await showFolderNotes(folder);
}

// Szuka węzła drzewa (z jego podfolderami) po ścieżce — żeby móc pokazać
// podfoldery jako kafelki w siatce, nawet gdy same nie mają notatek.
function findTreeNode(path, nodes) {
  nodes = nodes || AppState.folderTree;
  for (const n of nodes) {
    if (n.path === path) return n;
    const found = findTreeNode(path, n.children);
    if (found) return found;
  }
  return null;
}

async function showFolderNotes(folder) {
  const notes = await callApi("list_notes", folder);
  AppState.notes = notes;
  const node = findTreeNode(folder);
  const subfolders = node ? node.children : [];
  renderNotesGrid(notes, false, false, subfolders);
}

async function selectHome() {
  AppState.currentFolder = HOME_SENTINEL;
  clearSearchInputs();
  toggleHomeSortBar(true);
  renderFolderTree(); // podświetl "Home" i odśwież drzewo (bez aktywnego folderu)
  await showHomeNotes();
}

async function showHomeNotes() {
  const notes = await callApi("list_all_notes", AppState.homeSort);
  AppState.notes = notes;
  renderNotesGrid(notes, false, AppState.homeSort === "folder");
}

function toggleHomeSortBar(visible) {
  const bar = document.getElementById("home-sort-bar");
  if (!bar) return;
  bar.hidden = !visible;
  if (visible) {
    bar.querySelectorAll(".sort-btn").forEach((btn) => {
      btn.classList.toggle("sort-btn-active", btn.dataset.sort === AppState.homeSort);
    });
  }
}

function folderOf(note) {
  if (note.folder) return note.folder;
  const fp = note.filepath || "";
  const i = fp.lastIndexOf("/");
  return i === -1 ? "" : fp.slice(0, i);
}

function renderNotesGrid(notes, searchMode, groupByFolder, subfolders) {
  const el = document.getElementById("notes-grid");
  if (!el) return;
  subfolders = subfolders || [];

  if (notes.length === 0 && subfolders.length === 0) {
    const msg = searchMode ? "Brak wyników." : "Brak notatek w tym folderze.\nDodaj pierwszą!";
    el.innerHTML = `<p class="grid-empty">${escapeHtml(msg)}</p>`;
    return;
  }

  let html = "";

  // Podfoldery jako kafelki na górze siatki — dzięki temu folder bez
  // bezpośrednich notatek, ale z zawartością w podfolderach, nie wygląda
  // na "pusty" (szczególnie że drzewo po lewej jest teraz domyślnie zwinięte).
  if (subfolders.length > 0) {
    html += subfolders
      .map(
        (sf) => `
      <div class="thumb-card folder-tile" data-folder-path="${escapeAttr(sf.path)}" title="${escapeAttr(sf.name)}">
        <div class="thumb-img">📁</div>
        <div class="thumb-title">${escapeHtml(sf.name)}</div>
      </div>
    `
      )
      .join("");
  }

  let lastFolder = null;
  notes.forEach((n, i) => {
    if (groupByFolder && n.folder !== lastFolder) {
      lastFolder = n.folder;
      html += `<div class="folder-group-header">📁 ${escapeHtml(n.folder)}</div>`;
    }
    html += `
      <div class="thumb-card" data-filepath="${escapeAttr(n.filepath)}" data-folder="${escapeAttr(folderOf(n))}" data-idx="${i}">
        <div class="thumb-img" id="thumb-${i}">🖼️</div>
        <div class="thumb-title">${escapeHtml(n.title)}</div>
      </div>
    `;
  });
  el.innerHTML = html;

  el.querySelectorAll(".folder-tile").forEach((tile) => {
    tile.addEventListener("click", () => {
      selectFolder(tile.dataset.folderPath);
    });
  });

  el.querySelectorAll(".thumb-card:not(.folder-tile)").forEach((card) => {
    card.addEventListener("click", () => {
      const fp = card.dataset.filepath;
      const folder = card.dataset.folder || AppState.currentFolder;
      showView("viewer", folder, fp);
    });
  });

  // Lazy-loading miniatur: tylko dla kart widocznych (lub prawie widocznych)
  // w obszarze siatki, przez IntersectionObserver — zamiast ładować
  // wszystkie naraz sekwencyjnie (istotne przy Home z dużą liczbą notatek).
  // Kafelki podfolderów pomijamy — nie mają miniatury do wczytania.
  setupLazyThumbnails(notes);
}

let _thumbObserver = null;

function setupLazyThumbnails(notes) {
  if (_thumbObserver) {
    _thumbObserver.disconnect();
    _thumbObserver = null;
  }

  const scrollRoot = document.getElementById("notes-grid");
  _thumbObserver = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const card = entry.target;
        _thumbObserver.unobserve(card);
        const idx = Number(card.dataset.idx);
        loadOneThumbnail(notes[idx], idx);
      });
    },
    { root: scrollRoot, rootMargin: "400px 0px", threshold: 0.01 }
  );

  document.querySelectorAll(".thumb-card:not(.folder-tile)").forEach((card) => {
    _thumbObserver.observe(card);
  });
}

async function loadOneThumbnail(note, idx) {
  const imgEl = document.getElementById(`thumb-${idx}`);
  if (!imgEl || !note) return;
  try {
    const b64 = await callApi("get_note_thumbnail", note.filepath);
    if (b64 && document.getElementById(`thumb-${idx}`)) {
      imgEl.innerHTML = `<img src="data:image/png;base64,${b64}" alt="">`;
    }
  } catch (e) {
    // pojedynczy błąd nie przerywa ładowania reszty
  }
}

// ── Wyszukiwanie ───────────────────────────────────────────────────

let _searchDebounce = null;

function clearSearchInputs() {
  const tagEl = document.getElementById("search-tag");
  const textEl = document.getElementById("search-text");
  const statusEl = document.getElementById("search-status");
  if (tagEl) tagEl.value = "";
  if (textEl) textEl.value = "";
  if (statusEl) statusEl.textContent = "";
}

async function onSearchChanged() {
  const tagQuery = document.getElementById("search-tag").value.trim();
  const textQuery = document.getElementById("search-text").value.trim();
  const statusEl = document.getElementById("search-status");

  if (!tagQuery && !textQuery) {
    statusEl.textContent = "";
    toggleHomeSortBar(AppState.currentFolder === HOME_SENTINEL);
    if (AppState.currentFolder === HOME_SENTINEL) await showHomeNotes();
    else if (AppState.currentFolder) await showFolderNotes(AppState.currentFolder);
    return;
  }

  toggleHomeSortBar(false);
  statusEl.textContent = "🔍 Szukam…";
  const results = await callApi("search_notes", tagQuery, textQuery);
  const count = results.length;
  statusEl.textContent =
    count === 0 ? "Brak wyników" :
    count === 1 ? "1 wynik" :
    count <= 4 ? `${count} wyniki` : `${count} wyników`;
  renderNotesGrid(results, true);
}

function debouncedSearch() {
  clearTimeout(_searchDebounce);
  _searchDebounce = setTimeout(onSearchChanged, 300);
}

// ── Podpowiedzi tagów w #search-tag (krok 8, punkt 15) ────────────────
//
// W przeciwieństwie do edytora (jedno płaskie pole = jeden tag) to pole
// niesie całą gramatykę zapytania (AND/OR/NOT, nawiasy) — podpowiedź musi
// patrzeć na TOKEN pod kursorem, nie na całą wartość pola. Granice tokenu
// (spacja, "(", ")") muszą się zgadzać z _tokenize_tags() w
// backendV4/search_engine.py, inaczej podpowiedź podmieniałaby coś innego
// niż to, co backend faktycznie sparsuje jako jedno słowo.

function _searchTagTokenBounds(input) {
  const value = input.value;
  const caret = input.selectionStart ?? value.length;
  const before = value.slice(0, caret);
  const tokenText = (before.match(/[^\s()]*$/) || [""])[0];
  return { start: caret - tokenText.length, end: caret, text: tokenText };
}

function updateSearchTagAutocomplete() {
  const input = document.getElementById("search-tag");
  const dropdown = document.getElementById("search-tag-autocomplete");
  const { text } = _searchTagTokenBounds(input);
  const q = text.trim().toLowerCase();
  if (!q) {
    hideSearchTagAutocomplete();
    return;
  }
  const matches = (AppState.knownTags || [])
    .filter((t) => t.toLowerCase().includes(q))
    .slice(0, 10);
  if (matches.length === 0) {
    hideSearchTagAutocomplete();
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
      applySearchTagSuggestion(input, item.dataset.tag);
      hideSearchTagAutocomplete();
      debouncedSearch();
    });
  });
}

function applySearchTagSuggestion(input, tag) {
  const { start, end } = _searchTagTokenBounds(input);
  const before = input.value.slice(0, start);
  const after = input.value.slice(end);
  const insertion = tag + " ";
  input.value = before + insertion + after;
  input.focus();
  const newCaret = before.length + insertion.length;
  input.setSelectionRange(newCaret, newCaret);
}

function hideSearchTagAutocomplete() {
  const dropdown = document.getElementById("search-tag-autocomplete");
  if (dropdown) dropdown.hidden = true;
}

// ── Toolbar: foldery, tagi, nowa notatka ─────────────────────────────

function wireGalleryEvents() {
  document.getElementById("home-node").addEventListener("click", async () => {
    await selectHome();
  });

  document.getElementById("home-sort-bar").addEventListener("click", async (e) => {
    const btn = e.target.closest(".sort-btn");
    if (!btn) return;
    AppState.homeSort = btn.dataset.sort;
    toggleHomeSortBar(true); // odśwież stan "active" przycisków
    await showHomeNotes();
  });

  document.getElementById("btn-add-folder").addEventListener("click", async () => {
    showProgressModal("Indeksowanie folderu…");
    let folder;
    try {
      folder = await callApi("add_root_folder");
    } catch (e) {
      alert("Nie udało się dodać folderu:\n" + (e && e.message ? e.message : e));
      return;
    } finally {
      hideProgressModal();
    }
    if (folder) {
      await refreshFolderTree();
      await selectFolder(folder);
    }
  });

  document.getElementById("btn-add-subfolder").addEventListener("click", async () => {
    if (!AppState.currentFolder || AppState.currentFolder === HOME_SENTINEL) {
      alert("Najpierw wybierz folder z drzewa po lewej.");
      return;
    }
    const name = prompt(`Nazwa podfolderu w:\n${AppState.currentFolder}`);
    if (!name || !name.trim()) return;
    try {
      const newPath = await callApi("create_subfolder", AppState.currentFolder, name);
      await refreshFolderTree();
      await selectFolder(newPath);
    } catch (e) {
      alert("Nie można utworzyć folderu:\n" + e);
    }
  });

  document.getElementById("btn-manage-tags").addEventListener("click", () => {
    openTagManagerPopup();
  });

  document.getElementById("btn-settings").addEventListener("click", () => {
    openSettingsPopup();
  });

  document.getElementById("btn-add-note").addEventListener("click", () => {
    if (AppState.currentFolder === HOME_SENTINEL) {
      alert("Wybierz konkretny folder z drzewa po lewej, żeby dodać notatkę.");
      return;
    }
    openEditorPopup(AppState.currentFolder, null);
  });

  document.getElementById("search-tag").addEventListener("input", () => {
    debouncedSearch();
    updateSearchTagAutocomplete();
  });
  document.getElementById("search-tag").addEventListener("blur", () => {
    setTimeout(hideSearchTagAutocomplete, 150);
  });
  document.getElementById("search-text").addEventListener("input", debouncedSearch);
  document.getElementById("btn-clear-search").addEventListener("click", async () => {
    clearSearchInputs();
    hideSearchTagAutocomplete();
    toggleHomeSortBar(AppState.currentFolder === HOME_SENTINEL);
    if (AppState.currentFolder === HOME_SENTINEL) await showHomeNotes();
    else if (AppState.currentFolder) await showFolderNotes(AppState.currentFolder);
  });
}

// ── Tagi (podpięcie pod callbacki z backendu, Etap 3) ───────────────

async function refreshKnownTags() {
  if (AppState.rootFolders.length === 0) return;
  await callApi("refresh_tags");
}

// Wywoływane przez backend przez window.evaluate_js (patrz backendV4/api.py).
window.onTagsUpdated = function (tags) {
  AppState.knownTags = tags;
};
