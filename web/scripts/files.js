"use strict";
const $ = (id) => document.getElementById(id),
  fragment = window.frameElement?.dataset.fragment || location.hash.slice(1),
  appOrigin = new URL(document.baseURI).origin,
  token = () => fragment.split("?")[0],
  mime = "application/x-termiusplus-items";
let draggedItems = null;
const sides = ["local", "remote"],
  sideName = (side) => (side === "local" ? "左栏" : "右栏");
const node = (tag, text, cls) => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
};
function icon(name, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"),
    use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  if (cls) svg.setAttribute("class", cls);
  return svg;
}
const routePasswords = new Map();
function routePasswordKey(route) {
  return JSON.stringify([
    route.host,
    Number(route.port || 22),
    route.jump || "",
    route.key || "",
  ]);
}
function rememberRoute(route) {
  if (typeof route.password === "string")
    routePasswords.set(routePasswordKey(route), route.password);
  const { password, ...saved } = route;
  return saved;
}
function withRoutePasswords(value) {
  if (Array.isArray(value)) return value.map(withRoutePasswords);
  if (value && typeof value === "object") {
    const copy = Object.fromEntries(
      Object.entries(value).map(([key, child]) => [
        key,
        withRoutePasswords(child),
      ]),
    );
    if (typeof copy.host === "string") {
      const password = routePasswords.get(routePasswordKey(copy));
      if (password !== undefined) copy.password = password;
    }
    return copy;
  }
  return value;
}
let routes = [],
  preferences = {};
try {
  const saved = JSON.parse(localStorage.getItem("termiusplus.routes") || "[]");
  if (Array.isArray(saved)) routes = saved;
} catch {}
try {
  preferences = JSON.parse(localStorage.getItem("termiusplus.panes") || "{}");
} catch {}
try {
  for (const route of JSON.parse(window.frameElement?.dataset.routes || "[]")) {
    const clean = rememberRoute(route);
    if (
      !routes.some(
        (r) =>
          r.host === clean.host &&
          Number(r.port || 22) === Number(clean.port || 22) &&
          (r.jump || "") === (clean.jump || "") &&
          (r.key || "") === (clean.key || ""),
      )
    )
      routes.push(clean);
  }
} catch {}
let terminalSupported = false,
  remoteTasksSupported = false,
  remoteLinks = [];
try {
  const saved = JSON.parse(
    localStorage.getItem("termiusplus.remoteLinks") || "[]",
  );
  if (Array.isArray(saved)) remoteLinks = saved;
} catch {}
let focused = "local",
  active = 0,
  localHome = "",
  rsyncReady = false,
  pending = null,
  toastTimer = null,
  lastJobStates = new Map(),
  staging = false,
  stagingAbort = null,
  stagingInfo = { count: 0, total: 0 },
  routeDialogTarget = "remote",
  editingRoute = null;
const panes = {};
for (const side of sides) {
  const saved = preferences?.[side] || {},
    kind = saved.kind || (side === "local" ? "local" : "remote");
  const connection =
    routes.find((r) => JSON.stringify(r) === saved.connection) ||
    routes[0] ||
    null;
  panes[side] = {
    kind,
    connection,
    root: "",
    selected: new Map(),
    selectionAnchor: null,
    showHidden: saved.showHidden === true,
    paths: saved.paths || {},
    generation: 0,
    entries: [],
    loadedKey: null,
  };
}
function endpointKey(side) {
  const p = panes[side];
  return p.kind === "local" ? "local" : JSON.stringify(p.connection);
}
function endpointLabel(side) {
  const p = panes[side];
  return p.kind === "local" ? "本地" : p.connection?.name || "远程";
}
function savePanePreferences() {
  const saved = {};
  for (const side of sides) {
    const p = panes[side];
    saved[side] = {
      kind: p.kind,
      connection: JSON.stringify(p.connection),
      showHidden: p.showHidden,
      paths: p.paths,
    };
  }
  localStorage.setItem("termiusplus.panes", JSON.stringify(saved));
}
function notice(message, error = false) {
  $("toast").textContent = message;
  $("toast").className = "toast" + (error ? " error" : "");
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(
    () => ($("toast").hidden = true),
    error ? 10000 : 5000,
  );
}
async function api(path, data = {}) {
  const response = await fetch("/api/" + path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + token(),
    },
    body: JSON.stringify(withRoutePasswords(data)),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "请求失败");
  return result;
}
async function startJob(path, options) {
  const job = await api(path, options);
  // Small transfers can finish before the first status request.
  lastJobStates.set(job.id, "queued");
  return job;
}
async function action(button, callback) {
  if (button) button.disabled = true;
  try {
    return await callback();
  } catch (error) {
    notice(error.message, true);
  } finally {
    if (button) button.disabled = false;
  }
}
function focus(side) {
  focused = side;
  for (const other of sides)
    $(other + "Pane").classList.toggle("focused", side === other);
}
function closeMenus() {
  $("fileMenu").hidden = true;
  for (const side of sides) {
    $(side + "Menu").hidden = true;
    $(side + "Location").setAttribute("aria-expanded", "false");
  }
}
function updateHeading(side) {
  const p = panes[side];
  $(side + "Terminal").hidden = false;
  $(side + "Terminal").disabled = p.kind === "remote" && !p.connection;
  $(side + "Terminal").setAttribute(
    "aria-label",
    "打开" + sideName(side) + (p.kind === "local" ? "本地" : "远程") + "终端",
  );
  $(side + "Terminal").title =
    "在此目录打开" + (p.kind === "local" ? "本地" : "远程") + "终端";
  const button = $(side + "Location");
  button.replaceChildren(
    icon(p.kind === "local" ? "computer" : "server"),
    node("strong", p.kind === "local" ? "本地" : "远程"),
    node(
      "span",
      p.kind === "local" ? "这台电脑" : p.connection?.name || "选择连接",
    ),
    icon("down", "location-chevron"),
  );
  button.querySelector("span").id = side + "LocationName";
  button.setAttribute(
    "aria-label",
    "选择" + sideName(side) + "位置：" + endpointLabel(side),
  );
  $(side + "Hidden").setAttribute("aria-pressed", String(p.showHidden));
  $(side + "Hidden").title = p.showHidden ? "不显示隐藏文件" : "显示隐藏文件";
  $(side + "Hidden").setAttribute(
    "aria-label",
    sideName(side) + (p.showHidden ? "不显示隐藏文件" : "显示隐藏文件"),
  );
  updateTransferButtons();
}
function updateTransferButtons() {
  const left = panes.local.kind,
    right = panes.remote.kind;
  for (const [id, from, to] of [
    ["upload", left, right],
    ["download", right, left],
  ]) {
    let text =
      from === "local" && to === "remote"
        ? "上传"
        : from === "remote" && to === "local"
          ? "下载"
          : from === "local"
            ? "复制"
            : "传输";
    let symbol =
      text === "上传" ? "upload" : text === "下载" ? "download" : "transfer";
    $(id).replaceChildren(
      icon(symbol),
      node("span", text + (id === "upload" ? " →" : " ←")),
    );
    $(id).title = id === "upload" ? "左栏 → 右栏" : "右栏 → 左栏";
  }
}
function disconnected(side) {
  const p = panes[side],
    box = node("div", undefined, "empty"),
    image = node("div", undefined, "empty-icon");
  image.append(icon(p.kind === "local" ? "computer" : "server"));
  box.append(
    image,
    node("strong", p.kind === "local" ? "打开本地目录" : "连接你的服务器"),
    node(
      "p",
      p.kind === "local"
        ? "选择目录后，可以向另一栏拖放文件。"
        : "点击上方“远程”选择连接，再浏览和传输文件。",
    ),
  );
  const button = node(
    "button",
    p.connection ? "连接服务器" : "选择位置",
    "control",
  );
  button.onclick = () =>
    p.connection ? action(button, () => load(side)) : openLocationMenu(side);
  box.append(button);
  $(side + "Tree").replaceChildren(box);
  $(side + "Count").textContent = "尚未连接";
}
function openLocationMenu(side) {
  const wasOpen = !$(side + "Menu").hidden;
  closeMenus();
  if (wasOpen) return;
  focus(side);
  renderLocationMenu(side);
  $(side + "Menu").hidden = false;
  $(side + "Location").setAttribute("aria-expanded", "true");
}
function renderLocationMenu(side) {
  const menu = $(side + "Menu");
  menu.replaceChildren();
  const p = panes[side];
  function choice(kind, connection) {
    const button = node(
      "button",
      undefined,
      "location-choice" +
        (p.kind === kind &&
        (kind === "local" ||
          JSON.stringify(p.connection) === JSON.stringify(connection))
          ? " active"
          : ""),
    );
    button.setAttribute("role", "menuitem");
    button.append(icon(kind === "local" ? "computer" : "server"));
    const description = node("div", undefined, "location-description");
    description.append(
      node("strong", kind === "local" ? "本地 · 这台电脑" : connection.name),
      node(
        "small",
        kind === "local"
          ? "浏览本机文件和文件夹"
          : connection.host +
              (connection.jump ? " · 跳板 " + connection.jump : ""),
      ),
    );
    button.append(description);
    if (button.classList.contains("active"))
      button.append(icon("check", "check"));
    button.onclick = () =>
      action(null, () => selectEndpoint(side, kind, connection));
    if (kind === "remote") {
      const row = node("div", undefined, "location-row"),
        edit = node("button", "编辑", "edit-route");
      edit.setAttribute("aria-label", "编辑连接 " + connection.name);
      edit.onclick = () => openRouteEditor(connection, side);
      row.append(button, edit);
      menu.append(row);
    } else menu.append(button);
  }
  choice("local", null);
  if (routes.length) menu.append(node("div", undefined, "location-divider"));
  for (const route of routes) choice("remote", route);
  menu.append(node("div", undefined, "location-divider"));
  const add = node("button", undefined, "location-choice");
  add.setAttribute("role", "menuitem");
  add.append(icon("plus"), node("span", "新增远程连接"));
  add.onclick = () => openRouteEditor(null, side);
  menu.append(add);
}
async function selectEndpoint(side, kind, connection, path) {
  closeMenus();
  focus(side);
  const p = panes[side];
  p.generation++;
  p.kind = kind;
  p.connection = connection;
  p.root = "";
  p.loadedKey = null;
  p.entries = [];
  p.selected.clear();
  $(side + "Path").value =
    path || p.paths[endpointKey(side)] || (kind === "local" ? localHome : "~");
  updateHeading(side);
  updateSelection(side);
  savePanePreferences();
  disconnected(side);
  renderRoutes();
  if (kind === "local" || connection) await load(side);
}
function saveRoutes() {
  localStorage.setItem("termiusplus.routes", JSON.stringify(routes));
  renderRoutes();
  const message = {
    type: "termiusplus:update-routes",
    config: { routes: withRoutePasswords(routes) },
  };
  if (terminalFrame)
    terminalFrame.contentWindow.postMessage(message, appOrigin);
  if (window.frameElement?.dataset.hostView === "terminal")
    parent.postMessage(message, appOrigin);
}
function renderRoutes() {
  for (const side of sides)
    if (!$(side + "Menu").hidden) renderLocationMenu(side);
}
let terminalFrame = null,
  filePageScroll = 0;
