import { apiFetch, showFlash } from "/panel/js/common.js";

let lastGeneratedObjectDatabaseUrl = "";

function buildObjectDatabaseUrl() {
  const postgresHost = String(document.getElementById("postgres-host")?.value || "localhost").trim() || "localhost";
  const postgresDb = String(document.getElementById("postgres-db")?.value || "").trim();
  const postgresUser = String(document.getElementById("postgres-user")?.value || "").trim();
  const postgresPassword = String(document.getElementById("postgres-password")?.value || "").trim();
  if (!postgresDb || !postgresUser || !postgresPassword) {
    return "";
  }
  return `postgresql+psycopg://${postgresUser}:${postgresPassword}@${postgresHost}:5432/${postgresDb}`;
}

function maybeSyncObjectDatabaseUrl(force = false) {
  const objectDatabaseUrlInput = document.getElementById("object-database-url");
  const nextValue = buildObjectDatabaseUrl();
  if (!nextValue) {
    return;
  }

  if (force || !objectDatabaseUrlInput.value.trim() || objectDatabaseUrlInput.value.trim() === lastGeneratedObjectDatabaseUrl) {
    objectDatabaseUrlInput.value = nextValue;
  }
  lastGeneratedObjectDatabaseUrl = nextValue;
}

function fillDefaults(status) {
  document.getElementById("postgres-host").value = status.postgres_host || "localhost";
  document.getElementById("object-database-url").value = status.object_database_url || "";
  document.getElementById("postgres-db").value = status.postgres_db || "";
  document.getElementById("postgres-user").value = status.postgres_user || "";
  document.getElementById("postgres-password").value = status.postgres_password || "";
  document.getElementById("admin-username").value = status.default_admin_username || "";
  document.getElementById("admin-password").value = status.default_admin_password || "";
  document.getElementById("public-base-url").value = status.public_base_url || "";
  document.getElementById("s3-service-name").value = status.s3_service_name || "s3";
  document.getElementById("s3-default-region").value = status.s3_default_region || "us-east-1";
  document.getElementById("s3-access-key-id").value = status.s3_access_key_id || "";
  document.getElementById("s3-secret-access-key").value = status.s3_secret_access_key || "";
  document.getElementById("s3-require-sigv4").checked = Boolean(status.s3_require_sigv4);
  document.getElementById("s3-max-clock-skew-seconds").value = status.s3_max_clock_skew_seconds;
  document.getElementById("s3-presign-expiry-seconds").value = status.s3_presign_expiry_seconds;
  lastGeneratedObjectDatabaseUrl = buildObjectDatabaseUrl();
}

async function loadSetupStatus() {
  const status = await apiFetch("/admin/setup/status");
  if (!status.needs_setup) {
    try {
      await apiFetch("/admin/me");
      window.location.href = "/panel/pages/dashboard.html";
      return null;
    } catch (_error) {
      window.location.href = "/panel/pages/login.html";
      return null;
    }
  }

  fillDefaults(status);
  return status;
}

function formValues(form) {
  const formData = new FormData(form);
  return {
    object_database_url: String(formData.get("object_database_url") || "").trim(),
    postgres_db: String(formData.get("postgres_db") || "").trim(),
    postgres_user: String(formData.get("postgres_user") || "").trim(),
    postgres_password: String(formData.get("postgres_password") || "").trim(),
    admin_username: String(formData.get("admin_username") || "").trim(),
    admin_password: String(formData.get("admin_password") || "").trim(),
    public_base_url: String(formData.get("public_base_url") || "").trim(),
    s3_service_name: String(formData.get("s3_service_name") || "").trim(),
    s3_default_region: String(formData.get("s3_default_region") || "").trim(),
    s3_access_key_id: String(formData.get("s3_access_key_id") || "").trim(),
    s3_secret_access_key: String(formData.get("s3_secret_access_key") || "").trim(),
    s3_require_sigv4: formData.get("s3_require_sigv4") === "on",
    s3_max_clock_skew_seconds: Number(formData.get("s3_max_clock_skew_seconds") || 900),
    s3_presign_expiry_seconds: Number(formData.get("s3_presign_expiry_seconds") || 3600),
  };
}

async function handleSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) {
    return;
  }

  try {
    const response = await apiFetch("/admin/setup", {
      method: "POST",
      body: JSON.stringify(formValues(form)),
    });
    if (response.object_database_available) {
      window.location.href = "/panel/pages/dashboard.html";
      return;
    }

    showFlash(response.message, "warning");
    window.setTimeout(() => {
      window.location.href = "/panel/pages/dashboard.html";
    }, 1200);
  } catch (error) {
    showFlash(error.message, "error");
  }
}

const status = await loadSetupStatus();
if (status) {
  ["postgres-db", "postgres-user", "postgres-password"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", () => maybeSyncObjectDatabaseUrl(false));
  });
  document.getElementById("setup-form")?.addEventListener("submit", handleSubmit);
}
