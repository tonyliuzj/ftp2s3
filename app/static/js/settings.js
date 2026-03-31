import {
  apiFetch,
  escapeHtml,
  formatBytes,
  formatDate,
  getQueryParam,
  initializePage,
  loadBuckets,
  loadRegions,
  loadSystemStatus,
  renderTableRows,
  showFlash,
} from "/panel/js/common.js";

const fallbackObjectSettings = {
  s3_service_name: "s3",
  s3_default_region: "us-east-1",
  s3_require_sigv4: true,
  s3_max_clock_skew_seconds: 900,
  s3_presign_expiry_seconds: 3600,
};

let currentSiteSettings = null;
let currentObjectSettings = null;
let currentRegions = [];
let currentBuckets = [];
let currentBucket = "";
let currentZoneSyncBucket = "";
let currentStatus = null;

function fillRegionOptions(regions, selectedCode = "") {
  const select = document.getElementById("s3-default-region");
  const normalizedRegions = [...regions];
  if (selectedCode && !normalizedRegions.some((region) => region.code === selectedCode)) {
    normalizedRegions.unshift({ code: selectedCode, name: selectedCode });
  }
  if (!normalizedRegions.length) {
    normalizedRegions.push({ code: fallbackObjectSettings.s3_default_region, name: fallbackObjectSettings.s3_default_region });
  }

  select.innerHTML = normalizedRegions
    .map((region) => {
      const label = region.name && region.name !== region.code ? `${region.code} - ${region.name}` : region.code;
      return `<option value="${escapeHtml(region.code)}">${escapeHtml(label)}</option>`;
    })
    .join("");
  select.value = selectedCode || normalizedRegions[0]?.code || fallbackObjectSettings.s3_default_region;
}

function fillBucketOptions(selectId, buckets) {
  const select = document.getElementById(selectId);
  select.innerHTML = buckets
    .map((bucket) => `<option value="${escapeHtml(bucket.name)}">${escapeHtml(bucket.name)}</option>`)
    .join("");
  select.disabled = buckets.length === 0;
  if (!buckets.length) {
    select.innerHTML = `<option value="">${currentStatus?.object_database_available ? "Create a bucket first" : "Object database unavailable"}</option>`;
  }
}

function fillSiteForm(settings) {
  currentSiteSettings = settings;
  document.getElementById("public-base-url").value = settings.public_base_url || "";
  document.getElementById("object-database-url").value = settings.object_database_url || "";
  renderPreview();
}

function fillObjectForm(settings) {
  currentObjectSettings = settings;
  document.getElementById("s3-service-name").value = settings.s3_service_name || fallbackObjectSettings.s3_service_name;
  fillRegionOptions(currentRegions, settings.s3_default_region || fallbackObjectSettings.s3_default_region);
  document.getElementById("s3-max-clock-skew-seconds").value = settings.s3_max_clock_skew_seconds;
  document.getElementById("s3-presign-expiry-seconds").value = settings.s3_presign_expiry_seconds;
  document.getElementById("s3-require-sigv4").checked = Boolean(settings.s3_require_sigv4);
  renderPreview();
}

function renderPreview() {
  const siteSettings = currentSiteSettings || { public_base_url: "https://files.example.com" };
  const objectSettings = currentObjectSettings || fallbackObjectSettings;
  const baseUrl = String(siteSettings.public_base_url || "https://files.example.com").replace(/\/+$/, "");
  const region = objectSettings.s3_default_region || fallbackObjectSettings.s3_default_region;
  const expires = objectSettings.s3_presign_expiry_seconds || fallbackObjectSettings.s3_presign_expiry_seconds;
  document.getElementById("settings-link-example").textContent =
    `${baseUrl}/my-bucket/photos/cat.jpg?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=<access-key>/${new Date().toISOString().slice(0, 10).replaceAll("-", "")}/${region}/s3/aws4_request&X-Amz-Date=<timestamp>&X-Amz-Expires=${expires}&X-Amz-SignedHeaders=host&X-Amz-Signature=<signature>`;
  document.getElementById("settings-summary").textContent =
    `Default region: ${region}. SigV4 required: ${objectSettings.s3_require_sigv4 ? "yes" : "no"}. Default presign expiry: ${expires} seconds.`;
}

function siteFormValues(form) {
  const formData = new FormData(form);
  return {
    public_base_url: String(formData.get("public_base_url") || "").trim(),
    object_database_url: String(formData.get("object_database_url") || "").trim(),
  };
}

