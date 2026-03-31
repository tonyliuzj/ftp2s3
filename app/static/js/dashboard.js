import { escapeHtml, formatDate, initializePage, loadSystemStatus } from "/panel/js/common.js";

function formatMetric(value) {
  return value === null || value === undefined ? "n/a" : String(value);
}

function renderStats(status) {
  document.getElementById("stats").innerHTML = `
    <div class="card">
      <span class="label">Object DB</span>
      <div class="value">${status.object_database_available ? "Up" : "Down"}</div>
      <div class="caption">${escapeHtml(status.object_database_available ? "Object metadata features available" : "Local panel stays online; object features are paused")}</div>
    </div>
    <div class="card">
      <span class="label">Zones</span>
      <div class="value">${formatMetric(status.zone_total)}</div>
      <div class="caption">${formatMetric(status.zone_enabled)} enabled</div>
    </div>
    <div class="card">
      <span class="label">Buckets</span>
      <div class="value">${formatMetric(status.bucket_total)}</div>
      <div class="caption">${formatMetric(status.bucket_enabled)} enabled</div>
    </div>
    <div class="card">
      <span class="label">Indexed Objects</span>
      <div class="value">${formatMetric(status.object_total)}</div>
      <div class="caption">PostgreSQL index of FTP-backed files</div>
    </div>
    <div class="card">
      <span class="label">Admins</span>
      <div class="value">${formatMetric(status.admin_user_total)}</div>
      <div class="caption">Session-based admin access</div>
    </div>
    <div class="card">
      <span class="label">Access Keys</span>
      <div class="value">${formatMetric(status.s3_access_key_count)}</div>
      <div class="caption">${escapeHtml(status.s3_default_access_key_id || "Unavailable")}</div>
    </div>
  `;
}

function renderSyncList(statuses, objectDatabaseAvailable) {
  const container = document.getElementById("sync-list");
  if (!objectDatabaseAvailable) {
    container.innerHTML = `<div class="empty-state">The object metadata database is unavailable, so sync history is currently hidden.</div>`;
    return;
  }

  if (!statuses.length) {
    container.innerHTML = `<div class="empty-state">No sync jobs have been run yet.</div>`;
    return;
  }

  container.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Bucket</th>
            <th>Action</th>
            <th>Status</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          ${statuses
            .map(
              (item) => `
                <tr>
                  <td>${escapeHtml(item.bucket)}</td>
                  <td>${escapeHtml(item.action || "n/a")}</td>
                  <td><span class="chip ${item.status === "error" ? "error" : item.status === "running" ? "warning" : "success"}">${escapeHtml(item.status)}</span></td>
                  <td>${escapeHtml(formatDate(item.updated_at))}</td>
                </tr>
              `,
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderRuntimeInfo(status) {
  const objectDatabaseSummary = status.object_database_available
    ? "Object metadata database is connected."
    : escapeHtml(status.object_database_error || "Object metadata database is unavailable.");

  document.getElementById("runtime-info").innerHTML = `
    <div class="hero-card">
      <h3 class="section-title">Runtime State</h3>
      <p>The panel keeps local auth and site settings in SQLite. Object storage metadata, buckets, zones, keys, and sync state depend on PostgreSQL.</p>
      <p class="list-muted">Site database: ${escapeHtml(status.site_database_url)}</p>
      <p class="list-muted">Object database: ${escapeHtml(status.object_database_url)}</p>
      <p class="list-muted">Object database status: ${objectDatabaseSummary}</p>
      <p class="list-muted">Endpoint URL: ${escapeHtml(status.s3_endpoint_url)}</p>
      <p class="list-muted">Default region: ${escapeHtml(status.s3_default_region || "Unavailable")}</p>
      <p class="list-muted">Default access key: ${escapeHtml(status.s3_default_access_key_id || "Unavailable")}</p>
      <p class="list-muted">Presign expiry: ${escapeHtml(status.s3_presign_expiry_seconds === null || status.s3_presign_expiry_seconds === undefined ? "Unavailable" : `${status.s3_presign_expiry_seconds} seconds`)}</p>
      <p class="list-muted">Signature auth required: ${status.s3_require_sigv4 === null || status.s3_require_sigv4 === undefined ? "Unavailable" : status.s3_require_sigv4 ? "yes" : "no"}</p>
      <p class="list-muted">Last loaded: ${escapeHtml(formatDate(new Date().toISOString()))}</p>
    </div>
  `;
}

const page = await initializePage("dashboard", "Dashboard", "Overview of panel health, local auth/settings, and the current object-storage state.");
const status = page.status || (await loadSystemStatus());
renderStats(status);
renderSyncList(status.sync_statuses || [], status.object_database_available);
renderRuntimeInfo(status);
