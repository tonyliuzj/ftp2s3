import { apiFetch, showFlash } from "/panel/js/common.js";

async function routeEntry() {
  try {
    const setup = await apiFetch("/admin/setup/status");
    if (setup.needs_setup) {
      window.location.href = "/panel/pages/setup.html";
      return;
    }
  } catch (_error) {
    return;
  }

  try {
    await apiFetch("/admin/me");
    window.location.href = "/panel/pages/dashboard.html";
  } catch (_error) {
    return;
  }
}

async function handleSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);

  try {
    await apiFetch("/admin/login", {
      method: "POST",
      body: JSON.stringify({
        username: String(formData.get("username") || ""),
        password: String(formData.get("password") || ""),
      }),
    });
    window.location.href = "/panel/pages/dashboard.html";
  } catch (error) {
    showFlash(error.message, "error");
  }
}

await routeEntry();
document.getElementById("login-form")?.addEventListener("submit", handleSubmit);