function objectFormValues(form) {
  const formData = new FormData(form);
  return {
    s3_service_name: String(formData.get("s3_service_name") || "").trim(),
    s3_default_region: String(formData.get("s3_default_region") || "").trim(),
    s3_max_clock_skew_seconds: Number(formData.get("s3_max_clock_skew_seconds") || fallbackObjectSettings.s3_max_clock_skew_seconds),
    s3_presign_expiry_seconds: Number(formData.get("s3_presign_expiry_seconds") || fallbackObjectSettings.s3_presign_expiry_seconds),
    s3_require_sigv4: formData.get("s3_require_sigv4") === "on",
  };
}

function formatMetric(value, suffix = "") {
  if (value === null || value === undefined) {
    return "n/a";
  }
  return `${value}${suffix}`;
}

function setObjectDatabaseState(status) {
  const available = Boolean(status.object_database_available);
  const chip = document.getElementById("object-db-state-chip");
  const inlineWarning = document.getElementById("object-db-inline-warning");
  const disabledState = document.getElementById("object-db-disabled-state");
  const disabledMessage = document.getElementById("object-db-disabled-message");
  const objectSections = document.getElementById("object-db-sections");
  const objectForm = document.getElementById("object-settings-form");
  const objectStateText = document.getElementById("object-database-state-text");
  const rescanAllButton = document.getElementById("status-rescan-all");

  chip.innerHTML = `<span class="chip ${available ? "success" : "error"}">${available ? "Connected" : "Unavailable"}</span>`;
  objectStateText.textContent = available ? "Connected" : status.object_database_error || "Unavailable";
  inlineWarning.hidden = available;
  disabledState.hidden = available;
  objectSections.hidden = !available;
  rescanAllButton.disabled = !available;

  if (available) {
    inlineWarning.textContent = "";
    disabledMessage.textContent = "";
  } else {
    const message = status.object_database_error || "Object metadata database is unavailable.";
    inlineWarning.textContent = `${message} Update the PostgreSQL URL in Local Site Settings, then refresh this page.`;
    disabledMessage.textContent = message;
  }

  objectForm.querySelectorAll("input, select, button").forEach((element) => {
    element.disabled = !available;
  });
}

function renderOverview(status) {
  document.getElementById("status-cards").innerHTML = `
    <div class="card"><span class="label">Zones</span><div class="value">${formatMetric(status.zone_total)}</div><div class="caption">${formatMetric(status.zone_enabled)} enabled</div></div>
    <div class="card"><span class="label">Zone Servers</span><div class="value">${formatMetric(status.zone_server_total)}</div><div class="caption">FTP endpoints across all pools</div></div>
    <div class="card"><span class="label">Mirror Zones</span><div class="value">${formatMetric(status.mirror_all_zone_total)}</div><div class="caption">Zones set to Mirror All</div></div>
    <div class="card"><span class="label">Buckets</span><div class="value">${formatMetric(status.bucket_total)}</div><div class="caption">${formatMetric(status.bucket_enabled)} enabled</div></div>
    <div class="card"><span class="label">Objects</span><div class="value">${formatMetric(status.object_total)}</div><div class="caption">Indexed metadata rows</div></div>
    <div class="card"><span class="label">Admins</span><div class="value">${formatMetric(status.admin_user_total)}</div><div class="caption">Accounts allowed into the panel</div></div>
    <div class="card"><span class="label">Access Keys</span><div class="value">${formatMetric(status.s3_access_key_count)}</div><div class="caption">${escapeHtml(status.s3_default_access_key_id || "Unavailable")}</div></div>
  `;

  document.getElementById("site-database-url").textContent = status.site_database_url;
  document.getElementById("object-database-url-status").textContent = status.object_database_url;
  document.getElementById("endpoint-url").textContent = status.s3_endpoint_url;
  document.getElementById("endpoint-region").textContent = status.s3_default_region || "Unavailable";
  document.getElementById("endpoint-access-key").textContent = status.s3_default_access_key_id || "Unavailable";
  document.getElementById("endpoint-auth-mode").textContent =
    status.s3_require_sigv4 === null || status.s3_require_sigv4 === undefined
      ? "Unavailable"
      : status.s3_require_sigv4
        ? "SigV4 required"
        : "Open";
  document.getElementById("endpoint-presign-expiry").textContent =
    status.s3_presign_expiry_seconds === null || status.s3_presign_expiry_seconds === undefined
      ? "Unavailable"
      : `${status.s3_presign_expiry_seconds} seconds`;
  setObjectDatabaseState(status);
}

