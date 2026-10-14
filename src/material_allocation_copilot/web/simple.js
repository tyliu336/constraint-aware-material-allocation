const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const state = {
  bearerToken: "",
  userId: "",
  projectId: "",
  inventory: null,
  materials: [],
  members: [],
  preferenceInterpretation: null,
  selectedNode: null,
  currentStep: 1,
  view: { yaw: -0.68, pitch: 0.48, zoom: 1 },
  projectedNodes: [],
};

const failureMessages = {
  400: "请求格式不正确。",
  401: "连接凭据无效或已失效，请重新粘贴 Token。",
  403: "当前凭据没有执行这一步的权限。",
  404: "请求的数据不存在，请重新开始当前步骤。",
  409: "相同标识的数据已经存在，请再试一次。",
  413: "文件过大，当前服务最多接受 5 MB。",
  422: "材料或结构数据未满足系统合同。",
  500: "服务内部错误，未生成结果。",
  502: "模型调用失败，请稍后重试。",
  503: "模型尚未配置，请检查启动终端中的 API key 和模型设置。",
};

class ApiError extends Error {
  constructor(status, message, context = {}) {
    super(message);
    this.status = status;
    this.context = context;
  }
}

function id(prefix) {
  const random = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  return `${prefix}-${random.replaceAll("-", "").slice(0, 20)}`;
}

function requestId() {
  return id("web").slice(0, 63);
}

function idempotencyKey(prefix) {
  return id(prefix).slice(0, 128);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  headers.set("X-Request-ID", requestId());
  if (state.bearerToken) headers.set("Authorization", `Bearer ${state.bearerToken}`);
  let body = options.body;
  if (body !== undefined && typeof body !== "string" && !(body instanceof Blob)) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(body);
  }
  const response = await fetch(path, { ...options, headers, body });
  const responseRequestId = response.headers.get("X-Request-ID") || "—";
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const fallback = failureMessages[response.status] || "请求失败，未写入后续数据。";
    throw new ApiError(
      response.status,
      payload?.error?.message || fallback,
      { ...(payload?.error?.context || {}), requestId: responseRequestId },
    );
  }
  return { data: payload, requestId: responseRequestId };
}

function setLive(message, tone = "neutral") {
  const region = $("#live-region");
  region.textContent = message;
  region.dataset.tone = tone;
}

function handleError(error, context) {
  let prefix = failureMessages[error.status] || error.message || "发生未知错误。";
  const edgeCount = Number(error.context?.edge_count);
  const maximumEdgeCount = Number(error.context?.maximum_edge_count);
  if (
    error.status === 422
    && Number.isFinite(edgeCount)
    && Number.isFinite(maximumEdgeCount)
  ) {
    prefix = `候选组合 ${edgeCount} 条，超过当前排序上限 ${maximumEdgeCount}；请改用 80 行演示 CSV，或减少材料/构件。`;
  }
  const suffix = error.context?.requestId ? ` Request ID: ${error.context.requestId}` : "";
  setLive(`${context}失败：${prefix}${suffix}`, "danger");
}

async function withBusy(button, busyText, work) {
  const original = button.textContent;
  button.disabled = true;
  button.classList.add("busy");
  button.textContent = busyText;
  try {
    return await work();
  } finally {
    button.classList.remove("busy");
    button.textContent = original;
    button.disabled = false;
    updateControls();
  }
}

