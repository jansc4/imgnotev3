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
    const homeActive = AppState.searchActive
      ? !AppState.searchFolderFilter
      : AppState.currentFolder === HOME_SENTINEL;
    homeEl.classList.toggle("tree-node-active", homeActive);
  }
  renderBreadcrumb();
  if (!el) return;

  if (AppState.searchActive) {
    renderSearchFolderTree(el);
    return;
  }

  if (AppState.folderTree.length === 0) {
    el.innerHTML = `<p class="tree-empty">Brak folderów — root wybierany przy logowaniu.</p>`;
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
  el.querySelectorAll(".tree-row").forEach((row) => {
    row.addEventListener("contextmenu", (e) => {
      const path = row.querySelector(".tree-node")?.dataset.path;
      if (path) openFolderContextMenu(e, path);
    });
  });
}

// Tryb wyszukiwania (runda "drzewo w wyszukiwaniu", 2026-08-28, Jan: "w
// tree jest wszystko zamiast zawartości folderu... wyszukiwanie wymaga
// innej logiki tree") — pełna, nieprzycięta hierarchia myli przy wynikach
// rozrzuconych po wielu folderach naraz. Tu: drzewo pokazuje TYLKO gałęzie
// prowadzące do folderów z co najmniej jednym trafieniem. Klik na węźle
// NIE nawiguje/nie czyści wyszukiwania (w odróżnieniu od zwykłego trybu)
// — zawęża widoczne wyniki do tego folderu + podfolderów, zapytanie
// zostaje aktywne. Menu kontekstowe zostaje dostępne (operacje działają
// na realnym folderze niezależnie od trybu) — refreshFolderTree() po
// operacji przelicza przycięte drzewo od nowa automatycznie.
function renderSearchFolderTree(el) {
  const matchedFolders = new Set(AppState.searchResults.map((n) => n.folder));
  const pruned = AppState.folderTree
    .map((root) => pruneTreeToMatches(root, matchedFolders))
    .filter((n) => n !== null);

  if (pruned.length === 0) {
    el.innerHTML = `<p class="tree-empty">Brak folderów z wynikami.</p>`;
    return;
  }

  el.innerHTML = pruned.map(renderTreeNode).join("");
  el.querySelectorAll(".tree-node").forEach((node) => {
    node.addEventListener("click", (e) => {
      e.stopPropagation();
      AppState.searchFolderFilter = node.dataset.path;
      renderFolderTree();
      applySearchResultsView();
    });
  });
  el.querySelectorAll(".tree-toggle:not(.tree-toggle-empty)").forEach((toggle) => {
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleFolderExpanded(toggle.dataset.path);
    });
  });
  el.querySelectorAll(".tree-row").forEach((row) => {
    row.addEventListener("contextmenu", (e) => {
      const path = row.querySelector(".tree-node")?.dataset.path;
      if (path) openFolderContextMenu(e, path);
    });
  });
}

// Rekurencyjnie odcina gałęzie bez trafień. Węzeł zostaje jeśli SAM ma
// trafienie (matchedFolders — dokładne ścieżki folderów z wynikami) LUB
// ma choć jednego (przyciętego) potomka — przodkowie trafień więc
// zostają automatycznie, bez osobnego liczenia łańcucha przodków.
function pruneTreeToMatches(node, matchedFolders) {
  const prunedChildren = node.children
    .map((c) => pruneTreeToMatches(c, matchedFolders))
    .filter((c) => c !== null);
  if (!matchedFolders.has(node.path) && prunedChildren.length === 0) return null;
  return { ...node, children: prunedChildren };
}

