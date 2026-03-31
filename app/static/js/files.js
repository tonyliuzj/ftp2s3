import {
  apiFetch,
  buildAdminDownloadUrl,
  clearFlash,
  copyText,
  encodePathSegments,
  escapeHtml,
  formatBytes,
  formatDate,
  getQueryParam,
  initializePage,
  loadBuckets,
  presignObjectUrl,
  renderBreadcrumbs,
  renderTableRows,
  setQueryParams,
  showFlash,
  uploadWithProgress,
} from "/panel/js/common.js";

let currentBucket = "";
let currentPrefix = "";
let currentSearchQuery = "";
let availableBuckets = [];
let progressResetTimer = null;

function fillBucketOptions(buckets) {
  const select = document.getElementById("files-bucket");
  select.innerHTML = buckets
    .map((bucket) => `<option value="${escapeHtml(bucket.name)}">${escapeHtml(bucket.name)}</option>`)
    .join("");
  select.disabled = buckets.length === 0;
  if (!buckets.length) {
    select.innerHTML = '<option value="">Create a bucket first</option>';
  }
}

function isSearchMode() {
  return Boolean(currentSearchQuery.trim());
}

function renderBreadcrumbBar() {
  const container = document.getElementById("files-breadcrumbs");
  if (isSearchMode()) {
    container.innerHTML = "";
    container.hidden = true;
    return;
  }

  container.hidden = false;
  const crumbs = renderBreadcrumbs(currentPrefix);
  container.innerHTML = crumbs
    .map((crumb) => `<a href="#" data-prefix="${escapeHtml(crumb.prefix)}">${escapeHtml(crumb.label)}</a>`)
    .join("<span>/</span>");
}

function objectKeyForUpload(filename) {
  const manualValue = document.getElementById("upload-object-key").value.trim();
  if (manualValue) {
    if (currentPrefix && !manualValue.includes("/")) {
      return `${currentPrefix}/${manualValue}`;
    }
    return manualValue;
  }

  return currentPrefix ? `${currentPrefix}/${filename}` : filename;
}

function setUploadProgress(percent, loaded = 0, total = 0) {
  const panel = document.getElementById("upload-progress");
  const value = document.getElementById("upload-progress-value");
  const label = document.getElementById("upload-progress-label");
  const bar = document.getElementById("upload-progress-bar");

  if (progressResetTimer) {
    window.clearTimeout(progressResetTimer);
    progressResetTimer = null;
  }

  panel.hidden = false;
  value.textContent = `${percent}%`;
  label.textContent =
    total > 0
      ? `Uploading ${formatBytes(loaded)} of ${formatBytes(total)}`
      : "Uploading...";
  bar.style.width = `${percent}%`;
}

function resetUploadProgress() {
  if (progressResetTimer) {
    window.clearTimeout(progressResetTimer);
    progressResetTimer = null;
  }

  const panel = document.getElementById("upload-progress");
  const value = document.getElementById("upload-progress-value");
  const label = document.getElementById("upload-progress-label");
  const bar = document.getElementById("upload-progress-bar");

  panel.hidden = true;
  value.textContent = "0%";
  label.textContent = "Uploading...";
  bar.style.width = "0%";
}

function renderUploadedLink(objectKey) {
  const container = document.getElementById("upload-link-result");
  if (!objectKey || !currentBucket) {
    container.innerHTML = "";
    return;
  }

  container.innerHTML = `
    Latest upload:
    <span>${escapeHtml(objectKey)}</span>
    <button class="btn btn-secondary" type="button" data-action="open-link" data-object-key="${escapeHtml(objectKey)}">Open Direct Link</button>
    <button class="btn btn-secondary" type="button" data-action="copy-latest-link" data-object-key="${escapeHtml(objectKey)}">Copy Direct Link</button>
  `;
}

function updateModeChrome({ title, subtitle, hint }) {
  document.getElementById("files-results-title").textContent = title;
  document.getElementById("files-results-subtitle").textContent = subtitle;
  document.getElementById("files-mode-hint").textContent = hint;
}

