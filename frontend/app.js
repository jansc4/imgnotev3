/**
 * app.js — punkt wejścia frontendu.
 * Startuje widok "login" gdy pywebview.api jest gotowe.
 */
window.addEventListener("pywebviewready", () => {
  // Ustawienia (krok 8, punkt 11) — zaaplikowane PRZED pierwszym widokiem,
  // żeby np. rozmiar miniatur był poprawny od razu w galerii, bez migotania.
  applySettings(loadSettings());
  showView("login");
});