function showWorkspace(view) {
  if (!$("fileWorkspace").hidden) filePageScroll = window.scrollY;
  const files = view === "files";
  document.body.classList.remove("terminal-view");
  document.body.classList.toggle("queue-view", !files);
  $("fileTools").hidden = !files;
  if (terminalFrame) terminalFrame.hidden = true;
  $("fileWorkspace").hidden = !files;
  $("queueWorkspace").hidden = files;
  $("filesNav").classList.toggle("active", files);
  $("queueNav").classList.toggle("active", !files);
  $("terminalNav").classList.remove("active");
  requestAnimationFrame(() => {
    if (files) {
      applyLayout();
      window.scrollTo(0, filePageScroll);
    } else window.scrollTo(0, 0);
  });
  if (!files) renderJobs(latestJobs);
}
function showFiles() {
  showWorkspace("files");
}
function showQueue() {
  closeMenus();
  showWorkspace("queue");
}

function terminalConfig(side, workspace = panes) {
  const p = workspace[side],
    remote = p.kind === "remote" && p.connection,
    local = p.kind === "local";
  return {
    route: remote ? withRoutePasswords(p.connection) : undefined,
    local,
    routes: withRoutePasswords(routes),
    path: p.root || (local ? localHome : ""),
    localPath:
      Object.values(workspace).find((p) => p.kind === "local")?.root ||
      localHome,
    filesUrl: appOrigin + "/#" + token(),
    autoConnect: !!(remote || local),
  };
}
async function openTerminal(side = focused, requestConnection = false) {
  closeMenus();
  const config = terminalConfig(side);
  if (window.frameElement?.dataset.hostView === "terminal") {
    parent.postMessage(
      { type: "termiusplus:show-terminal", config, requestConnection },
      appOrigin,
    );
    return;
  }
  if (!$("fileWorkspace").hidden) filePageScroll = window.scrollY;
  document.body.classList.remove("queue-view");
  document.body.classList.add("terminal-view");
  $("fileTools").hidden = true;
  $("fileWorkspace").hidden = true;
  $("queueWorkspace").hidden = true;
  $("queueNav").classList.remove("active");
  $("filesNav").classList.remove("active");
  $("terminalNav").classList.add("active");
  if (terminalFrame) {
    terminalFrame.hidden = false;
    terminalFrame.contentWindow.postMessage(
      { type: "termiusplus:activate-terminal", config, requestConnection },
      appOrigin,
    );
    return;
  }
  terminalFrame = document.createElement("iframe");
  terminalFrame.className = "terminal-frame";
  terminalFrame.title = "终端";
  terminalFrame.dataset.hostView = "files";
  terminalFrame.dataset.fragment =
    token() + "?config=" + encodeURIComponent(JSON.stringify(config));
  document.querySelector(".workspace").append(terminalFrame);
  try {
    const response = await fetch("/terminal");
    if (!response.ok) throw Error("终端页面加载失败");
    terminalFrame.srcdoc = await response.text();
  } catch (error) {
    terminalFrame.remove();
    terminalFrame = null;
    showFiles();
    notice(error.message, true);
  }
}
window.addEventListener("message", (event) => {
  if (event.origin !== appOrigin) return;
  const fromTerminal = event.source === terminalFrame?.contentWindow,
    fromHost = window.frameElement?.dataset.hostView === "terminal" &&
      event.source === parent;
  if (!fromTerminal && !fromHost) return;
  if (event.data?.type === "termiusplus:show-files") showFiles();
  if (event.data?.type === "termiusplus:show-queue") showQueue();
});
$("queueNav").onclick = showQueue;
$("terminalNav").onclick = () => openTerminal();
for (const side of sides)
  $(side + "Terminal").onclick = () => openTerminal(side, true);
for (const side of sides) {
  $(side + "Location").onclick = () => openLocationMenu(side);
  $(side + "Hidden").onclick = () => {
    const p = panes[side];
    p.showHidden = !p.showHidden;
    updateHeading(side);
    applyHiddenVisibility(side);
    savePanePreferences();
  };
}
document.addEventListener("click", (event) => {
  if (!event.target.closest(".location-menu,.location-button")) closeMenus();
});
$("addRoute").onclick = () => openRouteEditor(null, focused);
$("filesNav").onclick = showFiles;
$("settings").onclick = () => $("settingsDialog").showModal();
document
  .querySelectorAll("[data-close]")
  .forEach(
    (button) => (button.onclick = () => $(button.dataset.close).close()),
  );
function openRouteEditor(route = null, side = focused, authHint = "") {
  if ($("routeDialog").open) return;
  closeMenus();
  editingRoute = route;
  routeDialogTarget = side;
  $("routeForm").reset();
  for (const field of ["name", "host", "port", "jump", "key"])
    $(field).value = route?.[field] ?? (field === "port" ? 22 : "");
  $("password").value = route
    ? routePasswords.get(routePasswordKey(route)) || ""
    : "";
  $("routeAuthHint").textContent = authHint;
  $("routeAuthHint").hidden = !authHint;
  $("routeDialogTitle").textContent = route ? "编辑连接" : "新建连接";
  $("deleteRoute").hidden = !route;
  $("routeDialog").showModal();
  if (authHint) $("password").focus();
}
function replaceRoute(old, route) {
  for (const side of sides) {
    const p = panes[side];
    if (
      p.kind === "remote" &&
      JSON.stringify(p.connection) === JSON.stringify(old)
    ) {
      const oldKey = endpointKey(side);
      p.generation++;
      p.connection = route;
      p.root = "";
      p.loadedKey = null;
      p.entries = [];
      p.selected.clear();
      if (
        route &&
        route.host === old.host &&
        Number(route.port) === Number(old.port) &&
        p.paths[oldKey]
      )
        p.paths[endpointKey(side)] = p.paths[oldKey];
      $(side + "Path").value = route ? p.paths[endpointKey(side)] || "~" : "~";
      updateHeading(side);
      updateSelection(side);
      disconnected(side);
    }
  }
  saveRoutes();
  savePanePreferences();
}
$("deleteRoute").onclick = () => {
  if (!editingRoute) return;
  const old = editingRoute;
  routePasswords.delete(routePasswordKey(old));
  routes = routes.filter((r) => r !== old);
  replaceRoute(old, null);
  $("routeDialog").close();
  editingRoute = null;
  notice("连接已删除");
};
$("routeForm").onsubmit = async (event) => {
  event.preventDefault();
  if (!editingRoute && routes.length >= 8) {
    notice("最多支持 8 条线路", true);
    return;
  }
  const route = {
    name: $("name").value.trim() || $("host").value.trim(),
    host: $("host").value.trim(),
    port: Number($("port").value),
    jump: $("jump").value.trim(),
    key: $("key").value.trim(),
  };
  if (!/^(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9_][A-Za-z0-9_.:-]*$/.test(route.host)) {
    notice("主机请填 SSH 配置别名或 user@hostname", true);
    return;
  }
  const old = editingRoute;
  const password = $("password").value;
  if (old && routePasswordKey(old) !== routePasswordKey(route))
    routePasswords.delete(routePasswordKey(old));
  routePasswords.set(routePasswordKey(route), password);
  if (old) {
    const index = routes.indexOf(old);
    if (index < 0) return;
    routes[index] = route;
    replaceRoute(old, route);
  } else {
    routes.push(route);
    saveRoutes();
  }
  $("routeDialog").close();
  editingRoute = null;
  notice("连接已保存");
  await action(null, () => selectEndpoint(routeDialogTarget, "remote", route));
};