function renderSyncStatuses(statuses) {
  const body = document.getElementById("status-sync-table");
  if (!statuses.length) {
    body.innerHTML = `<tr><td colspan="5"><div class="empty-state">No bucket sync activity recorded yet.</div></td></tr>`;
    return;
  }

  body.innerHTML = statuses
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.bucket)}</td>
          <td>${escapeHtml(item.action || "n/a")}</td>
          <td><span class="chip ${item.status === "error" ? "error" : item.status === "running" ? "warning" : "success"}">${escapeHtml(item.status)}</span></td>
          <td>${escapeHtml(formatDate(item.updated_at))}</td>
          <td>${escapeHtml(item.error || "")}</td>
        </tr>
      `,
    )
    .join("");
}

function renderSummary(summary) {
  document.getElementById("sync-summary").innerHTML = `
    <div class="card"><span class="label">FTP Total</span><div class="value">${summary.ftp_total}</div><div class="caption">Files found on FTP</div></div>
    <div class="card"><span class="label">DB Total</span><div class="value">${summary.db_total}</div><div class="caption">Rows in object index</div></div>
    <div class="card"><span class="label">FTP Only</span><div class="value">${summary.ftp_only}</div><div class="caption">Files to insert into DB</div></div>
    <div class="card"><span class="label">DB Only</span><div class="value">${summary.db_only}</div><div class="caption">Stale rows missing on FTP</div></div>
    <div class="card"><span class="label">Path Mismatches</span><div class="value">${summary.path_mismatches}</div><div class="caption">Rows with wrong indexed path</div></div>
    <div class="card"><span class="label">Repaired</span><div class="value">${summary.repaired_rows || 0}</div><div class="caption">Rows changed by last repair</div></div>
  `;
}

function diffRows(items) {
  return items
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.object_key)}</td>
          <td>${escapeHtml(item.ftp_path)}</td>
          <td>${escapeHtml(item.db_object_key || "n/a")}</td>
          <td>${escapeHtml(item.db_ftp_path || "n/a")}</td>
          <td>${escapeHtml(formatBytes(item.size))}</td>
          <td>${escapeHtml(formatDate(item.last_modified))}</td>
        </tr>
      `,
    )
    .join("");
}

function renderZoneSyncSummary(summary) {
  document.getElementById("zone-sync-summary").innerHTML = `
    <div class="card"><span class="label">Objects</span><div class="value">${summary.object_total}</div><div class="caption">Logical objects in this bucket</div></div>
    <div class="card"><span class="label">Expected Replicas</span><div class="value">${summary.expected_replicas}</div><div class="caption">Copies implied by the zone topology</div></div>
    <div class="card"><span class="label">Actual Replicas</span><div class="value">${summary.actual_replicas}</div><div class="caption">Files found across the zone's enabled servers</div></div>
    <div class="card"><span class="label">Missing Objects</span><div class="value">${summary.missing_objects}</div><div class="caption">Objects with no live FTP copy in the zone</div></div>
    <div class="card"><span class="label">Missing Expected</span><div class="value">${summary.missing_expected_copies}</div><div class="caption">Copies missing for the selected strategy</div></div>
    <div class="card"><span class="label">Unexpected Copies</span><div class="value">${summary.unexpected_replicas}</div><div class="caption">Extra copies outside the expected topology</div></div>
    <div class="card"><span class="label">Replica DB Drift</span><div class="value">${summary.db_replica_mismatches}</div><div class="caption">Replica metadata that points at the wrong place</div></div>
    <div class="card"><span class="label">Repaired</span><div class="value">${summary.repaired_replicas || 0}</div><div class="caption">Copies or DB rows fixed by the last repair</div></div>
  `;
}

function zoneSyncRows(items) {
  return items
    .map(
      (item) => `
        <tr>
          <td>${escapeHtml(item.object_key)}</td>
          <td>${escapeHtml(item.ftp_path)}</td>
          <td>${escapeHtml(item.zone_name)}</td>
          <td>${escapeHtml(item.expected_server_name || "n/a")}</td>
          <td>${escapeHtml((item.actual_server_names || []).join(", ") || "n/a")}</td>
          <td>${escapeHtml(item.detail || "")}</td>
        </tr>
      `,
    )
    .join("");
}