async function sha256(value) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function textElement(tag, text, className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

function cleanIdentifier(value, fallback) {
  const cleaned = String(value || "").trim().replaceAll(/[^A-Za-z0-9._-]/g, "-").slice(0, 128);
  return /^[A-Za-z0-9]/.test(cleaned) ? cleaned : fallback;
}

async function connect() {
  const token = $("#bearer-token-simple").value.trim();
  const requestedProject = cleanIdentifier($("#project-scope").value, "PRJ-demo");
  if (!token) throw new ApiError(401, "请输入启动脚本生成的 Token。");
  state.bearerToken = token;
  let projects;
  try {
    const { data: identity } = await api("/api/v1/me");
    if (!identity.project_ids.includes(requestedProject)) {
      throw new ApiError(403, `当前 Token 未授权项目 ${requestedProject}。`);
    }
    ({ data: projects } = await api("/api/v1/projects?limit=50&offset=0"));
    const existing = projects.items.find((project) => project.project_id === requestedProject);
    if (!existing) {
      try {
        await api("/api/v1/projects", {
          method: "POST",
          body: { project_id: requestedProject, name: "木构配料演示" },
        });
      } catch (error) {
        if (error.status !== 409) throw error;
      }
    }
    state.projectId = requestedProject;
    state.userId = identity.user_id;
    $("#connection-state").textContent = "本地已连接";
    $("#connection-state").dataset.tone = "success";
    $("#connection-dialog").close();
    setLive("本地服务连接成功。现在可以导入材料。", "success");
  } catch (error) {
    state.bearerToken = "";
    state.projectId = "";
    state.userId = "";
    $("#connection-state").textContent = "连接失败";
    $("#connection-state").dataset.tone = "danger";
    throw error;
  }
}

function clearConnection() {
  state.bearerToken = "";
  state.projectId = "";
  state.userId = "";
  $("#bearer-token-simple").value = "";
  $("#connection-state").textContent = "尚未连接";
  $("#connection-state").dataset.tone = "warning";
  setLive("本页内存中的凭据已清除。", "neutral");
}

function parseCsv(text) {
  const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/).filter((line) => line.trim());
  if (lines.length < 2) throw new Error("CSV 至少需要表头和一行材料数据。");
  const parseLine = (line) => {
    const cells = [];
    let value = "";
    let quoted = false;
    for (let index = 0; index < line.length; index += 1) {
      const char = line[index];
      if (char === '"' && quoted && line[index + 1] === '"') {
        value += '"';
        index += 1;
      } else if (char === '"') {
        quoted = !quoted;
      } else if (char === "," && !quoted) {
        cells.push(value.trim());
        value = "";
      } else {
        value += char;
      }
    }
    cells.push(value.trim());
    return cells;
  };
  const headers = parseLine(lines[0]);
  return lines.slice(1).map((line) => {
    const values = parseLine(line);
    return Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""]));
  });
}

function validateMaterialShape(item, index) {
  const required = ["length_mm", "section_width_mm", "section_height_mm"];
  for (const field of required) {
    const value = Number(item[field]);
    if (!Number.isFinite(value) || value <= 0) {
      throw new Error(`第 ${index + 1} 行的 ${field} 必须是正数。`);
    }
  }
}

function surfaceKnotsFromRow(raw, materialId, rowIndex) {
  if (Array.isArray(raw.surface_knots)) return raw.surface_knots;
  const definitions = [
    ["wide", "whole", "knotTypeWideFaceWhole", "knotSizeWideFaceWhole_mm"],
    ["narrow", "whole", "knotTypeNarrowFaceWhole", "knotSizeNarrowFaceWhole_mm"],
    ["wide", "tested", "knotTypeWideFaceTested", "knotSizeWideFaceTested_mm"],
    ["narrow", "tested", "knotTypeNarrowFaceTested", "knotSizeNarrowFaceTested_mm"],
  ];
  const observations = [];
  for (const [face, scope, typeField, sizeField] of definitions) {
    const code = String(raw[typeField] || "").trim();
    if (!code) continue;
    if (!/^(0|[SG][0-5])$/.test(code)) {
      throw new Error(`第 ${rowIndex + 1} 行的 ${typeField} 不是有效的 TreeTrace 节疤代码。`);
    }
    const rawSize = String(raw[sizeField] ?? "").trim();
    const size = rawSize === "" || rawSize === "NA" ? null : Number(rawSize);
    if (size !== null && (!Number.isFinite(size) || size < 0)) {
      throw new Error(`第 ${rowIndex + 1} 行的 ${sizeField} 必须是非负数。`);
    }
    observations.push({
      face,
      scope,
      knot_type_code: code,
      size_mm: size,
      source_ref: `treetrace:${materialId}:${typeField}+${sizeField}`,
    });
  }
  return observations;
}

