import {
  apiFetch,
  clearFlash,
  escapeHtml,
  getObjectDatabaseError,
  initializePage,
  isObjectDatabaseUnavailable,
  loadBuckets,
  loadRegions,
  loadZones,
  renderTableRows,
  setControlsDisabled,
  showFlash,
} from "/panel/js/common.js";

const BUCKET_NAME_PATTERN = /^[a-z0-9](?:[a-z0-9.-]{1,61}[a-z0-9])$/;
const BUCKET_LABEL_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const IPV4_LIKE_BUCKET_PATTERN = /^\d{1,3}(?:\.\d{1,3}){3}$/;
const REGION_PATTERN = /^[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])$/;

let currentBuckets = [];
let currentRegions = [];

function fillZoneOptions(zones, emptyLabel = "Create a zone first") {
  const select = document.getElementById("bucket-zone");
  select.disabled = zones.length === 0;
  select.innerHTML =
    zones.map((zone) => `<option value="${zone.id}">${escapeHtml(zone.name)}</option>`).join("")
    || `<option value="">${escapeHtml(emptyLabel)}</option>`;
}

function fillRegionOptions(regions, selectedCode = "", emptyLabel = "Create a region first") {
  const select = document.getElementById("bucket-region");
  const hasRegions = regions.length > 0;
  const knownCodes = new Set(regions.map((region) => region.code));
  const options = regions.map((region) => {
    const label = region.name && region.name !== region.code ? `${region.code} - ${region.name}` : region.code;
    return `<option value="${escapeHtml(region.code)}">${escapeHtml(label)}</option>`;
  });

  if (selectedCode && !knownCodes.has(selectedCode)) {
    options.unshift(`<option value="${escapeHtml(selectedCode)}">Legacy region: ${escapeHtml(selectedCode)}</option>`);
  }

  select.disabled = !hasRegions && !selectedCode;
  select.innerHTML = options.join("") || `<option value="">${escapeHtml(emptyLabel)}</option>`;
  select.value = selectedCode || regions[0]?.code || "";
}

function fillForm(bucket = null) {
  document.getElementById("bucket-id").value = bucket?.id || "";
  document.getElementById("bucket-name").value = bucket?.name || "";
  document.getElementById("bucket-zone").value = bucket?.zone_id || "";
  document.getElementById("bucket-base-dir").value = bucket?.base_dir || "/";
  fillRegionOptions(currentRegions, bucket?.region || currentRegions[0]?.code || "");
  document.getElementById("bucket-enabled").checked = bucket ? Boolean(bucket.enabled) : true;
  renderLegacyWarning(bucket);
}

function bucketFormValues(form) {
  const formData = new FormData(form);
  return {
    name: String(formData.get("name") || "").trim(),
    zone_id: Number(formData.get("zone_id")),
    base_dir: String(formData.get("base_dir") || "").trim(),
    region: String(formData.get("region") || "").trim(),
    enabled: formData.get("enabled") === "on",
  };
}

function renderBuckets(buckets) {
  const rows = buckets
    .map(
      (bucket) => `
        <tr>
          <td>
            ${escapeHtml(bucket.name)}
            ${bucketHasLegacyValidationIssue(bucket) ? '<div class="muted">Legacy name or region needs cleanup before saving.</div>' : ""}
          </td>
          <td>${escapeHtml(bucket.region)}</td>
          <td>${escapeHtml(bucket.zone_name)}</td>
          <td>${escapeHtml(bucket.base_dir)}</td>
          <td>${bucket.object_count}</td>
          <td><span class="chip ${bucket.enabled ? "success" : "warning"}">${bucket.enabled ? "enabled" : "disabled"}</span></td>
          <td class="actions">
            <button class="btn btn-secondary" data-action="edit" data-bucket-id="${bucket.id}">Edit</button>
            <button class="btn btn-danger" data-action="delete" data-bucket-id="${bucket.id}">Delete</button>
          </td>
        </tr>
      `,
    )
    .join("");

  renderTableRows(document.getElementById("buckets-table"), rows, "No buckets configured yet.");
}

async function refreshBuckets() {
  currentBuckets = await loadBuckets();
  renderBuckets(currentBuckets);
}