function objectDatabaseReady() {
  if (currentStatus?.object_database_available) {
    return true;
  }
  showFlash(currentStatus?.object_database_error || "Object metadata database is unavailable.", "warning");
  return false;
}

async function refreshSiteSettings() {
  const settings = await apiFetch("/admin/settings/site");
  fillSiteForm(settings);
}

async function refreshObjectSettings() {
  if (!currentStatus?.object_database_available) {
    currentRegions = [];
    fillObjectForm(currentObjectSettings || fallbackObjectSettings);
    return;
  }

  const [settings, regions] = await Promise.all([apiFetch("/admin/settings/object"), loadRegions()]);
  currentRegions = regions;
  fillObjectForm(settings);
}

async function refreshBucketsForTools() {
  if (!currentStatus?.object_database_available) {
    currentBuckets = [];
    currentBucket = "";
    currentZoneSyncBucket = "";
    fillBucketOptions("sync-bucket", []);
    fillBucketOptions("zone-sync-bucket", []);
    document.getElementById("sync-status-pill").innerHTML = "";
    document.getElementById("sync-status-detail").textContent = "Object metadata database unavailable.";
    return;
  }

  currentBuckets = await loadBuckets();
  fillBucketOptions("sync-bucket", currentBuckets);
  fillBucketOptions("zone-sync-bucket", currentBuckets);
  currentBucket = currentBuckets.find((bucket) => bucket.name === currentBucket)?.name || currentBuckets[0]?.name || "";
  currentZoneSyncBucket =
    currentBuckets.find((bucket) => bucket.name === currentZoneSyncBucket)?.name || currentBuckets[0]?.name || "";

  if (currentBucket) {
    document.getElementById("sync-bucket").value = currentBucket;
  }
  if (currentZoneSyncBucket) {
    document.getElementById("zone-sync-bucket").value = currentZoneSyncBucket;
  }
}

async function refreshStatus(force = true) {
  currentStatus = await loadSystemStatus(force);
  renderOverview(currentStatus);
  renderSyncStatuses(currentStatus.sync_statuses || []);
}

async function rescanAll() {
  if (!objectDatabaseReady()) {
    return;
  }

  try {
    const response = await apiFetch("/admin/sync/rescan-all", { method: "POST" });
    const summary = response.results
      .map((item) => {
        if (item.status === "error") {
          return `${item.bucket}: error`;
        }
        return `${item.bucket}: ftp_only=${item.summary.ftp_only}, db_only=${item.summary.db_only}`;
      })
      .join(" | ");
    await refreshStatus(true);
    showFlash(summary || "Rescan complete.");
  } catch (error) {
    showFlash(error.message, "error");
  }
}

async function loadBucketStatus() {
  if (!objectDatabaseReady()) {
    return;
  }
  if (!currentBucket) {
    document.getElementById("sync-status-pill").innerHTML = "";
    document.getElementById("sync-status-detail").textContent = "Create a bucket first to preview or repair sync.";
    return;
  }

  try {
    const status = await apiFetch(`/admin/buckets/${encodeURIComponent(currentBucket)}/sync/status`);
    document.getElementById("sync-status-pill").innerHTML = `<span class="chip ${status.status === "error" ? "error" : status.status === "running" ? "warning" : "success"}">${escapeHtml(status.status)}</span>`;
    document.getElementById("sync-status-detail").textContent = status.updated_at
      ? `Last update: ${formatDate(status.updated_at)}`
      : "No sync activity for this bucket yet.";
  } catch (error) {
    showFlash(error.message, "error");
  }
}

async function previewSync() {
  if (!objectDatabaseReady()) {
    return;
  }
  if (!currentBucket) {
    showFlash("Choose a bucket first.", "warning");
    return;
  }

  try {
    const response = await apiFetch(`/admin/buckets/${encodeURIComponent(currentBucket)}/sync/preview`);
    renderSummary(response.summary);
    renderTableRows(document.getElementById("ftp-only-table"), diffRows(response.ftp_only_files), "No FTP-only files.");
    renderTableRows(document.getElementById("db-only-table"), diffRows(response.db_only_files), "No DB-only rows.");
    renderTableRows(document.getElementById("path-mismatch-table"), diffRows(response.path_mismatches), "No path mismatches.");
    renderTableRows(document.getElementById("size-mismatch-table"), diffRows(response.size_mismatches), "No size mismatches.");
    await loadBucketStatus();
    await refreshStatus(true);
    showFlash("Sync preview complete.");
  } catch (error) {
    showFlash(error.message, "error");
  }
}