async function normalizeMaterials(rawItems, inventoryId, sourceHash, { demo = false } = {}) {
  if (!Array.isArray(rawItems) || !rawItems.length) throw new Error("文件中没有材料记录。");
  if (rawItems.length > 10000) throw new Error("一次最多导入 10000 根材料。");
  const importedAt = new Date().toISOString();
  const materials = [];
  for (let index = 0; index < rawItems.length; index += 1) {
    const raw = rawItems[index];
    validateMaterialShape(raw, index);
    const materialId = cleanIdentifier(raw.material_id, `MAT-${String(index + 1).padStart(3, "0")}`);
    const normalized = {
      material_id: materialId,
      inventory_version_id: inventoryId,
      source_system: demo ? "synthetic_demo" : "simple_web_upload",
      source_record_id: materialId,
      source_file_sha256: sourceHash,
      source_row_sha256: await sha256(JSON.stringify(raw)),
      measured_at: raw.measured_at || importedAt,
      length_mm: Number(raw.length_mm),
      section_width_mm: Number(raw.section_width_mm),
      section_height_mm: Number(raw.section_height_mm),
      species: raw.species || null,
      moisture_content_pct: raw.moisture_content_pct ? Number(raw.moisture_content_pct) : null,
      structural_use_status: raw.structural_use_status || (demo ? "eligible" : "not_evaluable"),
      strength_grade: raw.strength_grade || (demo ? "C24" : null),
      design_property_ref: raw.design_property_ref || null,
      assessment_method: raw.assessment_method || (demo ? "engineering_assessment" : "unknown"),
      assessor_id: raw.assessor_id || (demo ? "SYNTHETIC-DEMO" : null),
      assessment_date: raw.assessment_date || (demo ? new Date().toISOString().slice(0, 10) : null),
      assessment_evidence_ref: raw.assessment_evidence_ref || (demo ? "synthetic-demo-evidence" : null),
      defect_data_status: raw.defect_data_status || (demo ? "complete" : "unknown"),
      defect_evidence_ref: raw.defect_evidence_ref || (demo ? "synthetic-demo-defect-record" : null),
      defects: Array.isArray(raw.defects) ? raw.defects : [],
      surface_knots: surfaceKnotsFromRow(raw, materialId, index),
      reuse_history_status: raw.reuse_history_status || "unknown",
      treatment_contamination_status: raw.treatment_contamination_status || "unknown",
      availability_status: raw.availability_status || "available",
      storage_location: raw.storage_location || null,
      image_manifest_ref: raw.image_manifest_ref || null,
      notes: demo ? "Synthetic demonstration record; not field evidence." : (raw.notes || null),
    };
    materials.push(normalized);
  }
  return materials;
}

function demoMaterialRows() {
  return [
    ["DEMO-01", 3150, 45, 95], ["DEMO-02", 3050, 45, 95],
    ["DEMO-03", 2900, 45, 95], ["DEMO-04", 2750, 45, 95],
    ["DEMO-05", 2450, 45, 95], ["DEMO-06", 2200, 45, 95],
    ["DEMO-07", 1800, 45, 95], ["DEMO-08", 1500, 45, 95],
  ].map(([material_id, length_mm, section_width_mm, section_height_mm]) => ({
    material_id, length_mm, section_width_mm, section_height_mm, species: "SPF",
  }));
}

async function importMaterials(rawItems, label, options = {}) {
  if (!state.bearerToken || !state.projectId) {
    $("#connection-dialog").showModal();
    throw new Error("请先连接本地服务。");
  }
  const inventoryId = id("INV");
  const sourceText = JSON.stringify(rawItems);
  const sourceHash = await sha256(sourceText);
  const materials = await normalizeMaterials(rawItems, inventoryId, sourceHash, options);
  const { data, requestId: correlationId } = await api(
    `/api/v1/projects/${encodeURIComponent(state.projectId)}/inventory/import`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey("inventory") },
      body: {
        inventory_version_id: inventoryId,
        source_system: options.demo ? "synthetic_demo" : "simple_web_upload",
        source_path: options.demo ? "builtin:synthetic-timber-v1" : `upload:${label}`,
        source_sha256: sourceHash,
        imported_at: new Date().toISOString(),
        materials,
      },
    },
  );
  state.inventory = data;
  state.materials = materials;
  renderMaterialSummary();
  updateControls();
  setLive(`已导入 ${materials.length} 根材料。Request ID: ${correlationId}`, "success");
}

async function readMaterialFile(file) {
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) throw new Error("文件超过 5 MB。请拆分后再导入。");
  const text = await file.text();
  let rows;
  if (file.name.toLowerCase().endsWith(".json")) {
    const parsed = JSON.parse(text);
    rows = Array.isArray(parsed) ? parsed : parsed.materials;
  } else {
    rows = parseCsv(text);
  }
  await importMaterials(rows, file.name);
}

function renderMaterialSummary() {
  const lengths = state.materials.map((item) => Number(item.length_mm));
  const sections = new Set(state.materials.map((item) => `${item.section_width_mm}×${item.section_height_mm}`));
  const eligible = state.materials.filter((item) => item.structural_use_status === "eligible").length;
  $("#material-count").textContent = `${state.materials.length} 根`;
  $("#material-length-range").textContent = `${Math.min(...lengths)}–${Math.max(...lengths)} mm`;
  $("#material-section-count").textContent = `${sections.size} 种`;
  $("#material-evidence").textContent = eligible === state.materials.length ? "演示评估齐全" : `${eligible}/${state.materials.length} 有结构评估`;
  $("#material-summary").hidden = false;
  $("#step-1-status").textContent = `${state.materials.length} 根材料`;
}

