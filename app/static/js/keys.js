import { apiFetch, clearFlash, copyText, escapeHtml, formatDate, initializePage, renderTableRows, showFlash } from "/panel/js/common.js";

let currentKeys = [];
let latestCreated = null;

function formValues(form) {
  const formData = new FormData(form);
  return {
    name: String(formData.get("name") || "").trim(),
    access_key_id: String(formData.get("access_key_id") || "").trim() || null,
    secret_access_key: String(formData.get("secret_access_key") || "").trim() || null,
    enabled: formData.get("enabled") === "on",
    is_default: formData.get("is_default") === "on",
  };
}

function fillForm(key = null) {
  const accessKeyField = document.getElementById("key-access-key-id");
  const secretField = document.getElementById("key-secret-access-key");

  document.getElementById("key-id").value = key?.id || "";
  document.getElementById("key-name").value = key?.name || "";
  accessKeyField.value = key?.access_key_id || "";
  accessKeyField.disabled = Boolean(key);
  secretField.value = "";
  secretField.disabled = Boolean(key);
  document.getElementById("key-enabled").checked = key ? Boolean(key.enabled) : true;
  document.getElementById("key-default").checked = key ? Boolean(key.is_default) : false;
}

function renderSecretPanel(result) {
  const panel = document.getElementById("key-secret-panel");
  if (!result) {
    panel.hidden = true;
    document.getElementById("key-secret-access-key-preview").textContent = "";
    latestCreated = null;
    return;
  }

  latestCreated = result;
  panel.hidden = false;
  document.getElementById("key-secret-access-key-preview").textContent =
    `Access Key ID: ${result.key.access_key_id}\nSecret Access Key: ${result.secret_access_key}`;
}

function renderKeys(keys) {
  const rows = keys
    .map(
      (key) => `
        <tr>
          <td>${escapeHtml(key.name)}</td>
          <td>${escapeHtml(key.access_key_id)}</td>
          <td>${escapeHtml(key.masked_secret_access_key)}</td>
          <td><span class="chip ${key.enabled ? "success" : "warning"}">${key.enabled ? "enabled" : "disabled"}</span></td>
          <td><span class="chip ${key.is_default ? "success" : ""}">${key.is_default ? "default" : "not default"}</span></td>
          <td>${escapeHtml(formatDate(key.last_used_at))}</td>
          <td class="actions">
            <button class="btn btn-secondary" data-action="edit" data-key-id="${key.id}">Edit</button>
            <button class="btn btn-secondary" data-action="rotate" data-key-id="${key.id}">Rotate</button>
            <button class="btn btn-danger" data-action="delete" data-key-id="${key.id}">Delete</button>
          </td>
        </tr>
      `,
    )
    .join("");

  renderTableRows(document.getElementById("keys-table"), rows, "No access keys have been created yet.");
}

async function refreshKeys() {
  currentKeys = await apiFetch("/admin/keys");
  renderKeys(currentKeys);
}

async function handleSubmit(event) {
  event.preventDefault();
  clearFlash();
  const form = event.currentTarget;
  if (!form.reportValidity()) {
    return;
  }

  const keyId = document.getElementById("key-id").value;
  const payload = formValues(form);

  try {
    if (keyId) {
      const updatePayload = {
        name: payload.name,
        enabled: payload.enabled,
        is_default: payload.is_default,
      };
      await apiFetch(`/admin/keys/${keyId}`, { method: "PUT", body: JSON.stringify(updatePayload) });
      renderSecretPanel(null);
      showFlash("Access key updated.");
    } else {
      const result = await apiFetch("/admin/keys", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      renderSecretPanel(result);
      showFlash("Access key created. Copy the secret now.");
    }

    fillForm(null);
    await refreshKeys();
  } catch (error) {
    showFlash(error.message, "error");
  }
}

async function handleTableClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }

  const keyId = Number(target.dataset.keyId);
  const action = target.dataset.action;
  if (!keyId || !action) {
    return;
  }

  const key = currentKeys.find((item) => item.id === keyId);
  if (!key) {
    return;
  }

  if (action === "edit") {
    fillForm(key);
    return;
  }

  if (action === "rotate") {
    if (!window.confirm(`Rotate the secret for "${key.name}"? Existing clients will stop working until updated.`)) {
      return;
    }

    try {
      const result = await apiFetch(`/admin/keys/${keyId}/rotate`, { method: "POST" });
      renderSecretPanel(result);
      await refreshKeys();
      showFlash("Secret rotated. Copy the new value now.");
    } catch (error) {
      showFlash(error.message, "error");
    }
    return;
  }

  if (action === "delete") {
    if (!window.confirm(`Delete access key "${key.name}"?`)) {
      return;
    }

    try {
      await apiFetch(`/admin/keys/${keyId}`, { method: "DELETE" });
      fillForm(null);
      renderSecretPanel(null);
      await refreshKeys();
      showFlash("Access key deleted.");
    } catch (error) {
      showFlash(error.message, "error");
    }
  }
}

document.getElementById("copy-key-id")?.addEventListener("click", async () => {
  if (!latestCreated) {
    return;
  }
  try {
    await copyText(latestCreated.key.access_key_id);
    showFlash("Access key ID copied.");
  } catch (error) {
    showFlash(error.message || "Could not copy the access key ID.", "error");
  }
});

document.getElementById("copy-key-secret")?.addEventListener("click", async () => {
  if (!latestCreated) {
    return;
  }
  try {
    await copyText(latestCreated.secret_access_key);
    showFlash("Secret access key copied.");
  } catch (error) {
    showFlash(error.message || "Could not copy the secret access key.", "error");
  }
});

await initializePage("keys", "Keys", "Create and rotate multiple S3 access keys. One enabled key can be marked as the default for presigned direct links.", {
  requiresObjectDatabase: true,
});
await refreshKeys();
fillForm(null);
renderSecretPanel(null);
document.getElementById("key-form")?.addEventListener("submit", handleSubmit);
document.getElementById("key-reset")?.addEventListener("click", () => {
  fillForm(null);
  renderSecretPanel(null);
});
document.getElementById("keys-table")?.addEventListener("click", handleTableClick);