function renderObjectRows(objects) {
  return objects
    .map(
      (object) => `
        <tr>
          <td>${escapeHtml(object.object_key.split("/").at(-1))}</td>
          <td>
            <div>${escapeHtml(object.object_key)}</div>
            <div class="muted">${escapeHtml(object.ftp_path)}</div>
          </td>
          <td>${escapeHtml(formatBytes(object.size))}</td>
          <td>${escapeHtml(formatDate(object.last_modified))}</td>
          <td class="actions">
            <button class="btn btn-secondary" data-action="open-link" data-object-key="${escapeHtml(object.object_key)}">Open</button>
            <a class="btn btn-secondary" href="${buildAdminDownloadUrl(currentBucket, object.object_key)}">Download</a>
            <button class="btn btn-secondary" data-action="copy-link" data-object-key="${escapeHtml(object.object_key)}">Copy Direct Link</button>
            <button class="btn btn-danger" data-action="delete" data-object-key="${escapeHtml(object.object_key)}">Delete</button>
          </td>
        </tr>
      `,
    )
    .join("");
}

async function renderListing() {
  if (!currentBucket) {
    updateModeChrome({
      title: "Indexed Files",
      subtitle: "Create a bucket first, then browse or search its indexed objects.",
      hint: "",
    });
    renderTableRows(document.getElementById("files-table"), "", "Create a bucket first.");
    return;
  }

  renderBreadcrumbBar();
  document.getElementById("upload-path-hint").textContent = currentPrefix
    ? `Uploads default to ${currentPrefix}/`
    : "Uploads default to the bucket root.";

  if (isSearchMode()) {
    const response = await apiFetch(
      `/admin/buckets/${encodeURIComponent(currentBucket)}/search?q=${encodeURIComponent(currentSearchQuery)}`,
    );
    updateModeChrome({
      title: "Search Results",
      subtitle: "Results come from the indexed metadata, not from a live FTP directory scan.",
      hint: `Showing ${response.count} matching objects in ${currentBucket}. Clear search to go back to folder browsing.`,
    });
    renderTableRows(document.getElementById("files-table"), renderObjectRows(response.objects), "No objects matched this search.");
    return;
  }

  const searchParams = new URLSearchParams();
  if (currentPrefix) {
    searchParams.set("prefix", currentPrefix);
  }

  const queryString = searchParams.toString();
  const path = `/admin/buckets/${encodeURIComponent(currentBucket)}/objects${queryString ? `?${queryString}` : ""}`;
  const response = await apiFetch(path);

  updateModeChrome({
    title: "Indexed Files",
    subtitle: "Folders are derived from object key prefixes stored in the database index.",
    hint: currentPrefix ? `Browsing ${currentPrefix}/ inside ${currentBucket}.` : `Browsing the root of ${currentBucket}.`,
  });

  const parentRow =
    currentPrefix && currentPrefix.includes("/")
      ? `<tr>
          <td><a class="folder-link" href="#" data-prefix="${escapeHtml(currentPrefix.split("/").slice(0, -1).join("/"))}">..</a></td>
          <td class="muted">Parent directory</td>
          <td></td>
          <td></td>
          <td></td>
        </tr>`
      : currentPrefix
        ? `<tr>
            <td><a class="folder-link" href="#" data-prefix="">..</a></td>
            <td class="muted">Back to root</td>
            <td></td>
            <td></td>
            <td></td>
          </tr>`
        : "";

  const directoryRows = response.directories
    .map(
      (directory) => `
        <tr>
          <td><a class="folder-link" href="#" data-prefix="${escapeHtml(directory.prefix)}">${escapeHtml(directory.name)}/</a></td>
          <td class="muted">Directory derived from indexed object prefixes</td>
          <td></td>
          <td></td>
          <td></td>
        </tr>
      `,
    )
    .join("");

  renderTableRows(
    document.getElementById("files-table"),
    `${parentRow}${directoryRows}${renderObjectRows(response.objects)}`,
    "No indexed objects in this folder.",
  );
}

async function handleBucketChange() {
  currentBucket = document.getElementById("files-bucket").value;
  renderUploadedLink("");
  setQueryParams({ bucket: currentBucket, prefix: currentPrefix, q: currentSearchQuery || null });
  await renderListing();
}