function gridSettings() {
  return {
    x: Number($("#grid-x").value),
    y: Number($("#grid-y").value),
    z: Number($("#grid-z").value),
    spacing: Number($("#grid-spacing").value),
  };
}

function gridNodes() {
  const grid = gridSettings();
  const nodes = [];
  for (let y = 0; y < grid.y; y += 1) {
    for (let z = 0; z < grid.z; z += 1) {
      for (let x = 0; x < grid.x; x += 1) nodes.push({ id: `${x}-${y}-${z}`, x, y, z });
    }
  }
  return nodes;
}

function projectNode(node, width, height) {
  const grid = gridSettings();
  const cx = (grid.x - 1) / 2;
  const cy = (grid.y - 1) / 2;
  const cz = (grid.z - 1) / 2;
  const x = node.x - cx;
  const y = node.y - cy;
  const z = node.z - cz;
  const cosY = Math.cos(state.view.yaw);
  const sinY = Math.sin(state.view.yaw);
  const rotatedX = x * cosY - z * sinY;
  const rotatedZ = x * sinY + z * cosY;
  const cosP = Math.cos(state.view.pitch);
  const sinP = Math.sin(state.view.pitch);
  const rotatedY = y * cosP - rotatedZ * sinP;
  const depth = y * sinP + rotatedZ * cosP;
  const base = Math.min(width / Math.max(grid.x + grid.z, 5), height / Math.max(grid.y + 1.5, 4));
  const scale = base * 1.48 * state.view.zoom;
  return { ...node, sx: width / 2 + rotatedX * scale, sy: height / 2 - rotatedY * scale, depth };
}

function adjacent(a, b) {
  return Math.abs(a.x - b.x) + Math.abs(a.y - b.y) + Math.abs(a.z - b.z) === 1;
}

function resizeAndDraw() {
  const canvas = $("#structure-canvas");
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const pixelWidth = Math.max(1, Math.round(rect.width * dpr));
  const pixelHeight = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }
  const context = canvas.getContext("2d");
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  drawCanvas(context, rect.width, rect.height);
}

function drawCanvas(context, width, height) {
  context.clearRect(0, 0, width, height);
  const gradient = context.createRadialGradient(width * 0.5, height * 0.45, 10, width * 0.5, height * 0.5, width * 0.7);
  gradient.addColorStop(0, "#183c31");
  gradient.addColorStop(1, "#0d2922");
  context.fillStyle = gradient;
  context.fillRect(0, 0, width, height);
  const projected = gridNodes().map((node) => projectNode(node, width, height));
  state.projectedNodes = projected;
  const byId = new Map(projected.map((node) => [node.id, node]));

  context.lineWidth = 1;
  context.strokeStyle = "rgba(204, 224, 207, 0.12)";
  for (let first = 0; first < projected.length; first += 1) {
    for (let second = first + 1; second < projected.length; second += 1) {
      const a = projected[first];
      const b = projected[second];
      if (!adjacent(a, b)) continue;
      context.beginPath();
      context.moveTo(a.sx, a.sy);
      context.lineTo(b.sx, b.sy);
      context.stroke();
    }
  }

  context.lineCap = "round";
  for (const member of state.members) {
    const a = byId.get(member.start);
    const b = byId.get(member.end);
    if (!a || !b) continue;
    context.beginPath();
    context.moveTo(a.sx, a.sy);
    context.lineTo(b.sx, b.sy);
    context.lineWidth = 7;
    context.strokeStyle = "rgba(7, 19, 16, 0.6)";
    context.stroke();
    context.lineWidth = 3.5;
    context.strokeStyle = member.role === "column" ? "#b8d68c" : member.role === "brace" ? "#e0a95c" : "#f1d595";
    context.stroke();
  }

  projected.sort((a, b) => a.depth - b.depth);
  for (const node of projected) {
    const selected = node.id === state.selectedNode;
    context.beginPath();
    context.arc(node.sx, node.sy, selected ? 7.5 : 4.7, 0, Math.PI * 2);
    context.fillStyle = selected ? "#f2b756" : "#dcebc6";
    context.fill();
    context.lineWidth = selected ? 4 : 1.5;
    context.strokeStyle = selected ? "rgba(242,183,86,.28)" : "rgba(8,28,22,.9)";
    context.stroke();
  }
  renderPointControls(projected);
}

function renderPointControls(nodes) {
  const controls = $("#point-controls");
  const existing = new Map(
    Array.from(controls.children).map((button) => [button.dataset.nodeId, button]),
  );
  const active = new Set();
  for (const node of nodes) {
    active.add(node.id);
    let button = existing.get(node.id);
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.className = "point-control";
      button.dataset.nodeId = node.id;
      button.setAttribute("aria-label", `连接点 ${node.id}`);
      controls.append(button);
    }
    button.style.left = `${node.sx}px`;
    button.style.top = `${node.sy}px`;
    button.setAttribute("aria-pressed", String(node.id === state.selectedNode));
  }
  for (const [nodeId, button] of existing) {
    if (!active.has(nodeId)) button.remove();
  }
}