function renderUnavailableState(source) {
  const message = getObjectDatabaseError(source);
  currentBuckets = [];
  currentRegions = [];
  fillForm(null);
  fillZoneOptions([], "Object metadata database unavailable");
  fillRegionOptions([], "", "Object metadata database unavailable");
  renderTableRows(
    document.getElementById("buckets-table"),
    "",
    "Object metadata database unavailable. Reconnect PostgreSQL and refresh this page.",
  );
  setControlsDisabled("#bucket-form", true);
  renderLegacyWarning(null);
  showFlash(message, "warning");
}

async function handleSubmit(event) {
  event.preventDefault();
  clearFlash();
  const form = event.currentTarget;
  const bucketId = document.getElementById("bucket-id").value;

  if (!form.reportValidity()) {
    return;
  }

  try {
    const payload = bucketFormValues(form);
    const method = bucketId ? "PUT" : "POST";
    const url = bucketId ? `/admin/buckets/${bucketId}` : "/admin/buckets";
    await apiFetch(url, { method, body: JSON.stringify(payload) });
    fillForm(null);
    await refreshBuckets();
    showFlash(`Bucket ${bucketId ? "updated" : "created"} successfully.`);
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
  const bucketId = Number(target.dataset.bucketId);
  if (!action || !bucketId) {
    return;
  }

  const bucket = currentBuckets.find((item) => item.id === bucketId);
  if (!bucket) {
    return;
  }

  if (action === "edit") {
    fillForm(bucket);
    return;
  }

  if (action === "delete") {
    if (!window.confirm(`Delete bucket "${bucket.name}"?`)) {
      return;
    }

    try {
      await apiFetch(`/admin/buckets/${bucketId}`, { method: "DELETE" });
      await refreshBuckets();
      fillForm(null);
      showFlash("Bucket deleted.");
    } catch (error) {
      showFlash(error.message, "error");
    }
  }
}

function renderLegacyWarning(bucket) {
  const warning = document.getElementById("bucket-form-warning");
  if (!warning) {
    return;
  }

  if (!bucket) {
    warning.className = "flash";
    warning.textContent = "";
    return;
  }

  const issues = getBucketValidationIssues(bucket);
  if (!issues.length) {
    warning.className = "flash";
    warning.textContent = "";
    return;
  }

  warning.className = "flash visible warning";
  warning.textContent = `This bucket has legacy values that no longer pass validation. ${issues.join(" ")} Update the bucket before saving.`;
}

function bucketHasLegacyValidationIssue(bucket) {
  return getBucketValidationIssues(bucket).length > 0;
}

function getBucketValidationIssues(bucket) {
  const issues = [];
  const bucketName = String(bucket?.name || "").trim();
  const region = String(bucket?.region || "").trim();
  const regionExists = currentRegions.some((item) => item.code === region);

  if (!isValidBucketName(bucketName)) {
    issues.push("Bucket names must be 3-63 characters, lowercase, DNS-compliant, and start/end with a letter or number.");
  }

  if (!isValidRegion(region)) {
    issues.push("Regions must use lowercase letters, numbers, and hyphens only.");
  } else if (!regionExists) {
    issues.push("Add this region to the Regions page or pick a different region.");
  }

  return issues;
}

function isValidBucketName(value) {
  if (value.length < 3 || value.length > 63) {
    return false;
  }
  if (!BUCKET_NAME_PATTERN.test(value)) {
    return false;
  }
  if (value.includes("..") || value.includes(".-") || value.includes("-.")) {
    return false;
  }
  if (IPV4_LIKE_BUCKET_PATTERN.test(value)) {
    return false;
  }
  return value.split(".").every((label) => BUCKET_LABEL_PATTERN.test(label));
}

function isValidRegion(value) {
  return value.length >= 3 && value.length <= 32 && REGION_PATTERN.test(value);
}

const page = await initializePage("buckets", "Buckets", "Buckets belong to zones, pick their region from the shared catalog, and write under a configured FTP base directory.", {
  requiresObjectDatabase: true,
});
fillForm(null);
if (isObjectDatabaseUnavailable(page.status)) {
  renderUnavailableState(page.status);
} else {
  try {
    const [zones, regions] = await Promise.all([loadZones(), loadRegions()]);
    currentRegions = regions;
    fillZoneOptions(zones);
    fillRegionOptions(currentRegions, currentRegions[0]?.code || "");
    await refreshBuckets();
  } catch (error) {
    renderUnavailableState(error);
  }
}
document.getElementById("bucket-form")?.addEventListener("submit", handleSubmit);
document.getElementById("bucket-reset")?.addEventListener("click", () => fillForm(null));
document.getElementById("buckets-table")?.addEventListener("click", handleTableClick);