async function repairSync() {
  if (!objectDatabaseReady()) {
    return;
  }
  if (!currentBucket) {
    showFlash("Choose a bucket first.", "warning");
    return;
  }

  if (!window.confirm(`Repair database rows for bucket "${currentBucket}" using FTP as the source of truth?`)) {
    return;
  }

  try {
    const response = await apiFetch(`/admin/buckets/${encodeURIComponent(currentBucket)}/sync/repair`, {
      method: "POST",
    });
    const preview = await apiFetch(`/admin/buckets/${encodeURIComponent(currentBucket)}/sync/preview`);
    renderSummary({ ...preview.summary, repaired_rows: response.repaired_rows });
    renderTableRows(document.getElementById("ftp-only-table"), diffRows(preview.ftp_only_files), "No FTP-only files.");
    renderTableRows(document.getElementById("db-only-table"), diffRows(preview.db_only_files), "No DB-only rows.");
    renderTableRows(document.getElementById("path-mismatch-table"), diffRows(preview.path_mismatches), "No path mismatches.");
    renderTableRows(document.getElementById("size-mismatch-table"), diffRows(preview.size_mismatches), "No size mismatches.");
    await loadBucketStatus();
    await refreshStatus(true);
    showFlash(`Repair complete. ${response.repaired_rows} rows changed.`);
  } catch (error) {
    showFlash(error.message, "error");
  }
}

async function previewZoneSync() {
  if (!objectDatabaseReady()) {
    return;
  }
  if (!currentZoneSyncBucket) {
    showFlash("Choose a bucket first.", "warning");
    return;
  }

  try {
    const response = await apiFetch(`/admin/buckets/${encodeURIComponent(currentZoneSyncBucket)}/zone-sync/preview`);
    renderZoneSyncSummary(response.summary);
    renderTableRows(document.getElementById("zone-sync-missing-objects-table"), zoneSyncRows(response.missing_objects), "No missing objects.");
    renderTableRows(document.getElementById("zone-sync-missing-expected-table"), zoneSyncRows(response.missing_expected_copies), "No missing expected copies.");
    renderTableRows(document.getElementById("zone-sync-unexpected-table"), zoneSyncRows(response.unexpected_replicas), "No unexpected replica copies.");
    renderTableRows(document.getElementById("zone-sync-db-mismatch-table"), zoneSyncRows(response.db_replica_mismatches), "No replica metadata mismatches.");
    showFlash("Zone sync preview complete.");
  } catch (error) {
    showFlash(error.message, "error");
  }
}

async function repairZoneSync() {
  if (!objectDatabaseReady()) {
    return;
  }
  if (!currentZoneSyncBucket) {
    showFlash("Choose a bucket first.", "warning");
    return;
  }

  if (!window.confirm(`Repair missing zone replicas for bucket "${currentZoneSyncBucket}"?`)) {
    return;
  }

  try {
    const response = await apiFetch(`/admin/buckets/${encodeURIComponent(currentZoneSyncBucket)}/zone-sync/repair`, {
      method: "POST",
    });
    const preview = await apiFetch(`/admin/buckets/${encodeURIComponent(currentZoneSyncBucket)}/zone-sync/preview`);
    renderZoneSyncSummary({ ...preview.summary, repaired_replicas: response.repaired_replicas });
    renderTableRows(document.getElementById("zone-sync-missing-objects-table"), zoneSyncRows(preview.missing_objects), "No missing objects.");
    renderTableRows(document.getElementById("zone-sync-missing-expected-table"), zoneSyncRows(preview.missing_expected_copies), "No missing expected copies.");
    renderTableRows(document.getElementById("zone-sync-unexpected-table"), zoneSyncRows(preview.unexpected_replicas), "No unexpected replica copies.");
    renderTableRows(document.getElementById("zone-sync-db-mismatch-table"), zoneSyncRows(preview.db_replica_mismatches), "No replica metadata mismatches.");
    showFlash(`Zone sync repair complete. ${response.repaired_replicas} placements changed.`);
  } catch (error) {
    showFlash(error.message, "error");
  }
}

async function handleSiteSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) {
    return;
  }

  try {
    const settings = await apiFetch("/admin/settings/site", {
      method: "PUT",
      body: JSON.stringify(siteFormValues(form)),
    });
    fillSiteForm(settings);
    await refreshStatus(true);
    await refreshObjectSettings();
    await refreshBucketsForTools();
    if (currentStatus?.object_database_available) {
      await loadBucketStatus();
    }
    showFlash("Local settings saved.");
  } catch (error) {
    showFlash(error.message, "error");
  }
}

