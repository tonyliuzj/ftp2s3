import { apiFetch, clearFlash, escapeHtml, initializePage, loadZones, renderTableRows, showFlash } from "/panel/js/common.js";

let currentZones = [];

function emptyServer() {
  return {
    id: null,
    name: "",
    ftp_host: "",
    ftp_port: 21,
    ftp_username: "",
    ftp_password: "",
    enabled: true,
    capacity_bytes: "",
  };
}

function strategyLabel(strategy) {
  if (strategy === "round_robin") {
    return "Round Robin";
  }
  if (strategy === "mirror_all") {
    return "Mirror All Servers";
  }
  return "Fill First";
}

function fillForm(zone = null) {
  document.getElementById("zone-id").value = zone?.id || "";
  document.getElementById("zone-name").value = zone?.name || "";
  document.getElementById("zone-pool-strategy").value = zone?.pool_strategy || "fill_first";
  document.getElementById("zone-enabled").checked = zone ? Boolean(zone.enabled) : true;
  renderServerRows(
    zone?.servers?.map((server) => ({
      ...server,
      ftp_password: "",
      capacity_bytes: server.capacity_bytes ?? "",
    })) || [emptyServer()],
  );
}

function renderServerRows(servers) {
  const body = document.getElementById("zone-servers-editor");
  body.innerHTML = servers
    .map(
      (server, index) => `
        <tr data-server-index="${index}">
          <td>
            <input data-field="name" value="${escapeHtml(server.name || "")}" placeholder="Primary" required />
            <input data-field="id" type="hidden" value="${server.id || ""}" />
          </td>
          <td><input data-field="ftp_host" value="${escapeHtml(server.ftp_host || "")}" placeholder="ftp.example.com" required /></td>
          <td><input data-field="ftp_port" type="number" min="1" value="${Number(server.ftp_port || 21)}" required /></td>
          <td><input data-field="ftp_username" value="${escapeHtml(server.ftp_username || "")}" placeholder="ftp-user" required /></td>
          <td><input data-field="ftp_password" type="password" value="${escapeHtml(server.ftp_password || "")}" placeholder="${server.id ? "Leave blank to keep" : "Required"}" ${server.id ? "" : "required"} /></td>
          <td><input data-field="capacity_bytes" type="number" min="0" value="${escapeHtml(server.capacity_bytes ?? "")}" placeholder="Optional bytes" /></td>
          <td><label class="field-inline"><input data-field="enabled" type="checkbox" ${server.enabled ? "checked" : ""} /><span>${server.enabled ? "enabled" : "disabled"}</span></label></td>
          <td class="actions">
            <button class="btn btn-secondary" type="button" data-action="move-up" data-server-index="${index}">Up</button>
            <button class="btn btn-secondary" type="button" data-action="move-down" data-server-index="${index}">Down</button>
          </td>
          <td><button class="btn btn-danger" type="button" data-action="remove-server" data-server-index="${index}">Remove</button></td>
        </tr>
      `,
    )
    .join("");
}

function collectServerRows() {
  const rows = Array.from(document.querySelectorAll("#zone-servers-editor tr"));
  return rows.map((row, index) => {
    const getField = (name) => row.querySelector(`[data-field="${name}"]`);
    return {
      id: Number(getField("id")?.value || "") || null,
      name: String(getField("name")?.value || "").trim(),
      ftp_host: String(getField("ftp_host")?.value || "").trim(),
      ftp_port: Number(getField("ftp_port")?.value || 21),
      ftp_username: String(getField("ftp_username")?.value || "").trim(),
      ftp_password: String(getField("ftp_password")?.value || "").trim() || null,
      enabled: getField("enabled")?.checked === true,
      sort_order: index,
      capacity_bytes: getField("capacity_bytes")?.value ? Number(getField("capacity_bytes").value) : null,
    };
  });
}

function zoneFormValues() {
  return {
    name: document.getElementById("zone-name").value.trim(),
    pool_strategy: document.getElementById("zone-pool-strategy").value,
    enabled: document.getElementById("zone-enabled").checked,
    servers: collectServerRows(),
  };
}