function closestNode(x, y) {
  let closest = null;
  let distance = 16;
  for (const node of state.projectedNodes) {
    const candidate = Math.hypot(node.sx - x, node.sy - y);
    if (candidate < distance) {
      closest = node;
      distance = candidate;
    }
  }
  return closest;
}

function memberRole(a, b) {
  const dx = Math.abs(a.x - b.x);
  const dy = Math.abs(a.y - b.y);
  const dz = Math.abs(a.z - b.z);
  if (dy > dx + dz) return "column";
  if (dy === 0) return "beam";
  return "brace";
}

function addConnection(nodeId) {
  if (!state.selectedNode) {
    state.selectedNode = nodeId;
    $("#canvas-mode").textContent = "已选第一个点，请选择终点";
    resizeAndDraw();
    return;
  }
  if (state.selectedNode === nodeId) {
    state.selectedNode = null;
    $("#canvas-mode").textContent = "已取消选择，请重新选择起点";
    resizeAndDraw();
    return;
  }
  const duplicate = state.members.some((member) =>
    (member.start === state.selectedNode && member.end === nodeId) ||
    (member.start === nodeId && member.end === state.selectedNode));
  if (duplicate) {
    state.selectedNode = null;
    $("#canvas-mode").textContent = "这两个点已经连接";
    resizeAndDraw();
    return;
  }
  const nodes = new Map(gridNodes().map((node) => [node.id, node]));
  const a = nodes.get(state.selectedNode);
  const b = nodes.get(nodeId);
  const grid = gridSettings();
  const length = Math.round(Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z) * grid.spacing);
  state.members.push({
    id: `MEM-${String(state.members.length + 1).padStart(3, "0")}`,
    start: state.selectedNode,
    end: nodeId,
    role: memberRole(a, b),
    length,
    width: Number($("#default-width").value),
    height: Number($("#default-height").value),
  });
  state.selectedNode = null;
  $("#canvas-mode").textContent = "构件已创建，请继续选择起点";
  renderMembers();
  resizeAndDraw();
}

function roleLabel(role) {
  return { beam: "梁", column: "柱", brace: "斜撑" }[role] || role;
}

function renderMembers() {
  const list = $("#member-list");
  list.replaceChildren();
  if (!state.members.length) {
    list.append(textElement("li", "点击画布中的两个点，创建第一根构件。", "empty-row"));
  } else {
    state.members.forEach((member, index) => {
      const item = document.createElement("li");
      item.append(textElement("span", String(index + 1), "member-number"));
      const detail = document.createElement("span");
      detail.className = "member-detail";
      detail.append(
        textElement("strong", `${roleLabel(member.role)} · ${member.length} mm`),
        textElement("small", `${member.start} → ${member.end} · ${member.width}×${member.height} mm`),
      );
      item.append(detail);
      const remove = textElement("button", "删除", "delete-member");
      remove.type = "button";
      remove.dataset.memberIndex = String(index);
      remove.setAttribute("aria-label", `删除第 ${index + 1} 根构件`);
      item.append(remove);
      list.append(item);
    });
  }
  $("#member-count").textContent = String(state.members.length);
  $("#step-2-status").textContent = state.members.length ? `${state.members.length} 根构件` : "尚未连线";
  updateControls();
}

function resetGrid({ ask = true } = {}) {
  if (ask && state.members.length && !window.confirm("调整点阵会清空当前构件，确定继续吗？")) return false;
  state.members = [];
  state.selectedNode = null;
  renderMembers();
  resizeAndDraw();
  return true;
}

function showStep(step) {
  state.currentStep = step;
  for (let index = 1; index <= 3; index += 1) {
    $(`#step-${index}`).hidden = index !== step;
    const button = $(`[data-step-target="${index}"]`);
    button.classList.toggle("active", index === step);
    button.classList.toggle("complete", index < step);
    if (index === step) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  }
  $("#result-section").hidden = true;
  updateControls();
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (step === 2) requestAnimationFrame(resizeAndDraw);
}