async function handleObjectSubmit(event) {
  event.preventDefault();
  if (!objectDatabaseReady()) {
    return;
  }

  const form = event.currentTarget;
  if (!form.reportValidity()) {
    return;
  }

  try {
    const settings = await apiFetch("/admin/settings/object", {
      method: "PUT",
      body: JSON.stringify(objectFormValues(form)),
    });
    fillObjectForm(settings);
    await refreshStatus(true);
    await refreshObjectSettings();
    showFlash("Object settings saved.");
  } catch (error) {
    showFlash(error.message, "error");
  }
}

const page = await initializePage("settings", "Settings", "Manage local panel settings separately from PostgreSQL-backed object storage settings.");
if (getQueryParam("object-db") === "unavailable") {
  showFlash("Object metadata database unavailable. Local panel access and site settings are still online.", "warning");
}

await refreshSiteSettings();
currentStatus = page.status || (await loadSystemStatus());
renderOverview(currentStatus);
renderPreview();
renderSummary({ ftp_total: 0, db_total: 0, ftp_only: 0, db_only: 0, path_mismatches: 0, size_mismatches: 0, repaired_rows: 0 });
renderTableRows(document.getElementById("ftp-only-table"), "", "Run a preview to compare FTP with the object index.");
renderTableRows(document.getElementById("db-only-table"), "", "Run a preview to compare FTP with the object index.");
renderTableRows(document.getElementById("path-mismatch-table"), "", "Run a preview to compare FTP with the object index.");
renderTableRows(document.getElementById("size-mismatch-table"), "", "Run a preview to compare FTP with the object index.");
renderZoneSyncSummary({ object_total: 0, expected_replicas: 0, actual_replicas: 0, missing_objects: 0, missing_expected_copies: 0, unexpected_replicas: 0, db_replica_mismatches: 0, repaired_replicas: 0 });
renderTableRows(document.getElementById("zone-sync-missing-objects-table"), "", "Run a preview to compare zone replicas.");
renderTableRows(document.getElementById("zone-sync-missing-expected-table"), "", "Run a preview to compare zone replicas.");
renderTableRows(document.getElementById("zone-sync-unexpected-table"), "", "Run a preview to compare zone replicas.");
renderTableRows(document.getElementById("zone-sync-db-mismatch-table"), "", "Run a preview to compare zone replicas.");

if (currentStatus.object_database_available) {
  await refreshObjectSettings();
  await refreshBucketsForTools();
  await loadBucketStatus();
} else {
  fillObjectForm(fallbackObjectSettings);
  await refreshBucketsForTools();
}

document.getElementById("site-settings-form")?.addEventListener("submit", handleSiteSubmit);
document.getElementById("object-settings-form")?.addEventListener("submit", handleObjectSubmit);
document.getElementById("status-refresh")?.addEventListener("click", async () => {
  try {
    await refreshStatus(true);
    await refreshObjectSettings();
    await refreshBucketsForTools();
    if (currentStatus.object_database_available) {
      await loadBucketStatus();
    }
  } catch (error) {
    showFlash(error.message, "error");
  }
});
document.getElementById("status-rescan-all")?.addEventListener("click", () => rescanAll().catch((error) => showFlash(error.message, "error")));
document.getElementById("sync-bucket")?.addEventListener("change", async (event) => {
  currentBucket = event.target.value;
  await loadBucketStatus();
});
document.getElementById("sync-preview")?.addEventListener("click", () => previewSync().catch((error) => showFlash(error.message, "error")));
document.getElementById("sync-repair")?.addEventListener("click", () => repairSync().catch((error) => showFlash(error.message, "error")));
document.getElementById("sync-refresh-status")?.addEventListener("click", () => loadBucketStatus().catch((error) => showFlash(error.message, "error")));
document.getElementById("zone-sync-bucket")?.addEventListener("change", (event) => {
  currentZoneSyncBucket = event.target.value;
});
document.getElementById("zone-sync-preview")?.addEventListener("click", () => previewZoneSync().catch((error) => showFlash(error.message, "error")));
document.getElementById("zone-sync-repair")?.addEventListener("click", () => repairZoneSync().catch((error) => showFlash(error.message, "error")));