function validateZonePayload(payload, isUpdate) {
  if (!payload.name) {
    throw new Error("Zone name is required.");
  }
  if (!payload.servers.length) {
    throw new Error("Add at least one FTP server to the zone.");
  }

  payload.servers.forEach((server, index) => {
    if (!server.name || !server.ftp_host || !server.ftp_username) {
      throw new Error(`Server row ${index + 1} is missing required fields.`);
    }
    if (!isUpdate && !server.ftp_password) {
      throw new Error(`Server row ${index + 1} needs an FTP password.`);
    }
    if (isUpdate && !server.id && !server.ftp_password) {
      throw new Error(`New server row ${index + 1} needs an FTP password.`);
    }
  });
}

function renderZones(zones) {
  const rows = zones
    .map(
      (zone) => `
        <tr>
          <td>${escapeHtml(zone.name)}</td>
          <td>${escapeHtml(strategyLabel(zone.pool_strategy))}</td>
          <td>
            <div>${zone.server_count} server(s)</div>
            <div class="muted">${zone.servers.map((server) => escapeHtml(server.name)).join(", ")}</div>
          </td>
          <td>${zone.bucket_count}</td>
          <td><span class="chip ${zone.enabled ? "success" : "warning"}">${zone.enabled ? "enabled" : "disabled"}</span></td>
          <td class="actions">
            <button class="btn btn-secondary" data-action="edit" data-zone-id="${zone.id}">Edit</button>
            <button class="btn btn-danger" data-action="delete" data-zone-id="${zone.id}">Delete</button>
          </td>
        </tr>
      `,
    )
    .join("");

  renderTableRows(document.getElementById("zones-table"), rows, "No zones configured yet.");
}

async function refreshZones() {
  currentZones = await loadZones();
  renderZones(currentZones);
}

async function handleSubmit(event) {
  event.preventDefault();
  clearFlash();
  const zoneId = document.getElementById("zone-id").value;
  const payload = zoneFormValues();

  try {
    validateZonePayload(payload, Boolean(zoneId));
    const method = zoneId ? "PUT" : "POST";
    const url = zoneId ? `/admin/zones/${zoneId}` : "/admin/zones";
    await apiFetch(url, { method, body: JSON.stringify(payload) });
    await refreshZones();
    fillForm(null);
    showFlash(`Zone ${zoneId ? "updated" : "created"} successfully.`);
  } catch (error) {
    showFlash(error.message, "error");
  }
}

async function handleZoneTableClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }

  const action = target.dataset.action;
  const zoneId = Number(target.dataset.zoneId);
  if (!action || !zoneId) {
    return;
  }

  const zone = currentZones.find((item) => item.id === zoneId);
  if (!zone) {
    return;
  }

  if (action === "edit") {
    fillForm(zone);
    return;
  }

  if (action === "delete") {
    if (!window.confirm(`Delete zone "${zone.name}"?`)) {
      return;
    }

    try {
      await apiFetch(`/admin/zones/${zoneId}`, { method: "DELETE" });
      await refreshZones();
      fillForm(null);
      showFlash("Zone deleted.");
    } catch (error) {
      showFlash(error.message, "error");
    }
  }
}

function handleServerEditorClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }

  const action = target.dataset.action;
  const index = Number(target.dataset.serverIndex);
  if (!action || Number.isNaN(index)) {
    return;
  }

  const servers = collectServerRows();
  if (action === "remove-server") {
    if (servers.length === 1) {
      showFlash("A zone needs at least one FTP server.", "warning");
      return;
    }
    servers.splice(index, 1);
  } else if (action === "move-up" && index > 0) {
    [servers[index - 1], servers[index]] = [servers[index], servers[index - 1]];
  } else if (action === "move-down" && index < servers.length - 1) {
    [servers[index], servers[index + 1]] = [servers[index + 1], servers[index]];
  } else {
    return;
  }

  renderServerRows(servers);
}

await initializePage(
  "zones",
  "Zones",
  "Each zone can fill servers in order, rotate uploads across them, or mirror every upload to all enabled servers in the zone.",
);
await refreshZones();
fillForm(null);
document.getElementById("zone-form")?.addEventListener("submit", handleSubmit);
document.getElementById("zone-reset")?.addEventListener("click", () => fillForm(null));
document.getElementById("zones-table")?.addEventListener("click", handleZoneTableClick);
document.getElementById("zone-servers-editor")?.addEventListener("click", handleServerEditorClick);
document.getElementById("zone-add-server")?.addEventListener("click", () => {
  const servers = collectServerRows();
  servers.push(emptyServer());
  renderServerRows(servers);
});