async function browse(side, path) {
  const p = panes[side];
  if (p.kind === "remote" && !p.connection) throw Error("请先选择 SSH 连接");
  return api(p.kind, {
    path,
    ...(p.kind === "remote" ? { route: p.connection } : {}),
  });
}
function formatSize(value) {
  if (value < 1024) return value + " B";
  if (value < 1048576) return (value / 1024).toFixed(1) + " KB";
  if (value < 1073741824) return (value / 1048576).toFixed(1) + " MB";
  return (value / 1073741824).toFixed(1) + " GB";
}
const folderClickDelay = 520;
let pendingFolderClick = null;
function cancelFolderClick() {
  if (pendingFolderClick) clearTimeout(pendingFolderClick.timer);
  pendingFolderClick = null;
}
function handleFolderClick(row, onDouble, onTriple) {
  const now = performance.now();
  const previous = pendingFolderClick;
  if (!previous || previous.row !== row || now - previous.time > folderClickDelay) {
    cancelFolderClick();
    pendingFolderClick = {
      row,
      count: 1,
      time: now,
      timer: setTimeout(() => {
        if (pendingFolderClick?.row === row && pendingFolderClick.count === 1)
          pendingFolderClick = null;
      }, folderClickDelay),
    };
    return;
  }
  clearTimeout(previous.timer);
  if (previous.count === 1) {
    pendingFolderClick = {
      row,
      count: 2,
      time: now,
      timer: setTimeout(() => {
        if (pendingFolderClick?.row !== row || pendingFolderClick.count !== 2)
          return;
        pendingFolderClick = null;
        if (row.isConnected) onDouble();
      }, folderClickDelay),
    };
  } else {
    pendingFolderClick = null;
    if (row.isConnected) onTriple();
  }
}
function relative(root, path) {
  if (!path.startsWith(root === "/" ? "/" : root + "/"))
    throw Error("所选文件已不在当前目录，请重新打开目录");
  return path.slice(root === "/" ? 1 : root.length + 1);
}
function updateSelection(side) {
  const p = panes[side];
  $(side + "Selected").textContent = p.selected.size
    ? "已选 " + p.selected.size + " 项"
    : "同机拖放移动 · 跨机拖放传输";
  $(side + "Selected").classList.toggle("selection-text", p.selected.size > 0);
  document.querySelectorAll("#" + side + "Tree .entry").forEach((row) => {
    const chosen = p.selected.has(row.dataset.path);
    row.classList.toggle("selected", chosen);
    row.setAttribute("aria-selected", String(chosen));
  });
}
function applyHiddenVisibility(side) {
  const p = panes[side];
  document
    .querySelectorAll("#" + side + "Tree .entry-wrapper")
    .forEach(
      (wrap) => (wrap.hidden = !p.showHidden && wrap.dataset.hidden === "true"),
    );
  if (!p.showHidden) {
    for (const path of p.selected.keys()) {
      if (
        relative(p.root, path)
          .split("/")
          .some((part) => part.startsWith("."))
      )
        p.selected.delete(path);
    }
  }
  updateSelection(side);
  const visible = p.entries.filter(
      (e) => p.showHidden || !e.name.startsWith("."),
    ).length,
    hidden = p.entries.length - visible;
  $(side + "Count").textContent =
    visible + " 个项目" + (hidden ? " · " + hidden + " 项隐藏" : "");
}
function visibleFileRows(side) {
  const tree = $(side + "Tree");
  return [...tree.querySelectorAll(".entry")].filter(row => {
    for (let parent = row; parent && parent !== tree; parent = parent.parentElement)
      if (parent.hidden) return false;
    return true;
  });
}
function select(side, entry, additive = false, range = false) {
  focus(side);
  const p = panes[side], selected = p.selected;
  if (range && p.selectionAnchor) {
    const rows = visibleFileRows(side),
      start = rows.findIndex(row => row.dataset.path === p.selectionAnchor),
      end = rows.findIndex(row => row.dataset.path === entry.path);
    if (start >= 0 && end >= 0) {
      if (!additive) selected.clear();
      for (const row of rows.slice(Math.min(start, end), Math.max(start, end) + 1))
        selected.set(row.dataset.path, row.entryData);
      updateSelection(side);
      return;
    }
  }
  if (!additive) selected.clear();
  if (additive && selected.has(entry.path)) selected.delete(entry.path);
  else selected.set(entry.path, entry);
  p.selectionAnchor = entry.path;
  updateSelection(side);
}
function entryType(entry) {
  return entry.symlink ? (entry.directory ? "目录链接" : "文件链接")
    : entry.directory ? "文件夹"
      : entry.name.includes(".") ? entry.name.split(".").pop().toUpperCase() : "文件";
}
function drawEntries(container, list, side) {
  for (const entry of list) {
    const wrap = node("div", undefined, "entry-wrapper"),
      row = node("div", undefined, "entry");
    wrap.dataset.hidden = String(entry.name.startsWith("."));
    wrap.hidden = !panes[side].showHidden && entry.name.startsWith(".");
    row.dataset.path = entry.path;
    row.entryData = entry;
    row.draggable = true;
    row.tabIndex = 0;
    row.setAttribute("role", "treeitem");
    row.setAttribute(
      "aria-label",
      entry.name + (entry.directory ? " 文件夹" : " 文件"),
    );
    const name = node("div", undefined, "entry-name");
    let toggle = null;
    if (entry.directory) {
      toggle = node("button", undefined, "toggle");
      toggle.setAttribute("aria-label", "展开 " + entry.name);
      toggle.setAttribute("aria-expanded", "false");
      toggle.append(icon("chevron"));
      name.append(toggle);
    } else name.append(node("span", undefined, "spacer"));
    name.append(
      icon(
        entry.directory ? "folder" : "file",
        entry.directory ? "folder-icon" : "file-icon",
      ),
      node("span", entry.name + (entry.symlink ? " ↗" : "")),
    );
    row.append(
      name,
      node(
        "span",
        entryType(entry),
        "entry-type",
      ),
      node(
        "span",
        entry.directory ? "—" : formatSize(entry.size),
        "entry-size",
      ),
    );
    wrap.append(row);
    container.append(wrap);
    row.updateEntry = (next) => {
      Object.assign(entry, next);
      name.lastElementChild.textContent = entry.name + (entry.symlink ? " ↗" : "");
      row.querySelector(".entry-type").textContent = entryType(entry);
      row.querySelector(".entry-size").textContent = entry.directory ? "—" : formatSize(entry.size);
      wrap.dataset.hidden = String(entry.name.startsWith("."));
      wrap.hidden = !panes[side].showHidden && entry.name.startsWith(".");
      if (panes[side].selected.has(entry.path)) panes[side].selected.set(entry.path, entry);
    };
    row.onclick = (event) => {
      if (event.button !== 0) return;
      if (entry.directory) {
        if (event.detail <= 1)
          select(side, entry, event.metaKey || event.ctrlKey, event.shiftKey);
        if (event.shiftKey || event.metaKey || event.ctrlKey) {
          cancelFolderClick();
          return;
        }
        handleFolderClick(
          row,
          () => action(null, toggleFolder),
          () => action(null, () => load(side, entry.path)),
        );
      } else {
        cancelFolderClick();
        if (event.detail <= 1)
          select(side, entry, event.metaKey || event.ctrlKey, event.shiftKey);
      }
    };
    row.onselectstart = (event) => event.preventDefault();
    row.onmousedown = (event) => {
      if (event.button === 0 && (event.detail > 1 || event.shiftKey)) event.preventDefault();
    };
    row.oncontextmenu = (event) => {
      event.preventDefault();
      event.stopPropagation();
      cancelFolderClick();
      showFileMenu(event, side, entry, toggleFolder);
    };
    row.onkeydown = (event) => {
      if (event.target !== row) return;
      if (event.key === " ") {
        event.preventDefault();
        select(side, entry, event.metaKey || event.ctrlKey, event.shiftKey);
      }
      if (event.key === "Enter" && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        action(null, () =>
          entry.directory ? load(side, entry.path) : previewFile(side, entry),
        );
      }
    };
    row.ondblclick = (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (!entry.directory) action(null, () => previewFile(side, entry));
    };
    let toggleFolder = async () => {};
    row.ondragstart = (event) => {
      if (!panes[side].selected.has(entry.path)) select(side, entry);
      event.dataTransfer.effectAllowed = "copyMove";
      draggedItems = {
        side,
        root: panes[side].root,
        paths: [...panes[side].selected.keys()],
        key: panes[side].loadedKey,
      };
      event.dataTransfer.setData(
        mime,
        JSON.stringify(draggedItems),
      );
    };
    row.ondragend = () => {
      draggedItems = null;
      clearDropHighlights();
    };
    row.ondragover = (event) => {
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = "none";
    };
    row.ondrop = (event) => {
      event.preventDefault();
      event.stopPropagation();
      clearDropHighlights();
      notice("请拖到目标文件夹或列表空白处", true);
    };
    if (entry.directory) {
      const children = node("div", undefined, "children");
      children.hidden = true;
      const generation = panes[side].generation;
      wrap.append(children);
      let loaded = false, opened = false, request = 0;
      row.refreshFolder = async () => {
        const current = ++request, refreshId = panes[side].treeRefreshId;
        const info = await browse(side, entry.path);
        if (!row.isConnected || generation !== panes[side].generation || current !== request || refreshId !== panes[side].treeRefreshId) return;
        reconcileEntries(children, info.entries.map(child => ({
          ...child, path: entry.path.replace(/\/$/, "") + "/" + child.name,
        })), side);
        loaded = true;
      };
      row.invalidateFolder = () => { loaded = false; request++; };
      toggleFolder = async () => {
        if (toggle.disabled) return;
        try {
          if (!loaded) {
            toggle.disabled = true;
            await row.refreshFolder();
            if (!loaded || !row.isConnected || generation !== panes[side].generation) return;
          }
          opened = !opened;
          children.hidden = !opened;
          toggle.replaceChildren(icon(opened ? "down" : "chevron"));
          toggle.setAttribute("aria-expanded", String(opened));
        } catch (error) {
          notice(error.message, true);
        } finally {
          toggle.disabled = false;
        }
      };
      toggle.onclick = (event) => {
        event.stopPropagation();
        if (event.detail <= 1) action(null, toggleFolder);
      };
      row.expandFolder = async () => {
        if (!opened) await toggleFolder();
      };
      row.ondragover = (event) => {
        event.preventDefault();
        event.stopPropagation();
        row.classList.add("dropover");
        event.dataTransfer.dropEffect = sameAccess(draggedItems?.side, side) ? "move" : "copy";
      };
      row.ondragleave = () => row.classList.remove("dropover");
      row.ondrop = (event) => {
        event.preventDefault();
        event.stopPropagation();
        clearDropHighlights();
        action(null, () => drop(event, side, entry.path));
      };
    }
  }
}
function reconcileEntries(container, list, side) {
  const p = panes[side], existing = new Map();
  const remove = (wrap) => {
    for (const row of wrap.querySelectorAll(".entry")) {
      p.selected.delete(row.dataset.path);
      if (p.selectionAnchor === row.dataset.path) p.selectionAnchor = null;
    }
    wrap.remove();
  };
  for (const wrap of [...container.children]) {
    const row = wrap.firstElementChild;
    if (wrap.classList.contains("entry-wrapper")) existing.set(row.dataset.path, wrap);
    else wrap.remove();
  }
  const incoming = new Set(list.map(entry => entry.path));
  for (const [path, wrap] of existing) {
    if (!incoming.has(path)) {
      remove(wrap);
      existing.delete(path);
    }
  }
  let cursor = container.firstElementChild;
  for (const entry of list) {
    let wrap = existing.get(entry.path);
    existing.delete(entry.path);
    if (wrap && wrap.firstElementChild.entryData.directory !== entry.directory) {
      if (cursor === wrap) cursor = wrap.nextElementSibling;
      remove(wrap);
      wrap = null;
    }
    if (wrap) wrap.firstElementChild.updateEntry(entry);
    else {
      const temporary = node("div");
      drawEntries(temporary, [entry], side);
      wrap = temporary.firstElementChild;
    }
    if (wrap === cursor) cursor = cursor.nextElementSibling;
    else container.insertBefore(wrap, cursor);
  }
  for (const wrap of existing.values()) remove(wrap);
  if (!list.length && container === $(side + "Tree")) {
    const box = node("div", undefined, "empty");
    box.append(icon("folder"), node("strong", "文件夹为空"));
    container.append(box);
  }
}
let pendingFileAction = null;
function showFileMenu(event, side, entry, toggleFolder) {
  closeMenus();
  if (entry && event.shiftKey) select(side, entry, false, true);
  else if (entry && !panes[side].selected.has(entry.path)) select(side, entry);
  else focus(side);
  const menu = $("fileMenu");
  menu.replaceChildren();
  const add = (label, callback, danger = false) => {
    const button = node("button", label, danger ? "danger" : "");
    button.setAttribute("role", "menuitem");
    button.onclick = () => {
      closeMenus();
      action(null, callback);
    };
    menu.append(button);
  };
  const directory = entry?.directory
    ? entry.path
    : entry
      ? entry.path.slice(0, entry.path.lastIndexOf("/")) || "/"
      : panes[side].root;
  const parent = {path: directory, name: ""};
  add("新建文件夹…", () => openFileAction(side, parent, "create_folder"));
  add("新建文件…", () => openFileAction(side, parent, "create_file"));
  if (entry?.directory) {
    add("展开 / 收起", toggleFolder);
    add("进入文件夹", () => load(side, entry.path));
  } else if (entry) add("预览", () => previewFile(side, entry));
  add("复制路径", async () => {
    await navigator.clipboard.writeText(entry ? entry.path : directory);
    notice("已复制路径");
  });
  if (entry) {
    add("打包", packSelection);
    const count = panes[side].selected.size;
    if (count <= 1) add("重命名", () => openFileAction(side, entry, "rename"));
    add(count > 1 ? "删除 " + count + " 项…" : "删除…", () => openFileAction(side, entry, "trash"), true);
  }
  menu.hidden = false;
  menu.style.left =
    Math.max(4, Math.min(event.clientX, innerWidth - menu.offsetWidth - 4)) +
    "px";
  menu.style.top =
    Math.max(4, Math.min(event.clientY, innerHeight - menu.offsetHeight - 4)) +
    "px";
  menu.querySelector("button").focus();
}
function openFileAction(side, entry, operation) {
  const p = panes[side];
  const creating = operation === "create_folder" || operation === "create_file";
  const entries = operation === "trash" && p.selected.has(entry.path)
    ? [...p.selected.values()].map(item => ({...item})) : [entry];
  pendingFileAction = {
    side,
    entry,
    entries,
    operation,
    key: endpointKey(side),
    root: p.root,
    kind: p.kind,
    route: p.connection,
  };
  $("fileActionTitle").textContent =
    creating ? (operation === "create_folder" ? "新建文件夹" : "新建文件")
      : operation === "rename" ? "重命名" : "删除 " + entries.length + " 项";
  $("fileActionPath").textContent = operation === "trash" ? entries.map(item => item.path).join("\n") : entry.path;
  $("renameField").hidden = operation === "trash";
  $("newFilename").required = operation !== "trash";
  $("newFilename").value = creating ? "" : entry.name;
  $("deleteNote").hidden = operation !== "trash";
  $("confirmFileAction").textContent =
    creating ? "创建" : operation === "rename" ? "保存" : "移入回收文件夹";
  $("fileActionDialog").showModal();
  if (operation !== "trash") {
    $("newFilename").focus();
    $("newFilename").select();
  }
}
async function refreshFileTree(side, revealDirectory) {
  const p = panes[side], tree = $(side + "Tree"), root = p.root,
    generation = p.generation, key = endpointKey(side),
    request = p.treeRefreshId = (p.treeRefreshId || 0) + 1;
  if (!root || p.loadedKey !== key) return;
  const valid = () => generation === p.generation && key === endpointKey(side) && request === p.treeRefreshId;
  const scrollTop = tree.scrollTop, bounds = tree.getBoundingClientRect();
  const anchors = visibleFileRows(side).map(row => ({row, rect: row.getBoundingClientRect()}))
    .filter(item => item.rect.bottom > bounds.top && item.rect.top < bounds.bottom);
  const info = await browse(side, root);
  if (!valid()) return;
  p.entries = info.entries;
  reconcileEntries(tree, info.entries, side);
  const expanded = new Set();
  for (const row of tree.querySelectorAll(".entry")) {
    if (row.querySelector('[aria-expanded="true"]')) expanded.add(row.dataset.path);
    else row.invalidateFolder?.();
  }
  if (revealDirectory?.startsWith(root.replace(/\/$/, "") + "/")) {
    let path = revealDirectory;
    while (path !== root) {
      expanded.add(path);
      path = path.slice(0, path.lastIndexOf("/")) || "/";
    }
  }
  try {
    for (const path of [...expanded].sort((a, b) => a.length - b.length)) {
      if (!valid()) return;
      const row = [...tree.querySelectorAll('.entry')].find(row => row.dataset.path === path);
      if (row?.refreshFolder) await row.refreshFolder();
      if (row?.expandFolder) await row.expandFolder();
    }
  } finally {
    if (valid()) {
      applyHiddenVisibility(side);
      tree.scrollTop = scrollTop;
      const anchor = anchors.find(item => item.row.isConnected);
      if (anchor) tree.scrollTop += anchor.row.getBoundingClientRect().top - anchor.rect.top;
    }
  }
}
$("fileActionForm").onsubmit = (event) => {
  event.preventDefault();
  action($("confirmFileAction"), async () => {
    const task = pendingFileAction;
    if (!task) return;
    const p = panes[task.side];
    if (endpointKey(task.side) !== task.key || p.root !== task.root)
      throw Error("位置已变化，请重新选择项目");
    const result = await api("files", {
      side: task.kind,
      route: task.route,
      root: task.root,
      path: task.entry.path,
      operation: task.operation,
      name: $("newFilename").value,
      paths: task.operation === "trash" ? task.entries.map(item => item.path) : undefined,
    });
    $("fileActionDialog").close();
    pendingFileAction = null;
    notice(
      task.operation === "rename"
        ? "已重命名"
        : task.operation === "create_folder" ? "已创建文件夹"
        : task.operation === "create_file" ? "已创建文件"
        : "已移入回收文件夹 " + result.trashed.length + " 项",
    );
    for (const side of sides) {
      const q = panes[side];
      if (
        q.root &&
        q.kind === task.kind &&
        (q.kind === "local" ||
          JSON.stringify(q.connection) === JSON.stringify(task.route))
      )
        await refreshFileTree(side, task.operation.startsWith("create_") ? task.entry.path : null);
    }
    if (result.error) throw Error("已处理 " + result.trashed.length + " 项，其余未删除：" + result.error);
  });
};
document.addEventListener("pointerdown", (event) => {
  if (!$("fileMenu").contains(event.target)) $("fileMenu").hidden = true;
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") $("fileMenu").hidden = true;
});
window.addEventListener(
  "scroll",
  () => {
    $("fileMenu").hidden = true;
  },
  true,
);
async function load(side, path, background = false) {
  if (!background) focus(side);
  const p = panes[side],
    generation = ++p.generation,
    key = endpointKey(side);
  let info;
  try {
    info = await browse(
      side,
      path === undefined ? $(side + "Path").value : path,
    );
  } catch (error) {
    if (generation !== p.generation || key !== endpointKey(side)) return;
    if (
      p.kind === "remote" &&
      p.connection &&
      (/Permission denied \([^)]*password[^)]*\)/i.test(error.message) ||
        /SSH 连接或远程目录读取超时/i.test(error.message))
    ) {
      const hasPassword = Boolean(
        routePasswords.get(routePasswordKey(p.connection)),
      );
      const timedOut = /超时/i.test(error.message);
      openRouteEditor(
        p.connection,
        side,
        timedOut
          ? "SSH 登录超时。请检查网络和服务器登录提示；如使用密码，请重新输入并保存后重试。"
          : hasPassword
            ? "服务器拒绝了本次 SSH 认证。请核对用户名和密码，或改用已登记的 SSH 密钥。"
            : "此服务器要求 SSH 密码。请在这里输入密码并保存，工具会立即重试连接。",
      );
      return;
    }
    throw error;
  }
  if (generation !== p.generation || key !== endpointKey(side)) return;
  $(side + "Path").value = info.path;
  p.root = info.path;
  p.machine = info.machine;
  p.account = info.account;
  p.loadedKey = key;
  p.paths[key] = info.path;
  p.entries = info.entries;
  p.selected.clear();
  p.selectionAnchor = null;
  p.treeRefreshId = (p.treeRefreshId || 0) + 1;
  $(side + "Tree").replaceChildren();
  drawEntries($(side + "Tree"), info.entries, side);
  applyHiddenVisibility(side);
  savePanePreferences();
  if (!info.entries.length) {
    const box = node("div", undefined, "empty");
    box.append(
      icon("folder"),
      node("strong", "文件夹为空"),
      node("p", "把另一栏的文件拖到这里开始传输"),
    );
    $(side + "Tree").append(box);
  }
  const connected = sides.filter(
    (s) => panes[s].kind === "remote" && panes[s].root,
  );
  $("status").textContent = connected.length
    ? connected.map((s) => endpointLabel(s)).join(" · ")
    : "本地工作空间";
  renderRoutes();
}
for (const side of sides) {
  $(side + "Pane").onpointerdown = () => focus(side);
  $(side + "Tree").oncontextmenu = (event) => {
    event.preventDefault();
    cancelFolderClick();
    if (panes[side].root && panes[side].loadedKey === endpointKey(side))
      showFileMenu(event, side, null);
  };
  $("load" + (side === "local" ? "Local" : "Remote")).onclick = () =>
    action($("load" + (side === "local" ? "Local" : "Remote")), () =>
      load(side),
    );
  $(side + "Refresh").onclick = () =>
    action($(side + "Refresh"), () =>
      panes[side].root && panes[side].loadedKey === endpointKey(side)
        ? refreshFileTree(side) : load(side, $(side + "Path").value),
    );
  $(side + "Path").onkeydown = (event) => {
    if (event.key === "Enter") action(null, () => load(side));
  };
  $(side + "Up").onclick = () =>
    action($(side + "Up"), () => {
      const root = panes[side].root || $(side + "Path").value,
        parent = root === "~" ? "~" : root.replace(/\/[^/]+\/?$/, "") || "/";
      return load(side, parent);
    });
  $(side + "Pane").ondragover = (event) => {
    event.preventDefault();
    $(side + "Pane").classList.add("dropover");
    event.dataTransfer.dropEffect = sameAccess(draggedItems?.side, side) ? "move" : "copy";
  };
  $(side + "Pane").ondragleave = (event) => {
    if (!$(side + "Pane").contains(event.relatedTarget))
      $(side + "Pane").classList.remove("dropover");
  };
  $(side + "Pane").ondrop = (event) => {
    event.preventDefault();
    clearDropHighlights();
    action(null, () => drop(event, side, panes[side].root));
  };
}
function clearDropHighlights() {
  document
    .querySelectorAll(".dropover")
    .forEach((e) => e.classList.remove("dropover"));
}
function routeGroup(pane) {
  if (!pane.connection) throw Error("请先选择远程连接");
  return [
    pane.connection,
    ...routes.filter(
      (r) => JSON.stringify(r) !== JSON.stringify(pane.connection),
    ),
  ];
}
function createTransfer(sourceSide, targetSide, sourceRoot, targetRoot, paths) {
  const source = sourceSide ? panes[sourceSide] : { kind: "local" },
    target = panes[targetSide],
    options = {
      auto: $("auto").checked,
      threshold: Number($("threshold").value),
      sourcePane: sourceSide,
      targetPane: targetSide,
      items: paths?.map((path) => relative(sourceRoot, path)),
      flattenItems: Boolean(paths?.length),
      sourceLabel:
        (sourceSide ? endpointLabel(sourceSide) : "Finder") + "：" + sourceRoot,
      targetLabel: endpointLabel(targetSide) + "：" + targetRoot,
    };
  if (source.kind === "local" && target.kind === "remote")
    Object.assign(options, {
      direction: "upload",
      routes: routeGroup(target),
      local: sourceRoot,
      remote: targetRoot,
    });
  else if (source.kind === "remote" && target.kind === "local")
    Object.assign(options, {
      direction: "download",
      routes: routeGroup(source),
      local: targetRoot,
      remote: sourceRoot,
    });
  else if (source.kind === "local")
    Object.assign(options, {
      direction: "copy",
      routes: [],
      local: sourceRoot,
      destination: targetRoot,
    });
  else
    Object.assign(options, {
      direction: "relay",
      crossAccount: sameMachine(sourceSide, targetSide) && !sameAccess(sourceSide, targetSide),
      routes: [],
      source: { kind: "remote", path: sourceRoot, routes: routeGroup(source) },
      destination: {
        kind: "remote",
        path: targetRoot,
        routes: routeGroup(target),
      },
    });
  return options;
}
function showTransfer(options, names, external = false) {
  if (!rsyncReady) throw Error("需要 rsync 3.x，请先安装");
  pending = options;
  const between =
    options.direction === "relay" || options.direction === "remote";
  $("remoteTaskOptions").hidden = !between;
  if (between) {
    const passwordLogin = [options.source, options.destination].some(
      (endpoint) =>
        endpoint?.routes?.some((route) =>
          routePasswords.get(routePasswordKey(route)),
        ),
    );
    $("remoteMode").value = options._resumeId
      ? "relay"
      : options.direction === "remote"
        ? "remote"
        : passwordLogin || options.crossAccount
          ? "relay"
          : "remote";
    $("remoteMode").disabled = !!options._resumeId;
    $("remoteExecutor").value = options.executor || "destination";
    updateRemoteSettings();
  }
  $("transferTitle").textContent = {
    upload: "上传到服务器",
    download: "下载到本地",
    copy: "复制到本地目录",
    relay: "远程到远程传输",
    remote: "远程独立会话",
  }[options.direction];
  $("confirmPack").checked = $("packFirst").checked;
  $("transferSummary").replaceChildren();
  const count = node("div");
  count.append(
    node(
      "strong",
      names.length ? names.length + " 个选中项目" : "当前目录的全部内容",
    ),
  );
  $("transferSummary").append(count);
  if (names.length)
    $("transferSummary").append(
      node(
        "div",
        names.slice(0, 5).join(" · ") + (names.length > 5 ? " …" : ""),
      ),
    );
  $("transferSummary").append(
    node("div", "源：" + options.sourceLabel),
    node("div", "目标：" + options.targetLabel),
  );
  $("stageNote").textContent =
    options.direction === "relay"
      ? "先下载到本机临时目录，再上传到目标。需要本地磁盘空间和两次传输带宽。"
      : external
        ? "外部拖放会先暂存到本机，再用 rsync 传输；需要额外磁盘空间。"
        : options.items
          ? "选中项目直接放入目标目录；文件夹保留名称和内部结构，不包含上级目录。"
          : "合并整个源目录的内容，包括隐藏文件；显示开关只影响浏览。";
  if (between) updateRemoteSettings();
  $("transferDialog").showModal();
}
function transferSelected(sourceSide) {
  const targetSide = sourceSide === "local" ? "remote" : "local";
  if (!panes[sourceSide].root) throw Error("请先打开源目录");
  if (!panes[targetSide].root) throw Error("请先打开目标目录");
  const entries = [...panes[sourceSide].selected.values()];
  showTransfer(
    createTransfer(
      sourceSide,
      targetSide,
      panes[sourceSide].root,
      panes[targetSide].root,
      entries.length ? entries.map((e) => e.path) : undefined,
    ),
    entries.map((e) => e.name),
  );
}
$("upload").onclick = () =>
  action($("upload"), () => transferSelected("local"));
