import { apiFetch, escapeHtml, formatDate, initializePage } from "/panel/js/common.js";

function renderStats(status) {
  document.getElementById("stats").innerHTML = `
    <div class="card">
      <span class="label">Zones</span>
      <div class="value">${status.zone_total}</div>
      <div class="caption">${status.zone_enabled} enabled</div>
    </div>
    <div class="card">
      <span class="label">Buckets</span>
      <div class="value">${status.bucket_total}</div>
      <div class="caption">${status.bucket_enabled} enabled</div>
    </div>
    <div class="card">
      <span class="label">Indexed Objects</span>
      <div class="value">${status.object_total}</div>
      <div class="caption">PostgreSQL index of FTP-backed files</div>
    </div>
    <div class="card">
      <span class="label">Admins</span>
      <div class="value">${status.admin_user_total}</div>
      <div class="caption">Session-based admin access</div>
    </div>
    <div class="card">
      <span class="label">Access Keys</span>
      <div class="value">${status.s3_access_key_count}</div>
      <div class="caption">${escapeHtml(status.s3_default_access_key_id || "No default key")}</div>
    </div>
  `;
}

function renderSyncList(statuses) {
  const container = document.getElementById("sync-list");
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

await initializePage("dashboard", "Dashboard", "Overview of zones, buckets, indexed files, and the most recent sync activity.");
const status = await apiFetch("/admin/system/status");
renderStats(status);
renderSyncList(status.sync_statuses || []);
document.getElementById("runtime-info").innerHTML = `
  <div class="hero-card">
    <h3 class="section-title">Endpoint Details</h3>
    <p>The endpoint accepts SigV4 headers and S3-style presigned query links. Bucket URLs stay path-style, and direct links now use X-Amz query parameters.</p>
    <p class="list-muted">Endpoint URL: ${escapeHtml(status.s3_endpoint_url)}</p>
    <p class="list-muted">Default region: ${escapeHtml(status.s3_default_region)}</p>
    <p class="list-muted">Default access key: ${escapeHtml(status.s3_default_access_key_id || "No default key configured")}</p>
    <p class="list-muted">Presign expiry: ${escapeHtml(String(status.s3_presign_expiry_seconds))} seconds</p>
    <p class="list-muted">Signature auth required: ${status.s3_require_sigv4 ? "yes" : "no"}</p>
    <p class="list-muted">Last loaded: ${escapeHtml(formatDate(new Date().toISOString()))}</p>
  </div>
`;