function updateControls() {
  const hasMaterials = Boolean(state.inventory && state.materials.length);
  const hasStructure = state.members.length > 0;
  $("#to-step-2").disabled = !hasMaterials;
  $("#to-step-3").disabled = !hasStructure;
  $("#undo-member").disabled = !state.members.length;
  $("#clear-members").disabled = !state.members.length;
  $(`[data-step-target="2"]`).disabled = !hasMaterials;
  $(`[data-step-target="3"]`).disabled = !hasMaterials || !hasStructure;
  $("#run-material-count").textContent = String(state.materials.length);
  $("#run-member-count").textContent = String(state.members.length);
  $("#step-3-status").textContent = hasStructure ? "可以生成方案" : "等待结构";
}

function preferences() {
  return (state.preferenceInterpretation?.preferences || []).map((item) => ({
    preference_type: item.preference_type,
    weight: item.weight,
    scope: "all_members",
    rationale: item.rationale,
  }));
}

function renderPreferenceInterpretation() {
  const panel = $("#model-interpretation");
  panel.replaceChildren();
  if (!state.preferenceInterpretation) {
    panel.hidden = true;
    return;
  }
  panel.append(textElement("p", state.preferenceInterpretation.summary));
  const labels = {
    minimize_cut_waste: "减少余料",
    preserve_scarce_long_stock: "保留长料",
    minimize_not_evaluable: "证据优先",
    minimize_surface_defects: "减少表面节疤",
  };
  const chips = document.createElement("div");
  chips.className = "preference-chips";
  for (const item of state.preferenceInterpretation.preferences) {
    chips.append(textElement("span", `${labels[item.preference_type]} ${item.weight}`, "preference-chip"));
  }
  panel.append(chips);
  panel.hidden = false;
}

async function interpretPreferencePrompt() {
  const prompt = $("#preference-prompt").value.trim();
  if (!prompt) throw new Error("请先输入配料提示词。");
  const { data } = await api(
    `/api/v1/projects/${encodeURIComponent(state.projectId)}/preference-interpretations`,
    { method: "POST", body: { prompt } },
  );
  state.preferenceInterpretation = data;
  renderPreferenceInterpretation();
  return preferences();
}

async function requirementSlots(version, confirmedAt) {
  const grade = $("#minimum-grade").value;
  const slots = [];
  for (const member of state.members) {
    const source = {
      start: member.start, end: member.end, length: member.length,
      width: member.width, height: member.height, grade,
    };
    slots.push({
      project_id: state.projectId,
      slot_requirement_version_id: version,
      slot_id: member.id,
      slot_role: member.role,
      clear_span_mm: member.length,
      end_allowance_mm: 0,
      min_section_width_mm: member.width,
      min_section_height_mm: member.height,
      allow_section_rotation: true,
      minimum_strength_grade: grade,
      capacity_requirement_ref: null,
      orientation_requirement: "any",
      defect_restriction_profile_id: null,
      max_moisture_content_pct: null,
      treatment_restriction_profile_id: null,
      visible_surface_scope: "none",
      aesthetic_requirement_text: null,
      visual_reference_ref: null,
      rule_set_version: "rules-0.1.1",
      requirement_source_ref: `simple-ui:${member.start}:${member.end}`,
      confirmed_by: state.userId,
      confirmed_at: confirmedAt,
      source_row_sha256: await sha256(JSON.stringify(source)),
    });
  }
  return slots;
}

async function runAllocation() {
  if (!state.inventory || !state.members.length) {
    throw new Error("木料或结构尚未完成。");
  }
  setLive("正在调用模型理解提示词…", "neutral");
  const preferenceItems = await interpretPreferencePrompt();
  const requirementId = id("REQ");
  const requirementVersion = id("SLOTS");
  const confirmedAt = new Date().toISOString();
  setLive("正在确认画布中的构件需求…", "neutral");
  const slots = await requirementSlots(requirementVersion, confirmedAt);
  await api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/requirements/confirm`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey("requirement") },
    body: { requirement_id: requirementId, slots, soft_preferences: preferenceItems },
  });
  setLive("正在创建不可变输入快照…", "neutral");
  const taskId = id("TASK");
  await api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/tasks`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey("task") },
    body: {
      allocation_task_id: taskId,
      inventory_version_id: state.inventory.inventory_version_id,
      requirement_id: requirementId,
    },
  });
  setLive("正在执行硬约束匹配和软偏好排序…", "neutral");
  const { data, requestId: correlationId } = await api(
    `/api/v1/projects/${encodeURIComponent(state.projectId)}/tasks/${encodeURIComponent(taskId)}/ranked-plans`,
    {
      method: "POST",
      body: {
        preferences: preferenceItems.map(({ preference_type, weight }) => ({ preference_type, weight })),
        max_plans: 3,
      },
    },
  );
  renderResults(data);
  setLive(`方案预览已生成。Request ID: ${correlationId}`, data.terminal_status === "complete_feasible" ? "success" : "neutral");
}

