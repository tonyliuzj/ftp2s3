const navItems = [
  { key: "dashboard", label: "Dashboard", href: "/panel/pages/dashboard.html", requiresObjectDatabase: false },
  { key: "settings", label: "Settings", href: "/panel/pages/settings.html", requiresObjectDatabase: false },
  { key: "regions", label: "Regions", href: "/panel/pages/regions.html", requiresObjectDatabase: true },
  { key: "keys", label: "Keys", href: "/panel/pages/keys.html", requiresObjectDatabase: true },
  { key: "zones", label: "Zones", href: "/panel/pages/zones.html", requiresObjectDatabase: true },
  { key: "buckets", label: "Buckets", href: "/panel/pages/buckets.html", requiresObjectDatabase: true },
  { key: "files", label: "File Browser", href: "/panel/pages/files.html", requiresObjectDatabase: true },
];

let cachedSystemStatus = null;

export function isObjectDatabaseUnavailable(status) {
  return status?.object_database_available === false;
}

export function getObjectDatabaseError(source) {
  if (source instanceof Error && source.message) {
    return source.message;
  }

  if (typeof source === "string" && source.trim()) {
    return source.trim();
  }

  if (source && typeof source.object_database_error === "string" && source.object_database_error) {
    return source.object_database_error;
  }

  return "Object metadata database is unavailable.";
}

export function setControlsDisabled(root, disabled = true) {
  const element = typeof root === "string" ? document.querySelector(root) : root;
  if (!element) {
    return;
  }

  element.querySelectorAll("button, input, select, textarea").forEach((control) => {
    control.disabled = disabled;
  });
}

export async function apiFetch(path, options = {}) {
  const requestOptions = { credentials: "same-origin", ...options };
  const headers = new Headers(options.headers || {});

  if (requestOptions.body && !(requestOptions.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  requestOptions.headers = headers;

  const response = await fetch(path, requestOptions);
  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();

  if (!response.ok) {
    const message = formatApiError(payload);
    throw new Error(message);
  }

  return payload;
}

export function uploadWithProgress(path, formData, handlers = {}) {
  return new Promise((resolve, reject) => {
    const callbacks = typeof handlers === "function" ? { onProgress: handlers } : handlers;
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path, true);
    xhr.withCredentials = true;

    xhr.upload.addEventListener("loadstart", () => {
      callbacks.onStart?.();
    });

    xhr.upload.addEventListener("progress", (event) => {
      if (!callbacks.onProgress || !event.lengthComputable) {
        return;
      }
      callbacks.onProgress({
        loaded: event.loaded,
        total: event.total,
        percent: Math.round((event.loaded / event.total) * 100),
      });
    });

    xhr.addEventListener("load", () => {
      const contentType = xhr.getResponseHeader("content-type") || "";
      let payload = xhr.responseText;
      if (contentType.includes("application/json")) {
        try {
          payload = JSON.parse(xhr.responseText);
        } catch (_error) {
          payload = xhr.responseText;
        }
      }

      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload);
        return;
      }

      reject(new Error(formatApiError(payload)));
    });

    xhr.addEventListener("error", () => {
      reject(new Error("Upload failed before the server returned a response."));
    });

    xhr.addEventListener("loadend", () => {
      callbacks.onFinish?.();
    });

    xhr.send(formData);
  });
}

function formatApiError(payload) {
  if (typeof payload === "string") {
    return payload;
  }

  if (Array.isArray(payload)) {
    return payload.map(formatValidationItem).join(" | ") || "Request failed.";
  }

  if (payload && Array.isArray(payload.errors) && payload.errors.length) {
    return payload.errors.join(" | ");
  }

  if (payload && Array.isArray(payload.detail) && payload.detail.length) {
    return payload.detail.map(formatValidationItem).join(" | ");
  }

  if (payload && typeof payload.detail === "string" && payload.detail) {
    return payload.detail;
  }

  if (payload && typeof payload.message === "string" && payload.message) {
    return payload.message;
  }

  return "Request failed.";
}

