/**
 * core.js — stan aplikacji, router widoków, wrapper wywołań js_api.
 * Bez modułów ES (ładowanie z file:// w pywebview) — wszystko na `window`.
 */

const AppState = {
  password: "",
  rootFolders: [],
  folderTree: [],
  expandedFolders: new Set(), // ścieżki folderów rozwiniętych w drzewie (domyślnie wszystko zwinięte)
  currentFolder: "",
  notes: [],       // aktualnie wyświetlana lista notatek {filepath, title}
  homeSort: "folder", // sortowanie widoku Home: "folder" | "newest" | "random"
  knownTags: [],
  editingNote: null, // filepath edytowanej notatki (null = nowa)
  // Tryb wyszukiwania (runda "drzewo w wyszukiwaniu", 2026-08-28) —
  // drzewo w tym trybie pokazuje tylko gałęzie z trafieniami, nie całą
  // hierarchię (Jan: "wyszukiwanie wymaga innej logiki tree").
  searchActive: false,       // czy aktywne wyszukiwanie (tag/text niepuste)
  searchResults: [],         // pełne, NIEzawężone wyniki ostatniego search_notes()
  searchFolderFilter: null,  // wybrany w drzewie folder podczas wyszukiwania (null = wszystkie wyniki)
};

/** Wrapper na window.pywebview.api.* — rzuca czytelny błąd, jeśli API jeszcze nie gotowe. */
async function callApi(method, ...args) {
  if (!window.pywebview || !window.pywebview.api) {
    throw new Error("Backend jeszcze nie gotowy (pywebviewready nie odpalone).");
  }
  return window.pywebview.api[method](...args);
}

const Views = {};   // nazwa widoku -> funkcja render(container)
let _currentView = null;

function registerView(name, renderFn) {
  Views[name] = renderFn;
}

function showView(name, ...args) {
  const container = document.getElementById("app");
  container.innerHTML = "";
  _currentView = name;
  if (!Views[name]) {
    container.innerHTML = `<p class="not-implemented">Widok "${name}" jeszcze nie zaimplementowany (kolejny etap planu).</p>`;
    return;
  }
  Views[name](container, ...args);
}