$("download").onclick = () =>
  action($("download"), () => transferSelected("remote"));
function sameMachine(source, target) {
  if (!source || !panes[source] || !panes[target]) return false;
  const p = panes[source], q = panes[target];
  if (p.loadedKey !== endpointKey(source) || q.loadedKey !== endpointKey(target)) return false;
  return source === target ||
    (p.kind === "local" && q.kind === "local") ||
    Boolean(p.machine && p.machine === q.machine);
}
function sameAccess(source, target) {
  if (!sameMachine(source, target)) return false;
  const p = panes[source], q = panes[target];
  return source === target ||
    (p.kind === "local" && q.kind === "local") ||
    Boolean(p.account && p.account === q.account);
}
async function moveSelection(payload, target, destination) {
  const source = panes[payload.side];
  const affected = sides.filter(side => sameMachine(payload.side, side))
    .map(side => ({side, key: endpointKey(side)}));
  const result = await api("files", {
    side: source.kind,
    route: source.connection,
    root: payload.root,
    path: payload.root,
    operation: "move",
    paths: payload.paths,
    destination,
  });
  // Even a partial failure must refresh the entries that were already moved.
  if (result.moved?.length) {
    for (const {side, key} of affected) {
      if (endpointKey(side) === key)
        await refreshFileTree(side, destination);
    }
  }
  if (result.error) throw Error("已移动 " + result.moved.length + " 项；其余未移动：" + result.error);
  notice(result.moved.length ? "已移动 " + result.moved.length + " 项" : "项目已在目标文件夹中");
}
async function drop(event, target, targetRoot) {
  if (!targetRoot) throw Error("请先打开目标目录");
  const internal = event.dataTransfer.getData(mime);
  if (internal) {
    const payload = JSON.parse(internal);
    if (
      !panes[payload.side] || !Array.isArray(payload.paths) ||
      payload.key !== endpointKey(payload.side) ||
      payload.key !== panes[payload.side].loadedKey ||
      payload.root !== panes[payload.side].root
    )
      throw Error("源位置已变化，请重新选择文件");
    if (panes[target].loadedKey !== endpointKey(target))
      throw Error("目标位置已变化，请先刷新目录");
    if (sameAccess(payload.side, target)) {
      await moveSelection(payload, target, targetRoot);
      return;
    }
    showTransfer(
      createTransfer(
        payload.side,
        target,
        payload.root,
        targetRoot,
        payload.paths,
      ),
      payload.paths.map((path) => path.split("/").pop()),
    );
    return;
  }
  if (staging) throw Error("正在暂存上一批文件，请稍后");
  const items = Array.from(event.dataTransfer.items).filter(
      (i) => i.kind === "file",
    ),
    entries = items.map((item) => item.webkitGetAsEntry?.()).filter(Boolean),
    files = Array.from(event.dataTransfer.files);
  if (!entries.length && !files.length) throw Error("没有可传输的文件");
  const names = entries.length
      ? entries.map((e) => e.name)
      : files.map((f) => f.name),
    options = createTransfer(null, target, "", targetRoot, undefined);
  options.external = { entries, files };
  showTransfer(options, names, true);
}
async function stageExternal(external) {
  staging = true;
  stagingAbort = new AbortController();
  stagingInfo = { count: 0, total: 0 };
  let stage = null,
    count = 0,
    total = 0;
  async function chunk(path, file) {
    const size = 4 * 1024 * 1024;
    for (let offset = 0; offset < file.size || offset === 0; offset += size) {
      const query = new URLSearchParams({
          id: stage.id,
          path,
          offset: String(offset),
        }),
        response = await fetch("/api/stage/chunk?" + query, {
          method: "POST",
          headers: { Authorization: "Bearer " + token() },
          signal: stagingAbort.signal,
          body: file.slice(offset, Math.min(offset + size, file.size)),
        }),
        data = await response.json();
      if (!response.ok) throw Error(data.error);
      total += Math.min(size, file.size - offset);
      stagingInfo = { count, total };
      $("status").textContent =
        "暂存 " + count + " 个文件 · " + formatSize(total);
    }
  }
  async function walk(entry, parent = "") {
    if (stagingAbort.signal.aborted) throw Error("暂存已取消");
    const path = parent + entry.name;
    if (entry.isFile) {
      const file = await new Promise((resolve, reject) =>
        entry.file(resolve, reject),
      );
      count++;
      await chunk(path, file);
    } else if (entry.isDirectory) {
      const query = new URLSearchParams({ id: stage.id, path, directory: "1" }),
        response = await fetch("/api/stage/chunk?" + query, {
          method: "POST",
          headers: { Authorization: "Bearer " + token() },
          signal: stagingAbort.signal,
          body: new Blob([]),
        });
      if (!response.ok) throw Error((await response.json()).error);
      const reader = entry.createReader();
      while (true) {
        const batch = await new Promise((resolve, reject) =>
          reader.readEntries(resolve, reject),
        );
        if (!batch.length) break;
        for (const child of batch) await walk(child, path + "/");
      }
    }
  }
  try {
    stage = await api("stage/create");
    if (stagingAbort.signal.aborted) throw Error("暂存已取消");
    if (external.entries.length) {
      for (const entry of external.entries) await walk(entry);
    } else {
      for (const file of external.files) {
        count++;
        await chunk(file.name, file);
      }
    }
    const result = await api("stage/seal", { id: stage.id });
    return { ...result, id: stage.id };
  } catch (error) {
    if (stage) await api("stage/release", { id: stage.id }).catch(() => {});
    throw error;
  } finally {
    staging = false;
    stagingAbort = null;
  }
}
function updateRemoteSettings() {
  const direct = $("remoteMode").value === "remote";
  $("directSettings").hidden = !direct;
  if (pending && direct) {
    const executor = pending[$("remoteExecutor").value].routes[0],
      peer =
        pending[
          $("remoteExecutor").value === "destination" ? "source" : "destination"
        ].routes[0];
    const saved = remoteLinks.find(
      (link) =>
        link.executorHost === executor.host &&
        Number(link.executorPort) === Number(executor.port || 22) &&
        link.peerHost === peer.host &&
        Number(link.peerPort) === Number(peer.port || 22),
    );
    const target = pending.remoteTarget || saved?.target || peer;
    $("remotePeer").value = target.host;
    $("remotePeerPort").value = target.port || 22;
    $("remotePeerKey").value =
      pending.remoteTarget?.key || saved?.target?.key || "";
    $("remotePeerLabel").textContent =
      $("remoteExecutor").value === "destination"
        ? "B 登录 A 的 SSH 地址"
        : "A 登录 B 的 SSH 地址";
  }
  $("stageNote").textContent = direct
    ? pending?.crossAccount
      ? "同机跨账号直传需要执行账号能独立通过 SSH 密钥登录另一账号；否则请选择本机中转。远程会话启动后，Mac 断联也会继续。"
      : "数据直接在 A 和 B 之间传输。Mac 断网、合盖或退出本机服务后，远程会话继续；重开工具会重新读取进度。"
    : pending?.crossAccount
      ? "同机不同账号：用源账号读取、目标账号写入，经本机中转。无需互相访问目录；需要本地磁盘空间并保持 Mac 在线。"
      : "先下载到 Mac 再上传；Mac 必须在线。旧记录续传使用原中转数据。";
}
function saveRemoteSettings() {
  if (!pending || $("remoteMode").value !== "remote") return;
  const executor = pending[$("remoteExecutor").value].routes[0],
    peer =
      pending[
        $("remoteExecutor").value === "destination" ? "source" : "destination"
      ].routes[0];
  const link = {
    executorHost: executor.host,
    executorPort: Number(executor.port || 22),
    peerHost: peer.host,
    peerPort: Number(peer.port || 22),
    target: {
      host: $("remotePeer").value.trim(),
      port: Number($("remotePeerPort").value),
      key: $("remotePeerKey").value.trim(),
    },
  };
  remoteLinks = remoteLinks.filter(
    (x) =>
      !(
        x.executorHost === link.executorHost &&
        Number(x.executorPort) === link.executorPort &&
        x.peerHost === link.peerHost &&
        Number(x.peerPort) === link.peerPort
      ),
  );
  remoteLinks.unshift(link);
  localStorage.setItem("termiusplus.remoteLinks", JSON.stringify(remoteLinks));
}
for (const id of ["remotePeer", "remotePeerPort", "remotePeerKey"])
  $(id).onchange = saveRemoteSettings;
