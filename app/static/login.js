const form = document.getElementById("login-form");
if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.getElementById("login-error");
    error.hidden = true;
    const response = await fetch("/api/admin/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({password: document.getElementById("password").value}),
    });
    if (response.ok) {
      location.assign("/");
      return;
    }
    error.textContent = response.status === 401 ? "Mật khẩu không đúng." : "Không thể đăng nhập.";
    error.hidden = false;
  });
}