async function handleUpload(event) {
  event.preventDefault();
  clearFlash();
  if (!currentBucket) {
    showFlash("Choose a bucket first.", "warning");
    return;
  }

  const fileInput = document.getElementById("upload-file");
  const file = fileInput.files?.[0];
  if (!file) {
    showFlash("Choose a file to upload.", "warning");
    return;
  }

  const formData = new FormData();
  formData.set("file", file);
  const objectKey = objectKeyForUpload(file.name);
  formData.set("object_key", objectKey);
  const submitButton = document.getElementById("upload-submit");

  try {
    submitButton.disabled = true;
    submitButton.textContent = "Uploading...";
    await uploadWithProgress(
      `/admin/buckets/${encodeURIComponent(currentBucket)}/upload`,
      formData,
      {
        onStart: () => {
          setUploadProgress(0, 0, file.size);
        },
        onProgress: ({ loaded, total, percent }) => {
          setUploadProgress(percent, loaded, total);
        },
      },
    );
    fileInput.value = "";
    document.getElementById("upload-object-key").value = "";
    await renderListing();
    renderUploadedLink(objectKey);
    showFlash("Upload complete.");
  } catch (error) {
    renderUploadedLink("");
    showFlash(error.message, "error");
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Upload to FTP";
    resetUploadProgress();
  }
}

async function handleTableClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }

  const prefix = target.dataset.prefix;
  if (prefix !== undefined) {
    event.preventDefault();
    currentPrefix = prefix;
    currentSearchQuery = "";
    document.getElementById("files-search-query").value = "";
    setQueryParams({ bucket: currentBucket, prefix: currentPrefix, q: null });
    await renderListing();
    return;
  }

  if (target.dataset.action === "delete") {
    const objectKey = target.dataset.objectKey;
    if (!objectKey) {
      return;
    }
    if (!window.confirm(`Delete ${objectKey}?`)) {
      return;
    }

    try {
      await apiFetch(
        `/admin/buckets/${encodeURIComponent(currentBucket)}/objects/${encodePathSegments(objectKey)}`,
        { method: "DELETE" },
      );
      await renderListing();
      showFlash("Object deleted.");
    } catch (error) {
      showFlash(error.message, "error");
    }
    return;
  }

  if (target.dataset.action === "copy-link" || target.dataset.action === "copy-latest-link" || target.dataset.action === "open-link") {
    const objectKey = target.dataset.objectKey;
    if (!objectKey) {
      return;
    }

    try {
      const response = await presignObjectUrl(currentBucket, objectKey);
      if (target.dataset.action === "open-link") {
        window.open(response.url, "_blank", "noopener,noreferrer");
        showFlash("Direct link opened.");
        return;
      }

      await copyText(response.url);
      showFlash("Direct link copied.");
    } catch (error) {
      showFlash(error.message || "Could not copy the link.", "error");
    }
  }
}

async function handleSearchSubmit(event) {
  event.preventDefault();
  currentSearchQuery = document.getElementById("files-search-query").value.trim();
  setQueryParams({ bucket: currentBucket, prefix: currentPrefix, q: currentSearchQuery || null });
  await renderListing();
}

async function clearSearch() {
  currentSearchQuery = "";
  document.getElementById("files-search-query").value = "";
  setQueryParams({ bucket: currentBucket, prefix: currentPrefix, q: null });
  await renderListing();
}

await initializePage("files", "File Browser", "Browse folders, upload files, search indexed object paths, and open S3-style direct links from one place.", {
  requiresObjectDatabase: true,
});
availableBuckets = await loadBuckets();
fillBucketOptions(availableBuckets);
currentBucket = getQueryParam("bucket") || availableBuckets[0]?.name || "";
currentPrefix = getQueryParam("prefix") || "";
currentSearchQuery = getQueryParam("q") || "";
if (currentBucket) {
  document.getElementById("files-bucket").value = currentBucket;
}
document.getElementById("files-search-query").value = currentSearchQuery;
resetUploadProgress();
await renderListing();
document.getElementById("files-bucket")?.addEventListener("change", handleBucketChange);
document.getElementById("upload-form")?.addEventListener("submit", handleUpload);
document.getElementById("files-table")?.addEventListener("click", handleTableClick);
document.getElementById("files-breadcrumbs")?.addEventListener("click", handleTableClick);
document.getElementById("upload-link-result")?.addEventListener("click", handleTableClick);
document.getElementById("files-search-form")?.addEventListener("submit", handleSearchSubmit);
document.getElementById("files-search-clear")?.addEventListener("click", () => clearSearch().catch((error) => showFlash(error.message, "error")));
document.getElementById("files-refresh")?.addEventListener("click", () => renderListing().catch((error) => showFlash(error.message, "error")));