$("remoteMode").onchange = updateRemoteSettings;
$("remoteExecutor").onchange = updateRemoteSettings;
$("confirmTransfer").onclick = () =>
  action($("confirmTransfer"), async () => {
    const options = pending;
    if (!options) return;
    if (options.direction === "relay" || options.direction === "remote") {
      options.direction = $("remoteMode").value;
      if (options.direction === "remote") {
        saveRemoteSettings();
        options.executor = $("remoteExecutor").value;
        options.remoteTarget = {
          host: $("remotePeer").value.trim(),
          port: Number($("remotePeerPort").value),
          key: $("remotePeerKey").value.trim(),
        };
        if (!options.remoteTarget.host)
          throw Error("请填写执行端独立登录另一台服务器的 SSH 地址");
        if (!remoteTasksSupported) {
          window.open(
            "http://127.0.0.1:8768/#" +
              token() +
              "?direct=" +
              encodeURIComponent(JSON.stringify(options)),
            "_blank",
            "noopener",
          );
          $("transferDialog").close();
          notice(
            "已打开远程会话页面，请核对后开始；当前中转任务请先完成或取消",
          );
          return;
        }
      }
    }
    const pack = $("confirmPack").checked;
    if (pack && !options.items && !options.external)
      throw Error("请先选择文件或文件夹，再使用先打包再传输");
    if (options.external) {
      $("transferDialog").close();
      notice("正在暂存拖入的文件，请保持页面打开");
      const staged = await stageExternal(options.external);
      options.local = staged.path;
      options.items = staged.items;
      options.stage = staged.id;
      delete options.external;
    }
    options.pack = pack;
    await startJob("start", options);
    $("transferDialog").close();
    pending = null;
    notice(pack ? "任务已创建，正在打包后传输" : "已加入传输队列");
    await refresh();
  });
