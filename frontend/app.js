/**
 * app.js — punkt wejścia frontendu.
 * Startuje widok "login" gdy pywebview.api jest gotowe.
 */
window.addEventListener("pywebviewready", () => {
  showView("login");
});