function metric(label, value) {
  const item = document.createElement("div");
  item.className = "result-metric";
  item.append(textElement("span", label), textElement("strong", value));
  return item;
}

function surfaceKnotSummary(material) {
  const observations = (material?.surface_knots || [])
    .filter((item) => item.scope === "whole")
    .sort((left, right) => left.face.localeCompare(right.face));
  if (!observations.length) return "无整板记录";
  const labels = { wide: "宽面", narrow: "窄面" };
  return observations.map((item) => {
    if (item.knot_type_code === "0") return `${labels[item.face]}无记录节疤`;
    const size = item.size_mm === null || item.size_mm === undefined ? "尺寸未知" : `${item.size_mm} mm`;
    return `${labels[item.face]} ${item.knot_type_code} / ${size}`;
  }).join("；");
}

function renderResults(result) {
  const statusLabels = {
    complete_feasible: ["已找到可验证完整匹配", "success"],
    requires_review: ["已有候选，需补充材料记录", "warning"],
    infeasible: ["当前材料无法覆盖全部构件", "danger"],
  };
  const [label, tone] = statusLabels[result.terminal_status] || [result.terminal_status, "warning"];
  const status = $("#result-status");
  status.textContent = label;
  status.dataset.tone = tone;
  const summary = $("#result-summary");
  summary.replaceChildren(
    metric("构件覆盖", `${result.feasibility.optimistic_matching.matched_count}/${result.feasibility.optimistic_matching.required_slot_count}`),
    metric("可选方案", String(result.plans.length)),
    metric("计算范围", result.graph_scope === "verified" ? "已验证边" : result.graph_scope === "optimistic" ? "含待复核边" : "无完整图"),
  );
  const list = $("#plan-list");
  list.replaceChildren();
  const materialMap = new Map(state.materials.map((item) => [item.material_id, item]));
  const memberMap = new Map(state.members.map((item) => [item.id, item]));
  const showSurfaceKnots = Boolean(
    state.preferenceInterpretation?.preferences.some(
      (item) => item.preference_type === "minimize_surface_defects",
    ),
  );
  if (!result.plans.length) {
    const shortage = result.feasibility.verified_matching.unmatched_slot_ids.length;
    list.append(textElement("div", `没有完整方案。至少有 ${shortage} 根构件在当前材料和证据下无法完成匹配，请补充材料或返回修改结构。`, "empty-result"));
  }
  result.plans.forEach((plan, index) => {
    const card = document.createElement("article");
    card.className = `plan-card${index === 0 ? " best" : ""}`;
    const header = document.createElement("div");
    header.className = "plan-card-header";
    header.append(textElement("h3", index === 0 ? "方案 1 · 推荐" : `方案 ${index + 1}`));
    header.append(textElement("span", `${Math.round(Number(plan.total_score) * 100)} 分`, "plan-score"));
    card.append(header);
    const table = document.createElement("table");
    table.className = "assignment-table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    const columns = ["构件", "分配材料", "长度余量"];
    if (showSurfaceKnots) columns.push("整板节疤记录");
    columns.push("结构评估");
    for (const column of columns) headRow.append(textElement("th", column));
    head.append(headRow);
    table.append(head);
    const body = document.createElement("tbody");
    for (const assignment of plan.assignments) {
      const member = memberMap.get(assignment.slot_id);
      const material = materialMap.get(assignment.material_id);
      const row = document.createElement("tr");
      const cells = [
        textElement("td", `${roleLabel(member?.role)} ${assignment.slot_id}`),
        textElement("td", assignment.material_id),
        textElement("td", material && member ? `${Number(material.length_mm) - member.length} mm` : "—"),
      ];
      if (showSurfaceKnots) cells.push(textElement("td", surfaceKnotSummary(material)));
      cells.push(textElement("td", material?.structural_use_status === "eligible" ? "已有评估" : "缺少评估"));
      row.append(...cells);
      body.append(row);
    }
    table.append(body);
    card.append(table);
    const breakdown = plan.preference_scores.map((item) => {
      const names = { minimize_cut_waste: "省料", preserve_scarce_long_stock: "保护长料", minimize_not_evaluable: "证据", minimize_surface_defects: "少节疤" };
      return `${names[item.preference_type]} ${Math.round(Number(item.score) * 100)}`;
    }).join(" · ");
    card.append(textElement("p", `排序：${breakdown}`, "plan-note"));
    list.append(card);
  });
  $("#result-section").hidden = false;
  $("#result-section").scrollIntoView({ behavior: "smooth", block: "start" });
}