async function packSelection() {
  const p = panes[focused],
    entries = [...p.selected.values()];
  if (!entries.length) throw Error("请先选中文件或文件夹，然后打包");
  if (p.loadedKey !== endpointKey(focused))
    throw Error("位置已变化，请重新打开目录");
  await startJob("pack", {
    side: p.kind,
    sourcePane: focused,
    targetPane: focused,
    routes: p.kind === "remote" ? [p.connection] : [],
    local: p.kind === "local" ? p.root : "",
    remote: p.kind === "remote" ? p.root : "",
    items: entries.map((e) => relative(p.root, e.path)),
  });
  notice("正在生成 .tar.gz，完成后出现在原目录");
  await refresh();
}
let previewGeneration = 0;
$("previewDialog").addEventListener("close", () => {
  previewGeneration++;
  $("previewContent").replaceChildren();
});
async function previewFile(side, entry) {
  focus(side);
  const p = panes[side];
  if (entry.directory) throw Error("请选择文件进行预览");
  if (p.loadedKey !== endpointKey(side))
    throw Error("位置已变化，请重新打开目录");
  const generation = ++previewGeneration;
  closeMenus();
  $("previewTitle").textContent = entry.name;
  $("previewMeta").textContent = endpointLabel(side) + " · " + entry.path;
  $("previewContent").replaceChildren(
    node("p", "正在读取文件…", "preview-message"),
  );
  $("previewHint").textContent = "只读预览";
  if (!$("previewDialog").open) $("previewDialog").showModal();
  try {
    const result = await api("preview", {
      side: p.kind,
      path: entry.path,
      ...(p.kind === "remote" ? { route: p.connection } : {}),
    });
    if (generation !== previewGeneration) return;
    $("previewMeta").textContent =
      endpointLabel(side) +
      " · " +
      result.path +
      " · " +
      formatSize(result.size);
    $("previewContent").replaceChildren();
    if (result.kind === "text") {
      $("previewContent").append(node("pre", result.content));
      $("previewHint").textContent = result.truncated
        ? "仅显示前 256 KiB，文件内容未修改。"
        : "只读预览 · " + result.encoding;
    } else if (result.kind === "image") {
      const img = node("img");
      img.alt = result.name;
      img.src = "data:" + result.mime + ";base64," + result.content;
      img.onerror = () => {
        $("previewContent").replaceChildren(
          node("p", "无法显示该图片，可能文件已损坏。", "preview-message"),
        );
      };
      $("previewContent").append(img);
      $("previewHint").textContent = "只读图片预览";
    } else
      $("previewContent").append(node("p", result.message, "preview-message"));
  } catch (error) {
    if (generation !== previewGeneration) return;
    $("previewContent").replaceChildren(
      node("p", error.message, "preview-message"),
    );
    $("previewHint").textContent = "读取失败，请检查文件权限或连接";
  }
}
function previewSelection() {
  const entries = [...panes[focused].selected.values()];
  if (entries.length !== 1 || entries[0].directory)
    throw Error("请选中一个文件进行预览");
  return previewFile(focused, entries[0]);
}
$("preview").onclick = () => action($("preview"), previewSelection);
$("pack").onclick = () => action($("pack"), packSelection);
document.addEventListener("keydown", (event) => {
  if ($("fileWorkspace").hidden) return;
  if (event.key === "Escape") {
    closeMenus();
    if (!document.querySelector("dialog[open]")) {
      panes[focused].selected.clear();
      updateSelection(focused);
    }
  }
  if (
    event.target.matches("input,textarea,select") ||
    document.querySelector("dialog[open]")
  )
    return;
  if (
    event.altKey &&
    (event.code === "KeyP" || event.key.toLowerCase() === "p")
  ) {
    event.preventDefault();
    action($("preview"), previewSelection);
  } else if (
    (event.metaKey || event.ctrlKey) &&
    event.shiftKey &&
    event.key.toLowerCase() === "p"
  ) {
    event.preventDefault();
    action($("pack"), packSelection);
  } else if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    action(null, () => transferSelected(focused));
  } else if (
    (event.metaKey || event.ctrlKey) &&
    event.key.toLowerCase() === "r"
  ) {
    event.preventDefault();
    action(null, () => load(focused, panes[focused].root));
  }
});
const states = {
  retrying: "重连中",
  queued: "等待中",
  checking: "核对线路",
  packing: "打包中",
  transferring: "传输中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  remote_running: "远程运行 / 待确认",
  interrupted: "已中断",
};
function transferMetrics(job) {
  const relay = job.direction === "relay",
    lastPhase = [...job.log]
      .reverse()
      .find((line) => /(下载|上传):/.test(line)),
    upload = job.stage
      ? job.stage === "upload"
      : job.route?.startsWith("上传 · ") ||
        (!job.route?.startsWith("下载 · ") &&
          (job.state === "completed" || lastPhase?.includes("上传:")));
  let stageProgress =
      job.stageProgress ??
      (relay
        ? Math.max(0, (job.progress - (upload ? 50 : 0)) * 2)
        : job.progress),
    speed = job.speed || 0,
    bytes = job.transferredBytes,
    eta = job.eta || "";
  if (job.stageProgress === undefined || job.legacyResume) {
    for (const line of [...job.log].reverse()) {
      if (relay && !(upload ? line.includes("上传:") : line.includes("下载:")))
        continue;
      const m = line.match(
        /(?:^|\s)([\d,]+)\s+(\d{1,3})%\s+([\d.,]+)\s*([kKMGT]?)B\/s\s+(\S+)/,
      );
      if (m) {
        stageProgress = Math.min(100, Number(m[2]));
        speed =
          Number(m[3].replaceAll(",", "")) *
          1024 ** { "": 0, K: 1, M: 2, G: 3, T: 4 }[m[4].toUpperCase()];
        bytes = Number(m[1].replaceAll(",", ""));
        eta = m[5];
        break;
      }
    }
  }
  if (job.state === "completed") stageProgress = 100;
  const total =
    job.state === "completed"
      ? 100
      : relay
        ? (upload ? 50 : 0) + stageProgress / 2
        : stageProgress;
  return { relay, upload, stageProgress, speed, bytes, eta, total };
}
function transferMetricText(job, m) {
  if (job.direction === "pack") return "tar.gz";
  const active = job.state === "transferring",
    pct = (value) =>
      Number(value || 0)
        .toFixed(1)
        .replace(/\.0$/, "") + "%";
  const parts = [];
  if (active) parts.push((m.speed / 1048576).toFixed(2) + " MiB/s");
  if (m.relay)
    parts.push(
      (m.upload ? "上传" : "下载") +
        "阶段 " +
        pct(m.stageProgress) +
        "（" +
        (m.upload ? "2" : "1") +
        "/2）",
      "总进度 " + pct(m.total),
    );
  else parts.push("进度 " + pct(m.total));
  return parts.join(" · ");
}
const logViews = new Map();
let latestJobs = [];
function renderJobs(jobs) {
  latestJobs = jobs;
  $("jobCount").textContent = jobs.length + (staging ? 1 : 0);
  if ($("queueWorkspace").hidden) return;
  const queueScroll = $("jobs").scrollTop;
  document.querySelectorAll("#jobs details[data-job-id]").forEach((details) =>
    logViews.set(details.dataset.jobId, {
      open: details.open,
      scroll: details.querySelector("pre").scrollTop,
    }),
  );
  $("jobs").replaceChildren();
  if (staging) {
    const stageRow = node("div", undefined, "job"),
      stageTop = node("div", undefined, "job-top"),
      cancel = node("button", "取消");
    cancel.onclick = () => stagingAbort?.abort();
    stageTop.append(
      icon("upload"),
      node("span", "正在暂存 Finder 拖入的文件", "job-name"),
      cancel,
    );
    stageRow.append(
      stageTop,
      node(
        "div",
        stagingInfo.count + " 个文件 · " + formatSize(stagingInfo.total),
        "job-meta",
      ),
    );
    $("jobs").append(stageRow);
  }
  if (!jobs.length && !staging) {
    const empty = node("div", undefined, "queue-empty");
    empty.append(
      icon("transfer"),
      node("span", "拖放文件开始传输，或选中项目后点击传输按钮"),
    );
    $("jobs").append(empty);
    return;
  }
  for (const job of jobs.slice().reverse()) {
    const card = node("div", undefined, "job"),
      top = node("div", undefined, "job-top"),
      label = job.items.length
        ? job.items.slice(0, 3).join(" · ")
        : job.local || job.remote;
    top.append(
      icon(
        job.direction === "pack"
          ? "archive"
          : job.direction === "upload"
            ? "upload"
            : job.direction === "download"
              ? "download"
              : "transfer",
      ),
      node("span", label || "目录传输", "job-name"),
      node(
        "span",
        states[job.state],
        "state" + (job.state === "failed" ? " failed" : ""),
      ),
    );
    if (
      !["completed", "failed", "cancelled", "interrupted"].includes(job.state)
    ) {
      const cancel = node("button");
      cancel.setAttribute("aria-label", "取消任务 " + job.id);
      cancel.append(icon("close"));
      cancel.onclick = () =>
        action(cancel, async () => {
          await api("cancel", { id: job.id });
          await refresh();
        });
      top.append(cancel);
    }
    if (job.resumable) {
      const resume = node("button", "续传");
      resume.setAttribute("aria-label", "续传任务 " + job.id);
      resume.onclick = () =>
        action(resume, async () => {
          if (job.legacyResume) {
            const source = job.sourcePane || "local",
              target = job.targetPane || "remote",
              p = panes[source],
              q = panes[target];
            if (
              !p.root ||
              !q.root ||
              p.loadedKey !== endpointKey(source) ||
              q.loadedKey !== endpointKey(target)
            )
              throw Error(
                "旧记录缺少连接参数，请先在两栏打开原来的源和目标目录",
              );
            const options = createTransfer(source, target, p.root, q.root);
            if (options.direction !== job.direction)
              throw Error("请将两栏切换到原来的传输方向");
            options.items = job.items;
            options._resumeId = job.id;
            showTransfer(options, job.items);
            return;
          }
          await startJob("resume", { id: job.id });
          notice("已创建续传任务，正在校验并复用未完成数据");
          await refresh();
        });
      top.append(resume);
    }
    if (
      ["completed", "failed", "cancelled", "interrupted"].includes(job.state)
    ) {
      const remove = node("button", "删除记录");
      remove.setAttribute("aria-label", "删除记录 " + job.id);
      remove.title = "只删除历史记录，保留文件和未完成数据";
      remove.onclick = () =>
        action(remove, async () => {
          await api("delete-record", { id: job.id });
          logViews.delete(job.id);
          lastJobStates.delete(job.id);
          await refresh();
        });
      top.append(remove);
    }
    const metrics = transferMetrics(job);
    const progress = node(
        "div",
        undefined,
        "progress" +
          (["checking", "packing", "queued", "retrying"].includes(job.state)
            ? " pending"
            : ""),
      ),
      bar = node("span");
    bar.style.width = metrics.total + "%";
    progress.setAttribute("role", "progressbar");
    progress.setAttribute("aria-valuemin", "0");
    progress.setAttribute("aria-valuemax", "100");
    progress.setAttribute("aria-valuenow", String(metrics.total));
    progress.setAttribute(
      "aria-label",
      metrics.relay ? "传输总进度" : "传输进度",
    );
    progress.append(bar);
    const meta = node("div", undefined, "job-meta");
    meta.append(
      node(
        "span",
        job.result?.archive
          ? "已生成：" + job.result.archive
          : job.route || "准备中",
      ),
      node("span", transferMetricText(job, metrics)),
    );
    if (job.state === "transferring" && metrics.bytes !== undefined) {
      const extra = node("div", undefined, "job-meta metric-extra");
      extra.append(
        node(
          "span",
          (metrics.relay ? "当前阶段已处理：" : "已处理：") +
            formatSize(metrics.bytes),
        ),
        node("span", metrics.eta ? "阶段时间：" + metrics.eta : ""),
      );
      meta.append(extra);
    }
    const details = node("details"),
      summary = node(
        "summary",
        "日志" + (job.switches ? " · 切换 " + job.switches + " 次" : ""),
      ),
      log = node("pre", job.log.join("\n"));
    details.dataset.jobId = job.id;
    const view = logViews.get(job.id);
    details.open = view?.open ?? job.state === "failed";
    details.append(summary, log);
    card.append(top, progress, meta, details);
    if (job.queueReason)
      card.append(node("div", job.queueReason, "job-meta"));
    if (job.target?.path) {
      const destination = node("div", undefined, "job-target"),
        location = node(
          "span",
          "目标：" +
            (job.target.kind === "remote"
              ? (job.target.route?.name || job.target.route?.host || "远程") + " · "
              : "本地 · ") +
            (job.target.paths?.length === 1
              ? job.target.paths[0]
              : job.target.path),
        ),
        open = node("button", "打开目标目录");
      location.title = location.textContent;
      open.setAttribute("aria-label", "打开任务目标目录 " + job.id);
      open.onclick = () =>
        action(open, async () => {
          const side = job.targetPane || (job.target.kind === "local" ? "local" : "remote"),
            connection = job.target.route
              ? routes.find((r) => routePasswordKey(r) === routePasswordKey(job.target.route)) || job.target.route
              : null,
            directory = job.target.paths?.length === 1
              ? job.target.paths[0].replace(/\/[^/]+$/, "") || "/"
              : job.target.path;
          showFiles();
          await selectEndpoint(side, job.target.kind, connection, directory);
        });
      destination.append(location, open);
      card.append(destination);
    }
    if (job.remoteSession)
      card.append(
        node(
          "div",
          "远程会话：" +
            job.remoteSession +
            " · " +
            (job.executor === "destination"
              ? "在目标服务器运行"
              : "在源服务器运行") +
            (job.disconnected
              ? " · 本地连接中断，等待重新读取状态"
              : " · Mac 断联后继续"),
          "job-meta",
        ),
      );
    if (job.error) card.append(node("div", job.error, "job-error"));
    $("jobs").append(card);
    if (view) log.scrollTop = view.scroll;
  }
  $("jobs").scrollTop = queueScroll;
}
let refreshing = false;
let remoteLinksLoaded = false;
async function loadRemoteLinkDefaults(status) {
  let defaults = status.remoteLinks;
  if (!Array.isArray(defaults) && !remoteLinksLoaded) {
    remoteLinksLoaded = true;
    try {
      const saved = await api("preview", {
        side: "local",
        path: ".termiusplus-state/remote-links.json",
      });
      if (saved.kind === "text" && !saved.truncated)
        defaults = JSON.parse(saved.content);
    } catch {}
  }
  if (Array.isArray(defaults))
    remoteLinks = [
      ...remoteLinks,
      ...defaults.filter(
        (link) =>
          !remoteLinks.some(
            (x) =>
              x.executorHost === link.executorHost &&
              x.peerHost === link.peerHost &&
              Number(x.executorPort) === Number(link.executorPort) &&
              Number(x.peerPort) === Number(link.peerPort),
          ),
      ),
    ];
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const status = await api("status");
    terminalSupported = status.terminal === true;
    remoteTasksSupported = status.remoteTasks === true;
    await loadRemoteLinkDefaults(status);
    localHome = status.home;
    rsyncReady = status.rsync.ok;
    for (const side of sides) {
      if (!$(side + "Path").value)
        $(side + "Path").value =
          panes[side].paths[endpointKey(side)] ||
          (panes[side].kind === "local" ? localHome : "~");
    }
    if (!rsyncReady) {
      $("status").textContent = "需要 rsync 3.x";
      notice(status.rsync.error, true);
    } else if (!staging)
      $("status").textContent = sides.some(
        (s) => panes[s].kind === "remote" && panes[s].root,
      )
        ? "远程已连接 · rsync 就绪"
        : "rsync 就绪 · 本地工作空间";
    renderJobs(status.jobs);
    for (const job of status.jobs) {
      if (
        job.state === "completed" &&
        lastJobStates.has(job.id) &&
        lastJobStates.get(job.id) !== "completed"
      ) {
        notice(job.direction === "pack" ? "打包完成" : "传输完成");
        const side =
          job.targetPane ||
          (job.direction === "upload"
            ? "remote"
            : job.direction === "download"
              ? "local"
              : job.side || "local");
        if (panes[side]?.root)
          action(null, () => refreshFileTree(side));
      }
      lastJobStates.set(job.id, job.state);
    }
  } finally {
    refreshing = false;
  }
}
let layout = {};
try {
  layout = JSON.parse(localStorage.getItem("termiusplus.layout") || "{}");
} catch {}
const paneGrid = document.querySelector(".panes");
for (const key of ["localHeight", "remoteHeight"]) {
  if (!Number.isFinite(layout[key]))
    layout[key] = Math.max(390, Math.round(window.innerHeight * 0.68));
  layout[key] = Math.max(260, Math.min(2400, layout[key]));
}
delete layout.queueHeight;
layout.leftRatio = Number.isFinite(layout.leftRatio)
  ? Math.max(0.18, Math.min(0.82, layout.leftRatio))
  : 0.5;
