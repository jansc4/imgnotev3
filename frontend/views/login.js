/**
 * views/login.js — odpowiednik v2 login_page.py.
 * Jedno hasło do całej aplikacji (używane do szyfrowania/deszyfrowania).
 */
registerView("login", function (container) {
  container.innerHTML = `
    <div class="login-screen">
      <h1 class="login-title">ImgNote v3</h1>
      <p class="login-sub">Podaj hasło sesji (używane do szyfrowania wszystkich plików)</p>
      <input type="password" id="login-pw" class="login-input" placeholder="Hasło…" autofocus>
      <button id="login-btn" class="btn btn-primary login-btn">Wejdź</button>
      <p id="login-error" class="login-error"></p>
    </div>
  `;

  const pwInput = document.getElementById("login-pw");
  const btn = document.getElementById("login-btn");
  const errorEl = document.getElementById("login-error");

  async function submit() {
    const pw = pwInput.value;
    if (!pw) {
      errorEl.textContent = "Hasło nie może być puste.";
      return;
    }
    errorEl.textContent = "";
    const ok = await callApi("login", pw);
    if (!ok) {
      errorEl.textContent = "Błąd logowania.";
      return;
    }
    AppState.password = pw;
    pwInput.value = "";
    showView("gallery");
  }

  btn.addEventListener("click", submit);
  pwInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit();
  });
});