$("#open-connection").addEventListener("click", () => $("#connection-dialog").showModal());
$("#connection-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#connect-service");
  await withBusy(button, "正在连接…", async () => {
    try { await connect(); } catch (error) { handleError(error, "连接"); }
  });
});
$("#clear-connection").addEventListener("click", clearConnection);

$("#material-file").addEventListener("change", async (event) => {
  try { await readMaterialFile(event.target.files[0]); } catch (error) { handleError(error, "材料导入"); }
  event.target.value = "";
});

const dropZone = $("#drop-zone");
for (const eventName of ["dragenter", "dragover"]) dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.add("dragging"); });
for (const eventName of ["dragleave", "drop"]) dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.remove("dragging"); });
dropZone.addEventListener("drop", async (event) => {
  try { await readMaterialFile(event.dataTransfer.files[0]); } catch (error) { handleError(error, "材料导入"); }
});

$("#load-demo-materials").addEventListener("click", async (event) => {
  await withBusy(event.currentTarget, "正在导入…", async () => {
    try { await importMaterials(demoMaterialRows(), "synthetic-demo.json", { demo: true }); } catch (error) { handleError(error, "演示材料导入"); }
  });
});

$("#to-step-2").addEventListener("click", () => showStep(2));
$("#to-step-3").addEventListener("click", () => showStep(3));
$$('[data-back]').forEach((button) => button.addEventListener("click", () => showStep(Number(button.dataset.back))));
$$('[data-step-target]').forEach((button) => button.addEventListener("click", () => showStep(Number(button.dataset.stepTarget))));

const canvas = $("#structure-canvas");
let pointerStart = null;
canvas.addEventListener("pointerdown", (event) => {
  pointerStart = { x: event.clientX, y: event.clientY, yaw: state.view.yaw, pitch: state.view.pitch };
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove", (event) => {
  if (!pointerStart) return;
  const dx = event.clientX - pointerStart.x;
  const dy = event.clientY - pointerStart.y;
  if (Math.hypot(dx, dy) < 4) return;
  state.view.yaw = pointerStart.yaw + dx * 0.008;
  state.view.pitch = Math.max(-1.1, Math.min(1.1, pointerStart.pitch - dy * 0.008));
  resizeAndDraw();
});
canvas.addEventListener("pointerup", (event) => {
  if (!pointerStart) return;
  const distance = Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y);
  if (distance < 5) {
    const rect = canvas.getBoundingClientRect();
    const node = closestNode(event.clientX - rect.left, event.clientY - rect.top);
    if (node) addConnection(node.id);
  }
  pointerStart = null;
});
canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  state.view.zoom = Math.max(0.55, Math.min(2.1, state.view.zoom * (event.deltaY > 0 ? 0.92 : 1.08)));
  resizeAndDraw();
}, { passive: false });

$("#reset-view").addEventListener("click", () => { state.view = { yaw: -0.68, pitch: 0.48, zoom: 1 }; resizeAndDraw(); });
$("#undo-member").addEventListener("click", () => { state.members.pop(); renderMembers(); resizeAndDraw(); });
$("#clear-members").addEventListener("click", () => resetGrid());
$("#member-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-member-index]");
  if (!button) return;
  state.members.splice(Number(button.dataset.memberIndex), 1);
  renderMembers();
  resizeAndDraw();
});
$("#point-controls").addEventListener("click", (event) => {
  const button = event.target.closest("[data-node-id]");
  if (button) addConnection(button.dataset.nodeId);
});
for (const selector of ["#grid-x", "#grid-y", "#grid-z", "#grid-spacing"]) {
  $(selector).addEventListener("change", (event) => {
    const previous = event.target.dataset.previous || event.target.defaultValue;
    if (!resetGrid()) event.target.value = previous;
    event.target.dataset.previous = event.target.value;
  });
}
$("#preference-prompt").addEventListener("input", () => {
  state.preferenceInterpretation = null;
  renderPreferenceInterpretation();
});
$$('[data-prompt]').forEach((button) => button.addEventListener("click", () => {
  $("#preference-prompt").value = button.dataset.prompt;
  state.preferenceInterpretation = null;
  renderPreferenceInterpretation();
}));

$("#run-allocation").addEventListener("click", async (event) => {
  await withBusy(event.currentTarget, "正在生成方案…", async () => {
    try { await runAllocation(); } catch (error) { handleError(error, "方案生成"); }
  });
});
$("#edit-and-rerun").addEventListener("click", () => showStep(3));

window.addEventListener("resize", () => { if (state.currentStep === 2) resizeAndDraw(); });
renderPreferenceInterpretation();
renderMembers();
updateControls();
setTimeout(() => $("#connection-dialog").showModal(), 250);