function formatValidationItem(item) {
  if (typeof item === "string") {
    return item;
  }

  if (!item || typeof item !== "object") {
    return "Invalid request.";
  }

  const rawLoc = Array.isArray(item.loc) ? item.loc.filter((part) => !["body", "query", "path"].includes(String(part))) : [];
  const field = rawLoc.join(" -> ");
  const rawMessage = typeof item.msg === "string" ? item.msg : "Invalid value.";
  const message = rawMessage.startsWith("Value error, ") ? rawMessage.slice("Value error, ".length) : rawMessage;
  return field ? `${field}: ${message}` : message;
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function formatDate(value) {
  if (!value) {
    return "n/a";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "n/a";
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function formatBytes(value) {
  if (value === null || value === undefined) {
    return "n/a";
  }

  const size = Number(value);
  if (Number.isNaN(size)) {
    return "n/a";
  }

  const units = ["B", "KB", "MB", "GB", "TB"];
  let unitIndex = 0;
  let current = size;
  while (current >= 1024 && unitIndex < units.length - 1) {
    current /= 1024;
    unitIndex += 1;
  }

  return `${current.toFixed(current >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

export function encodePathSegments(value) {
  return String(value)
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
}

export function buildAdminDownloadUrl(bucketName, objectKey) {
  return new URL(
    `/admin/buckets/${encodeURIComponent(bucketName)}/download/${encodePathSegments(objectKey)}`,
    window.location.origin,
  ).toString();
}

export function buildAdminDirectObjectUrl(bucketName, objectKey) {
  return new URL(
    `/admin/buckets/${encodeURIComponent(bucketName)}/view/${encodePathSegments(objectKey)}`,
    window.location.origin,
  ).toString();
}

export async function presignObjectUrl(bucketName, objectKey, options = {}) {
  const payload = { object_key: objectKey };
  if (options.expiresIn) {
    payload.expires_in = options.expiresIn;
  }
  if (options.accessKeyId) {
    payload.access_key_id = options.accessKeyId;
  }

  return apiFetch(`/admin/buckets/${encodeURIComponent(bucketName)}/presign`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function copyText(value) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "readonly");
  textarea.style.position = "absolute";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

export function showFlash(message, type = "info") {
  const flash = document.getElementById("flash");
  if (!flash) {
    return;
  }

  flash.className = `flash visible ${type}`;
  flash.textContent = message;
}

export function clearFlash() {
  const flash = document.getElementById("flash");
  if (!flash) {
    return;
  }

  flash.className = "flash";
  flash.textContent = "";
}

export function getQueryParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}

export function setQueryParams(params) {
  const url = new URL(window.location.href);
  Object.entries(params).forEach(([key, value]) => {
    if (value === null || value === undefined || value === "") {
      url.searchParams.delete(key);
    } else {
      url.searchParams.set(key, value);
    }
  });

  window.history.replaceState({}, "", url);
}

export async function requireAuth(redirectOnFail = true) {
  try {
    return await apiFetch("/admin/me");
  } catch (error) {
    if (redirectOnFail) {
      window.location.href = "/panel/pages/login.html";
    }
    throw error;
  }
}

export async function logout() {
  await apiFetch("/admin/logout", { method: "POST" });
  window.location.href = "/panel/pages/login.html";
}

export async function initializePage(pageKey, title, subtitle, options = {}) {
  const user = await requireAuth(true);
  let status = null;
  try {
    status = await loadSystemStatus();
  } catch (_error) {
    status = null;
  }

  renderShell(pageKey, title, subtitle, user, status);

  return { user, status };
}

export async function loadZones() {
  return apiFetch("/admin/zones");
}

export async function loadRegions() {
  return apiFetch("/admin/regions");
}

export async function loadBuckets() {
  return apiFetch("/admin/buckets");
}

export async function loadSystemStatus(force = false) {
  if (cachedSystemStatus && !force) {
    return cachedSystemStatus;
  }

  cachedSystemStatus = await apiFetch("/admin/system/status");
  return cachedSystemStatus;
}

export function renderBreadcrumbs(prefix) {
  const parts = prefix ? prefix.split("/") : [];
  const crumbs = [{ label: "root", prefix: "" }];
  parts.forEach((part, index) => {
    crumbs.push({
      label: part,
      prefix: parts.slice(0, index + 1).join("/"),
    });
  });
  return crumbs;
}

export function renderTableRows(container, rowsHtml, emptyMessage = "No rows yet.") {
  if (!container) {
    return;
  }

  container.innerHTML = rowsHtml || `<tr><td colspan="99"><div class="empty-state">${escapeHtml(emptyMessage)}</div></td></tr>`;
}

function renderShell(pageKey, title, subtitle, user, status = null) {
  const sidebar = document.getElementById("sidebar");
  const topbar = document.getElementById("topbar");
  const objectDatabaseAvailable = !status || status.object_database_available !== false;

  if (sidebar) {
    sidebar.innerHTML = `
      <div class="brand">
        <span class="eyebrow">FTP-backed S3</span>
        <h1>ftp2s3</h1>
        <p>Bucket to zone to FTP server routing, with the database acting as the searchable index.</p>
      </div>
      <nav class="nav-links">
        ${navItems
          .map(
            (item) => `
              <a
                class="nav-link ${item.key === pageKey ? "active" : ""}"
                href="${item.href}"
              >
                <span>${item.label}</span>
              </a>
            `,
          )
          .join("")}
      </nav>
      <div class="sidebar-spacer"></div>
      <div class="sidebar-footer">
        <div class="sidebar-user">Signed in as ${escapeHtml(user.username)}</div>
        <button id="logout-button" class="btn btn-secondary sidebar-logout">Log Out</button>
      </div>
    `;

    const logoutButton = document.getElementById("logout-button");
    logoutButton?.addEventListener("click", () => {
      logout().catch((error) => showFlash(error.message, "error"));
    });
  }

  if (topbar) {
    topbar.innerHTML = `
      <div>
        <h2>${escapeHtml(title)}</h2>
        <p>${escapeHtml(subtitle)}</p>
      </div>
      <div class="sidebar-user">${escapeHtml(user.username)}</div>
    `;
  }
}
