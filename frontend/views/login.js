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
      <input type="password" id="login-pw-confirm" class="login-input" placeholder="Potwierdź hasło…" style="display:none">
      <button id="login-btn" class="btn btn-primary login-btn">Wejdź</button>
      <p id="login-error" class="login-error"></p>
    </div>
  `;

  let selectedFolder = null;
  // Ustawione, gdy login() zwróci password_verified: false (świeży, pusty
  // vault) — od tego momentu przycisk "Wejdź" nie woła już login() ponownie,
  // tylko confirm_new_vault_password() z drugim polem.
  let awaitingConfirm = false;

  const pickBtn = document.getElementById("login-pick-folder");
  const folderPathEl = document.getElementById("login-folder-path");
  const pwInput = document.getElementById("login-pw");
  const pwConfirmInput = document.getElementById("login-pw-confirm");
  const btn = document.getElementById("login-btn");
  const errorEl = document.getElementById("login-error");

  pickBtn.addEventListener("click", async () => {
    const folder = await callApi("pick_folder");
    if (folder) {
      selectedFolder = folder;
      folderPathEl.textContent = folder;
    }
  });

  function resetConfirmStep() {
    awaitingConfirm = false;
    pwConfirmInput.style.display = "none";
    pwConfirmInput.value = "";
    btn.textContent = "Wejdź";
  }

  async function submit() {
    errorEl.textContent = "";

    if (awaitingConfirm) {
      const confirmPw = pwConfirmInput.value;
      if (!confirmPw) {
        errorEl.textContent = "Powtórz hasło, żeby potwierdzić.";
        return;
      }
      btn.disabled = true;
      try {
        await callApi("confirm_new_vault_password", selectedFolder, confirmPw);
        pwInput.value = "";
        pwConfirmInput.value = "";
        showView("gallery");
      } catch (e) {
        // Złe potwierdzenie — na dysku nic jeszcze nie ma (patrz api.py),
        // więc wystarczy pozwolić spróbować jeszcze raz to samo pole.
        errorEl.textContent = "Hasła nie są identyczne. Spróbuj ponownie.";
        pwConfirmInput.value = "";
        pwConfirmInput.focus();
      } finally {
        btn.disabled = false;
      }
      return;
    }

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
      const result = await callApi("login", selectedFolder, pw);
      if (!result.password_verified) {
        // Świeży, pusty vault — backend jeszcze NIC nie zapisał na dysk.
        // Wymuszamy potwierdzenie, żeby literówka w haśle nie została
        // nieodwracalnie "wypieczona" jako hasło do tego vaulta.
        awaitingConfirm = true;
        pwConfirmInput.style.display = "block";
        pwConfirmInput.focus();
        btn.textContent = "Potwierdź";
        errorEl.textContent = "Nowy, pusty folder — powtórz hasło, żeby je potwierdzić.";
        return;
      }
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
  pwConfirmInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit();
  });
  // Zmiana folderu po nieudanym potwierdzeniu (rzadkie, ale możliwe) —
  // wróć do stanu pierwszego pola, żeby nie wysłać confirm dla innego roota.
  pickBtn.addEventListener("click", resetConfirmStep);
});