// Older layouts stored independent heights; keep the taller pane on migration.
layout.localHeight = layout.remoteHeight = Math.max(
  layout.localHeight,
  layout.remoteHeight,
);
function setLayoutValue(key, value) {
  layout[key] = value;
  if (key === "localHeight" || key === "remoteHeight")
    layout.localHeight = layout.remoteHeight = value;
}
function saveLayout() {
  localStorage.setItem("termiusplus.layout", JSON.stringify(layout));
}
function applyLayout() {
  for (const side of sides)
    $(side + "Pane").style.height = layout[side + "Height"] + "px";
  const width = paneGrid.clientWidth - 10;
  if (width > 0) {
    const minimum = Math.min(180, width * 0.35),
      left = Math.max(
        minimum,
        Math.min(width - minimum, width * layout.leftRatio),
      );
    paneGrid.style.gridTemplateColumns = left + "px 10px minmax(0,1fr)";
    $("columnResizer").setAttribute(
      "aria-valuenow",
      Math.round((left / width) * 100),
    );
    $("columnResizer").setAttribute("aria-valuemin", "18");
    $("columnResizer").setAttribute("aria-valuemax", "82");
  }
  for (const [id, key] of [
    ["localResizer", "localHeight"],
    ["remoteResizer", "remoteHeight"],
  ])
    $(id).setAttribute("aria-valuenow", Math.round(layout[key]));
}
function bindResizer(id, key, axis, min, max) {
  const handle = $(id);
  handle.setAttribute("aria-valuemin", axis === "x" ? min * 100 : min);
  handle.setAttribute("aria-valuemax", axis === "x" ? max * 100 : max);
  let drag = null;
  handle.onpointerdown = (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    drag = {
      pointer: event.pointerId,
      start: axis === "x" ? event.clientX : event.clientY,
      value: layout[key],
    };
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add("resizing");
  };
  handle.onpointermove = (event) => {
    if (!drag || event.pointerId !== drag.pointer) return;
    const delta = (axis === "x" ? event.clientX : event.clientY) - drag.start;
    setLayoutValue(
      key,
      Math.max(
        min,
        Math.min(
          max,
          drag.value +
            (axis === "x"
              ? delta / Math.max(1, paneGrid.clientWidth - 10)
              : delta),
        ),
      ),
    );
    applyLayout();
  };
  function finish() {
    if (!drag) return;
    drag = null;
    document.body.classList.remove("resizing");
    saveLayout();
  }
  handle.onpointerup = finish;
  handle.onpointercancel = finish;
  handle.onlostpointercapture = finish;
  handle.onkeydown = (event) => {
    const negative = axis === "x" ? "ArrowLeft" : "ArrowUp",
      positive = axis === "x" ? "ArrowRight" : "ArrowDown";
    if (event.key === negative || event.key === positive) {
      event.preventDefault();
      setLayoutValue(
        key,
        Math.max(
          min,
          Math.min(
            max,
            layout[key] +
              (event.key === positive ? 1 : -1) * (axis === "x" ? 0.03 : 40),
          ),
        ),
      );
      applyLayout();
      saveLayout();
    }
  };
  handle.ondblclick = () => {
    setLayoutValue(
      key,
      axis === "x"
        ? 0.5
        : Math.max(390, Math.round(window.innerHeight * 0.68)),
    );
    applyLayout();
    saveLayout();
  };
}
bindResizer("columnResizer", "leftRatio", "x", 0.18, 0.82);
bindResizer("localResizer", "localHeight", "y", 260, 2400);
bindResizer("remoteResizer", "remoteHeight", "y", 260, 2400);
new ResizeObserver(applyLayout).observe(paneGrid);
applyLayout();
if (window.frameElement?.dataset.view === "queue") showQueue();
renderRoutes();
for (const side of sides) {
  updateHeading(side);
  $(side + "Path").value =
    panes[side].paths[endpointKey(side)] ||
    (panes[side].kind === "remote" ? "~" : "");
  disconnected(side);
}
if (!token()) {
  notice("请使用启动时输出的完整链接打开", true);
  $("status").textContent = "缺少访问凭证";
} else {
  refresh()
    .then(async () => {
      for (const side of sides) {
        if (panes[side].kind === "local" || panes[side].connection)
          await action(null, () => load(side, undefined, true));
      }
      focus("local");
      const direct = new URLSearchParams(fragment.split("?")[1] || "").get(
        "direct",
      );
      if (direct) {
        const config = JSON.parse(direct);
        showTransfer(config, config.items || []);
      }
    })
    .catch((e) => notice(e.message, true));
  setInterval(
    () =>
      refresh().catch((e) => {
        $("status").textContent = "服务连接中断";
        notice(e.message, true);
      }),
    2000,
  );
}
