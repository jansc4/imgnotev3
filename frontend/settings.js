/**
 * settings.js — Ustawienia (krok 8, punkt 11).
 *
 * Trwałość: localStorage w QtWebEngine (decyzja Jana — profil pywebview
 * jest trwały, przetrwa restart appki; brak potrzeby osobnego pliku
 * configu po stronie backendu na razie).
 *
 * ŚWIADOMY ZAKRES: dziś w pełni działają tylko rozmiar miniatur i rozmiar
 * czcionki treści (edytor + podgląd Markdown) — obie sterowane pojedynczą
 * zmienną CSS, więc "działają" naprawdę, nie połowicznie.
 *
 * Dark/light theme i język pl/en świadomie NIE są tu zaimplementowane —
 * to osobne, dużo większe zadania (theme: ~70 zaszytych na sztywno
 * kolorów hex w style.css poza systemem zmiennych --bg/--text/itd.;
 * język: wszystkie stringi UI zaszyte po polsku w każdym pliku widoku).
 * Wrzucenie tu przełącznika, który realnie zmienia 2-3 elementy, byłoby
 * gorsze niż jego brak — patrz V4_REDESIGN_PLAN.md, krok 8 punkt 11.
 */

const SETTINGS_STORAGE_KEY = "imgnote_settings";
// autoLockMinutes: S7, 0 = autoblokada wyłączona (decyzja Jana: domyślnie 10 min)
const SETTINGS_DEFAULTS = { thumbSize: 140, fontSize: 13, autoLockMinutes: 10 };
const AUTO_LOCK_CHOICES = [0, 5, 10, 15, 30, 60];
// Fizyczny sufit z backendV4/thumbnail.py (miniatury generowane maks.
// 320px po dłuższym boku) — suwak wyżej zacząłby tylko rozciągać/rozmywać.
const THUMB_SIZE_MIN = 100;
const THUMB_SIZE_MAX = 320;
const FONT_SIZE_MIN = 11;
const FONT_SIZE_MAX = 18;

function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (!raw) return { ...SETTINGS_DEFAULTS };
    const parsed = JSON.parse(raw);
    return {
      thumbSize: clampInt(parsed.thumbSize, THUMB_SIZE_MIN, THUMB_SIZE_MAX, SETTINGS_DEFAULTS.thumbSize),
      fontSize: clampInt(parsed.fontSize, FONT_SIZE_MIN, FONT_SIZE_MAX, SETTINGS_DEFAULTS.fontSize),
      autoLockMinutes: AUTO_LOCK_CHOICES.includes(parsed.autoLockMinutes)
        ? parsed.autoLockMinutes : SETTINGS_DEFAULTS.autoLockMinutes,
    };
  } catch (_) {
    // localStorage niedostępny albo uszkodzony JSON — appka ma dalej działać.
    return { ...SETTINGS_DEFAULTS };
  }
}

function clampInt(value, min, max, fallback) {
  const n = parseInt(value, 10);
  if (Number.isNaN(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

function saveSettings(settings) {
  try {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  } catch (_) {
    // Brak miejsca / zablokowany localStorage — ustawienie i tak zostaje
    // zaaplikowane na czas tej sesji przez applySettings(), tylko nie
    // przetrwa restartu. Nie ma tu nic sensownego do pokazania userowi.
  }
}

function applySettings(settings) {
  const root = document.documentElement.style;
  root.setProperty("--thumb-size", `${settings.thumbSize}px`);
  root.setProperty("--content-font-size", `${settings.fontSize}px`);
}

function openSettingsPopup() {
  const current = loadSettings();

  openPopup({
    title: "Ustawienia",
    render(container) {
      container.innerHTML = `
        <div class="settings-row">
          <label class="settings-label" for="set-thumb-size">
            Rozmiar miniatur: <span id="set-thumb-size-val">${current.thumbSize}px</span>
          </label>
          <input type="range" id="set-thumb-size" min="${THUMB_SIZE_MIN}" max="${THUMB_SIZE_MAX}" step="10" value="${current.thumbSize}">
        </div>
        <div class="settings-row">
          <label class="settings-label" for="set-font-size">
            Rozmiar czcionki treści: <span id="set-font-size-val">${current.fontSize}px</span>
          </label>
          <input type="range" id="set-font-size" min="${FONT_SIZE_MIN}" max="${FONT_SIZE_MAX}" step="1" value="${current.fontSize}">
        </div>
        <div class="settings-row">
          <label class="settings-label" for="set-auto-lock">Automatyczna blokada po bezczynności:</label>
          <select id="set-auto-lock" class="settings-select">
            ${AUTO_LOCK_CHOICES.map((m) => `<option value="${m}"${m === current.autoLockMinutes ? " selected" : ""}>${m === 0 ? "wyłączona" : m + " min"}</option>`).join("")}
          </select>
        </div>
        <p class="settings-note">
          Motyw (jasny/ciemny) i język (PL/EN) to osobna, większa runda prac —
          nie ma ich tu jeszcze celowo, żeby nie udawać czegoś, co nie działa.
        </p>
      `;

      const thumbInput = document.getElementById("set-thumb-size");
      const thumbVal = document.getElementById("set-thumb-size-val");
      const fontInput = document.getElementById("set-font-size");
      const fontVal = document.getElementById("set-font-size-val");

      // Zmiany aplikują się na żywo (podgląd natychmiastowy) i od razu
      // zapisują — bez osobnego przycisku "Zapisz": to dwa proste suwaki,
      // nie formularz z walidacją wymagającą potwierdzenia.
      thumbInput.addEventListener("input", () => {
        const settings = loadSettings();
        settings.thumbSize = parseInt(thumbInput.value, 10);
        thumbVal.textContent = `${settings.thumbSize}px`;
        applySettings(settings);
        saveSettings(settings);
      });

      fontInput.addEventListener("input", () => {
        const settings = loadSettings();
        settings.fontSize = parseInt(fontInput.value, 10);
        fontVal.textContent = `${settings.fontSize}px`;
        applySettings(settings);
        saveSettings(settings);
      });

      document.getElementById("set-auto-lock").addEventListener("change", (e) => {
        const settings = loadSettings();
        settings.autoLockMinutes = parseInt(e.target.value, 10);
        saveSettings(settings); // timer w core.js czyta ustawienie przy każdym sprawdzeniu
      });
    },
  });
}