// Menu kontekstowe drzewa (krok 8, runda folder rename/move) — GŁÓWNY
// mechanizm operacji na folderach, zastępuje dawny toolbar-owy przycisk
// "Utwórz podfolder" i hover-ikonkę usuwania (oba były rozproszone,
// każde gdzie indziej — teraz jedno miejsce, prawy klik na węźle).
function openFolderContextMenu(event, path) {
  const isRoot = AppState.rootFolders.includes(path);
  const items = [
    {
      label: "📁 Nowy podfolder",
      onClick: async () => {
        const name = prompt(`Nazwa podfolderu w:\n${path}`);
        if (!name || !name.trim()) return;
        try {
          const newPath = await callApi("create_subfolder", path, name);
          await refreshFolderTree();
          await selectFolder(newPath);
        } catch (e) {
          alert("Nie można utworzyć folderu:\n" + e);
        }
      },
    },
  ];
  if (!isRoot) {
    items.push(
      {
        label: "✏️ Zmień nazwę",
        onClick: async () => {
          const currentName = basename(path);
          const newName = prompt(`Nowa nazwa dla „${currentName}”:`, currentName);
          if (!newName || !newName.trim() || newName.trim() === currentName) return;
          try {
            const newPath = await callApi("rename_folder", path, newName.trim());
            await refreshFolderTree();
            if (AppState.currentFolder === path || AppState.currentFolder.startsWith(path + "/")) {
              await selectFolder(newPath + AppState.currentFolder.slice(path.length));
            } else {
              await refreshCurrentGridView(); // np. podfolder-kafelek w widoku rodzica
            }
          } catch (e) {
            alert("Nie można zmienić nazwy:\n" + e);
          }
        },
      },
      {
        label: "→ Przenieś",
        onClick: () => {
          openFolderPickerPopup({
            title: `Przenieś „${basename(path)}” do…`,
            excludePath: path, // nie można przenieść w siebie/swój podfolder
            onPick: async (destination) => {
              try {
                const newPath = await callApi("move_folder", path, destination);
                await refreshFolderTree();
                if (AppState.currentFolder === path || AppState.currentFolder.startsWith(path + "/")) {
                  await selectFolder(newPath + AppState.currentFolder.slice(path.length));
                } else {
                  await refreshCurrentGridView();
                }
              } catch (e) {
                alert("Nie można przenieść folderu:\n" + e);
              }
            },
          });
        },
      },
      { separator: true },
      { label: "🗑️ Usuń", danger: true, onClick: () => deleteFolderWithConfirm(path) }
    );
  }
  openContextMenu(event, items);
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
    await refreshCurrentGridView(); // np. podfolder-kafelek w widoku rodzica
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
  // W trybie wyszukiwania drzewo jest już przycięte do samych trafień —
  // zawsze w pełni rozwinięte, żeby nie chować tego, co i tak jest istotne
  // (bez tego użytkownik musiałby ręcznie rozklikiwać już przefiltrowane
  // gałęzie).
  const isExpanded = AppState.searchActive || AppState.expandedFolders.has(node.path);
  const childrenHtml = hasChildren && isExpanded
    ? `<div class="tree-children">${node.children.map(renderTreeNode).join("")}</div>`
    : "";
  const toggleHtml = hasChildren
    ? `<span class="tree-toggle" data-path="${escapeAttr(node.path)}">${isExpanded ? "▾" : "▸"}</span>`
    : `<span class="tree-toggle tree-toggle-empty"></span>`;
  const activePath = AppState.searchActive ? AppState.searchFolderFilter : AppState.currentFolder;
  const active = node.path === activePath ? " tree-node-active" : "";
  return `
    <div class="tree-item">
      <div class="tree-row">
        ${toggleHtml}
        <div class="tree-node${active}" data-path="${escapeAttr(node.path)}">${escapeHtml(node.name)}</div>
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

// Odświeża aktualnie wyświetloną siatkę (Home albo konkretny folder) —
// wspólny punkt użyty po move/delete notatki z menu kontekstowego, żeby
// nie duplikować tej samej gałęzi if/else w kilku miejscach.
async function refreshCurrentGridView() {
  if (AppState.currentFolder === HOME_SENTINEL) {
    await showHomeNotes();
  } else if (AppState.currentFolder) {
    await showFolderNotes(AppState.currentFolder);
  }
}

// Menu kontekstowe karty notatki (krok 8, runda folder rename/move) —
// dawniej "Usuń" siedziało wyłącznie w Viewerze, "Przenieś" nie istniało
// wcale. Teraz oba dostępne wprost z siatki, bez otwierania notatki.
function openNoteContextMenu(event, filepath, currentFolder) {
  openContextMenu(event, [
    {
      label: "→ Przenieś",
      onClick: () => {
        openFolderPickerPopup({
          title: "Przenieś notatkę do…",
          excludePath: currentFolder, // "przenieś w to samo miejsce" nic nie daje
          onPick: async (destination) => {
            try {
              await callApi("move_note", filepath, destination);
              await refreshCurrentGridView();
            } catch (e) {
              alert("Nie można przenieść notatki:\n" + e);
            }
          },
        });
      },
    },
    { separator: true },
    {
      label: "🗑️ Usuń",
      danger: true,
      onClick: async () => {
        const note = AppState.notes.find((n) => n.filepath === filepath);
        const title = note ? note.title : "tę notatkę";
        if (!confirm(`Czy na pewno chcesz usunąć:\n${title}?`)) return;
        try {
          await callApi("delete_note", filepath);
          await refreshCurrentGridView();
        } catch (e) {
          alert("Błąd usuwania: " + e);
        }
      },
    },
  ]);
}

// Prosty, klikalny (nie draggable) wybór folderu docelowego dla "Przenieś" —
// używany zarówno przez notatki, jak i foldery (openFolderContextMenu).
// Klik od razu wybiera i zamyka — to jednorazowy wybór, nie formularz.
function openFolderPickerPopup({ title, excludePath, onPick }) {
  function isExcluded(path) {
    return !!excludePath && (path === excludePath || path.startsWith(excludePath + "/"));
  }
  function renderPickerNode(node, depth) {
    if (isExcluded(node.path)) return "";
    const childrenHtml = node.children.map((c) => renderPickerNode(c, depth + 1)).join("");
    return `
      <div class="folder-picker-item" data-path="${escapeAttr(node.path)}" style="padding-left:${depth * 18 + 10}px">
        📁 ${escapeHtml(node.name)}
      </div>
      ${childrenHtml}
    `;
  }

  openPopup({
    title: title || "Wybierz folder",
    render(container) {
      const html = AppState.folderTree.map((root) => renderPickerNode(root, 0)).join("");
      container.innerHTML = `
        <div class="folder-picker-list">
          ${html || `<p class="tree-empty">Brak dostępnych folderów docelowych.</p>`}
        </div>
      `;
      container.querySelectorAll(".folder-picker-item").forEach((el) => {
        el.addEventListener("click", () => {
          closePopup();
          onPick(el.dataset.path);
        });
      });
    },
  });
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
  // Dawny fallback (fp.lastIndexOf("/")) zakładał, że folder to katalog
  // pliku na dysku — prawda w V3 (nazwa pliku = tytuł, w realnym
  // podfolderze), fałsz w V4 (pliki płaskie w root_folder, losowy UUID —
  // fallback zawsze dawał root_folder, niezależnie od realnego folderu
  // notatki). Wszystkie ścieżki backendu (list_notes_in/list_all_notes/
  // collect_viewer_files/search_notes) zwracają dziś "folder" wprost —
  // fallback do AppState.currentFolder tylko na wypadek, gdyby jakaś
  // przyszła ścieżka o tym zapomniała (bezpieczniejszy zgadywacz niż
  // cichy, systematycznie zły).
  return note.folder || AppState.currentFolder;
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
      openViewerPopup(folder, fp);
    });
    card.addEventListener("contextmenu", (e) => {
      openNoteContextMenu(e, card.dataset.filepath, card.dataset.folder || AppState.currentFolder);
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
    AppState.searchActive = false;
    AppState.searchResults = [];
    AppState.searchFolderFilter = null;
    statusEl.textContent = "";
    toggleHomeSortBar(AppState.currentFolder === HOME_SENTINEL);
    renderFolderTree(); // wraca do pełnego, nieprzyciętego drzewa
    if (AppState.currentFolder === HOME_SENTINEL) await showHomeNotes();
    else if (AppState.currentFolder) await showFolderNotes(AppState.currentFolder);
    return;
  }

  toggleHomeSortBar(false);
  statusEl.textContent = "🔍 Szukam…";
  const results = await callApi("search_notes", tagQuery, textQuery);
  AppState.searchActive = true;
  AppState.searchResults = results;
  AppState.searchFolderFilter = null; // nowe zapytanie = zaczynamy bez zawężenia
  renderFolderTree(); // przycina drzewo do folderów z trafieniami
  applySearchResultsView();
}

// Renderuje siatkę dla aktualnego stanu wyszukiwania — wszystkie wyniki
// albo, jeśli wybrano folder w przyciętym drzewie, tylko te z niego i
// jego podfolderów. Osobna funkcja od onSearchChanged, bo klik w drzewie
// (renderSearchFolderTree) też musi to wywołać, bez ponownego pytania
// backendu (wyniki już mamy w AppState.searchResults).
function applySearchResultsView() {
  const statusEl = document.getElementById("search-status");
  const filter = AppState.searchFolderFilter;
  let visible = filter
    ? AppState.searchResults.filter((n) => n.folder === filter || n.folder.startsWith(filter + "/"))
    : AppState.searchResults;

  // Posortowane po folderze (potem tytule) — groupByFolder w renderNotesGrid
  // wstawia nagłówek za każdym razem gdy folder się zmienia; bez sortowania
  // ten sam folder mógłby pojawić się w kilku rozjechanych grupach.
  visible = [...visible].sort((a, b) => {
    const fc = a.folder.localeCompare(b.folder);
    return fc !== 0 ? fc : a.title.localeCompare(b.title);
  });

  const count = visible.length;
  const total = AppState.searchResults.length;
  statusEl.textContent = filter
    ? `${count} / ${total} w tym folderze`
    : count === 0 ? "Brak wyników" :
      count === 1 ? "1 wynik" :
      count <= 4 ? `${count} wyniki` : `${count} wyników`;

  AppState.notes = visible;
  renderNotesGrid(visible, true, true); // groupByFolder=true — widać z jakiego folderu każdy wynik
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
    // Podczas wyszukiwania Home NIE wychodzi z trybu (to robi tylko ✕)
    // — tylko czyści zawężenie do folderu, pokazując wszystkie wyniki.
    if (AppState.searchActive) {
      AppState.searchFolderFilter = null;
      renderFolderTree();
      applySearchResultsView();
      return;
    }
    await selectHome();
  });

  document.getElementById("home-sort-bar").addEventListener("click", async (e) => {
    const btn = e.target.closest(".sort-btn");
    if (!btn) return;
    AppState.homeSort = btn.dataset.sort;
    toggleHomeSortBar(true); // odśwież stan "active" przycisków
    await showHomeNotes();
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
    AppState.searchActive = false;
    AppState.searchResults = [];
    AppState.searchFolderFilter = null;
    renderFolderTree(); // wraca do pełnego, nieprzyciętego drzewa
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
