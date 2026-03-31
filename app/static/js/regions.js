import { apiFetch, clearFlash, escapeHtml, initializePage, loadRegions, renderTableRows, showFlash } from "/panel/js/common.js";

let currentRegions = [];

function formValues(form) {
  const formData = new FormData(form);
  return {
    code: String(formData.get("code") || "").trim(),
    name: String(formData.get("name") || "").trim(),
  };
}

function fillForm(region = null) {
  document.getElementById("region-id").value = region?.id || "";
  document.getElementById("region-code").value = region?.code || "";
  document.getElementById("region-name").value = region?.name || "";
}

function renderRegions(regions) {
  const rows = regions
    .map(
      (region) => `
        <tr>
          <td>${escapeHtml(region.code)}</td>
          <td>${escapeHtml(region.name)}</td>
          <td>${region.bucket_count}</td>
          <td><span class="chip ${region.is_default ? "success" : ""}">${region.is_default ? "default" : "not default"}</span></td>
          <td class="actions">
            <button class="btn btn-secondary" data-action="edit" data-region-id="${region.id}">Edit</button>
            <button class="btn btn-danger" data-action="delete" data-region-id="${region.id}">Delete</button>
          </td>
        </tr>
      `,
    )
    .join("");

  renderTableRows(document.getElementById("regions-table"), rows, "No regions configured yet.");
}

async function refreshRegions() {
  currentRegions = await loadRegions();
  renderRegions(currentRegions);
}

async function handleSubmit(event) {
  event.preventDefault();
  clearFlash();
  const form = event.currentTarget;
  if (!form.reportValidity()) {
    return;
  }

  const regionId = document.getElementById("region-id").value;
  const payload = formValues(form);

  try {
    const method = regionId ? "PUT" : "POST";
    const url = regionId ? `/admin/regions/${regionId}` : "/admin/regions";
    await apiFetch(url, { method, body: JSON.stringify(payload) });
    fillForm(null);
    await refreshRegions();
    showFlash(`Region ${regionId ? "updated" : "created"} successfully.`);
  } catch (error) {
    showFlash(error.message, "error");
  }
}

async function handleTableClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }

  const action = target.dataset.action;
  const regionId = Number(target.dataset.regionId);
  if (!action || !regionId) {
    return;
  }

  const region = currentRegions.find((item) => item.id === regionId);
  if (!region) {
    return;
  }

  if (action === "edit") {
    fillForm(region);
    return;
  }

  if (action === "delete") {
    if (!window.confirm(`Delete region "${region.code}"?`)) {
      return;
    }

    try {
      await apiFetch(`/admin/regions/${regionId}`, { method: "DELETE" });
      fillForm(null);
      await refreshRegions();
      showFlash("Region deleted.");
    } catch (error) {
      showFlash(error.message, "error");
    }
  }
}

await initializePage("regions", "Regions", "Create the region catalog once, then pick from it whenever you create or update a bucket.", {
  requiresObjectDatabase: true,
});
await refreshRegions();
fillForm(null);
document.getElementById("region-form")?.addEventListener("submit", handleSubmit);
document.getElementById("region-reset")?.addEventListener("click", () => fillForm(null));
document.getElementById("regions-table")?.addEventListener("click", handleTableClick);
