/**
 * views/login.js — folder + hasło razem, jeden krok (2026-08-28, V4 krok 6).
 *
 * Dawniej: samo hasło tutaj, folder dodawany osobno przyciskiem "+ Dodaj
 * folder" już w galerii — ale root_folders nigdzie się nie zapisywało,
 * więc trzeba było go dodawać ręcznie po każdym restarcie appki. Folder
 * wybrany tu od razu buduje indeks (może chwilę potrwać przy pierwszym
 * razie na dużej bibliotece — pełny popup postępu to krok 7 planu V4,
 * na razie tylko disabled+tekst na przycisku, żeby było widać że coś się
 * dzieje).
 */
registerView("login", function (container) {
  container.innerHTML = `
    <div class="login-screen">
      <h1 class="login-title">ImgNote v3</h1>
      <p class="login-sub">Wybierz folder z notatkami i podaj hasło</p>
      <button id="login-pick-folder" class="btn login-btn">📁 Wybierz folder…</button>
      <p id="login-folder-path" class="login-folder-path"></p>
      <input type="password" id="login-pw" class="login-input" placeholder="Hasło…" autofocus>
      <button id="login-btn" class="btn btn-primary login-btn">Wejdź</button>
      <p id="login-error" class="login-error"></p>
    </div>
  `;

  let selectedFolder = null;

  const pickBtn = document.getElementById("login-pick-folder");
  const folderPathEl = document.getElementById("login-folder-path");
  const pwInput = document.getElementById("login-pw");
  const btn = document.getElementById("login-btn");
  const errorEl = document.getElementById("login-error");

  pickBtn.addEventListener("click", async () => {
    const folder = await callApi("pick_folder");
    if (folder) {
      selectedFolder = folder;
      folderPathEl.textContent = folder;
    }
  });

  async function submit() {
    errorEl.textContent = "";
    if (!selectedFolder) {
      errorEl.textContent = "Najpierw wybierz folder.";
      return;
    }
    const pw = pwInput.value;
    if (!pw) {
      errorEl.textContent = "Hasło nie może być puste.";
      return;
    }

    btn.disabled = true;
    showProgressModal("Logowanie i indeksowanie folderu…");
    try {
      await callApi("login", selectedFolder, pw);
      AppState.password = pw;
      pwInput.value = "";
      showView("gallery");
    } catch (e) {
      errorEl.textContent = "Błąd logowania: " + (e && e.message ? e.message : String(e));
    } finally {
      btn.disabled = false;
      hideProgressModal();
    }
  }

  btn.addEventListener("click", submit);
  pwInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit();
  });
});
