/**
 * context_menu.js — generyczne menu kontekstowe (prawy klik), krok 8
 * runda folder rename/move + spójny mechanizm operacji (2026-08-28).
 *
 * Wybrany jako GŁÓWNY mechanizm dla operacji na folderach/notatkach
 * (rename/move/usuń) zamiast rozproszonych przycisków w różnych miejscach
 * toolbara — decyzja Jana w tej rundzie.
 *
 * Użycie:
 *   el.addEventListener("contextmenu", (e) => {
 *     openContextMenu(e, [
 *       { label: "✏️ Zmień nazwę", onClick: () => {...} },
 *       { separator: true },
 *       { label: "🗑️ Usuń", danger: true, onClick: () => {...} },
 *     ]);
 *   });
 *
 * Zamyka się na: klik gdziekolwiek indziej, Escape, scroll, wybór pozycji.
 * Tylko jedno menu naraz — otwarcie nowego zamyka poprzednie.
 */

let _ctxMenuEl = null;
let _ctxMenuHandlers = null; // { outsideClick, keydown, scroll } — do odpięcia

function openContextMenu(event, items) {
  event.preventDefault();
  event.stopPropagation();
  closeContextMenu();

  const menu = document.createElement("div");
  menu.className = "context-menu";
  menu.setAttribute("role", "menu");
  menu.innerHTML = items
    .map((item, i) =>
      item.separator
        ? `<div class="context-menu-sep"></div>`
        : `<div class="context-menu-item${item.danger ? " context-menu-item-danger" : ""}" data-i="${i}" role="menuitem" tabindex="-1">${escapeHtml(item.label)}</div>`
    )
    .join("");
  document.body.appendChild(menu);
  _ctxMenuEl = menu;

  // Pozycjonowanie: domyślnie pod kursorem, przesunięte tak, żeby menu
  // nie wystawało poza okno — rozmiar znamy dopiero po dołączeniu do DOM.
  const rect = menu.getBoundingClientRect();
  let x = event.clientX;
  let y = event.clientY;
  if (x + rect.width > window.innerWidth) x = window.innerWidth - rect.width - 4;
  if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 4;
  menu.style.left = `${Math.max(4, x)}px`;
  menu.style.top = `${Math.max(4, y)}px`;

  menu.querySelectorAll(".context-menu-item").forEach((el) => {
    el.addEventListener("click", () => {
      const item = items[Number(el.dataset.i)];
      closeContextMenu();
      if (item.onClick) item.onClick();
    });
  });

  const outsideClick = (e) => {
    if (!menu.contains(e.target)) closeContextMenu();
  };
  // Escape: router w core.js (G6); tu tylko nawigacja strzałkami/Enter (G5)
  const keydown = (e) => {
    const els = Array.from(menu.querySelectorAll(".context-menu-item"));
    const i = els.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = e.key === "ArrowDown" ? (i + 1) % els.length : (i - 1 + els.length) % els.length;
      els[next]?.focus();
    } else if (e.key === "Enter" && i >= 0) {
      e.preventDefault();
      els[i].click();
    }
  };
  const scroll = () => closeContextMenu();

  // Odłożone o jeden "tick" — inaczej TO SAMO zdarzenie prawego kliku,
  // które otwiera menu, mogłoby natychmiast je zamknąć przez własny
  // outsideClick (mousedown bąbelkuje przed contextmenu się nie zdąży odpiąć).
  setTimeout(() => {
    document.addEventListener("mousedown", outsideClick);
    document.addEventListener("keydown", keydown);
    document.addEventListener("scroll", scroll, true);
  }, 0);

  _ctxMenuHandlers = { outsideClick, keydown, scroll };
}

function closeContextMenu() {
  if (_ctxMenuEl) {
    _ctxMenuEl.remove();
    _ctxMenuEl = null;
  }
  if (_ctxMenuHandlers) {
    document.removeEventListener("mousedown", _ctxMenuHandlers.outsideClick);
    document.removeEventListener("keydown", _ctxMenuHandlers.keydown);
    document.removeEventListener("scroll", _ctxMenuHandlers.scroll, true);
    _ctxMenuHandlers = null;
  }
}
