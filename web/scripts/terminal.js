"use strict";
const $ = (id) => document.getElementById(id),
  fragment = window.frameElement?.dataset.fragment || location.hash.slice(1),
  appOrigin = new URL(document.baseURI).origin,
  separator = fragment.indexOf("?"),
  token = separator < 0 ? fragment : fragment.slice(0, separator);
let config = {};
try {
  config = JSON.parse(
    new URLSearchParams(separator < 0 ? "" : fragment.slice(separator + 1)).get(
      "config",
    ) || "{}",
  );
} catch {}
const routes = Array.isArray(config.routes) ? config.routes : [];
if (!routes.length) {
  try {
    routes.push(
      ...JSON.parse(localStorage.getItem("termiusplus.routes") || "[]"),
    );
  } catch {}
}
const localTarget = { local: true, name: "本地" };
let localPath = typeof config.localPath === "string" ? config.localPath : "";
const initialTarget = config.local
  ? localTarget
  : routes.find(
      (route) => JSON.stringify(route) === JSON.stringify(config.route),
    ) || routes[0];
function renderPicker() {
  $("pickerRoutes").replaceChildren();
  const local = document.createElement("button");
  local.className = "picker-route";
  local.append(document.createTextNode("本地 · 这台电脑"));
  const localHint = document.createElement("small");
  localHint.textContent = "SHELL";
  local.append(localHint);
  local.onclick = () => {
    hidePicker();
    connect(localTarget, localPath);
  };
  $("pickerRoutes").append(local);
  for (const route of routes) {
    const button = document.createElement("button");
    button.className = "picker-route";
    button.append(document.createTextNode(route.name || route.host));
    const hint = document.createElement("small");
    hint.textContent = "SSH";
    button.append(hint);
    button.onclick = () => {
      hidePicker();
      connect(route);
    };
    $("pickerRoutes").append(button);
  }
  if (!routes.length) {
    const hint = document.createElement("div");
    hint.className = "picker-empty";
    hint.textContent =
      "还没有远程连接；可在文件传输页面新建，或直接使用上面的本地终端。";
    $("pickerRoutes").append(hint);
  }
}
renderPicker();
function hidePicker() {
  $("sessionPicker").hidden = true;
  $("newSession").setAttribute("aria-expanded", "false");
}
function togglePicker() {
  if (!$("sessionPicker").hidden) {
    hidePicker();
    return;
  }
  const box = $("newSession").getBoundingClientRect();
  $("sessionPicker").style.left =
    Math.max(12, Math.min(box.left, window.innerWidth - 272)) + "px";
  $("sessionPicker").style.top = box.bottom + 6 + "px";
  $("sessionPicker").hidden = false;
  $("newSession").setAttribute("aria-expanded", "true");
  $("pickerRoutes").querySelector("button")?.focus();
}
$("newSession").onclick = togglePicker;
document.addEventListener("click", (event) => {
  if (
    !$("sessionPicker").contains(event.target) &&
    !$("newSession").contains(event.target)
  )
    hidePicker();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("sessionPicker").hidden) {
    hidePicker();
    $("newSession").focus();
  }
});
window.addEventListener("resize", hidePicker);
$("filesLink").href = /^http:\/\/127\.0\.0\.1:\d+\/#[-\w]+$/.test(
  config.filesUrl || "",
)
  ? config.filesUrl
  : appOrigin + "/#" + token;
let filesFrame = null;
if (window.frameElement?.dataset.hostView === "files")
  document.body.classList.add("embedded-terminal");
$("terminalSelf").onclick = () => {
  if (filesFrame) filesFrame.hidden = true;
  if (active) activate(active);
};
$("filesLink").onclick = async (event) => {
  event.preventDefault();
  hidePicker();
  if (window.frameElement?.dataset.hostView === "files") {
    parent.postMessage({ type: "termiusplus:show-files" }, appOrigin);
    return;
  }
  if (filesFrame) {
    filesFrame.hidden = false;
    return;
  }
  filesFrame = document.createElement("iframe");
  filesFrame.className = "files-frame";
  filesFrame.title = "文件传输";
  filesFrame.dataset.hostView = "terminal";
  filesFrame.dataset.fragment = token;
  filesFrame.dataset.routes = JSON.stringify(routes);
  document.body.append(filesFrame);
  try {
    const response = await fetch(new URL($("filesLink").href).origin + "/");
    if (!response.ok) throw Error("文件页面加载失败");
    filesFrame.srcdoc = await response.text();
  } catch (error) {
    filesFrame.remove();
    filesFrame = null;
    state(error.message, true);
  }
};
window.addEventListener("message", (event) => {
  if (event.origin !== appOrigin) return;
  const fromFiles = event.source === filesFrame?.contentWindow,
    fromHost =
      window.frameElement?.dataset.hostView === "files" &&
      event.source === parent;
  if (!fromFiles && !fromHost) return;
  if (
    ![
      "termiusplus:show-terminal",
      "termiusplus:activate-terminal",
      "termiusplus:update-routes",
    ].includes(event.data?.type)
  )
    return;
  const incoming = event.data.config || {};
  if (Array.isArray(incoming.routes)) {
    routes.splice(0, routes.length, ...incoming.routes);
    renderPicker();
  }
  if (typeof incoming.localPath === "string") localPath = incoming.localPath;
  if (event.data.type === "termiusplus:update-routes") return;
  if (filesFrame) filesFrame.hidden = true;
  const chosen = incoming.local ? localTarget : incoming.route;
  if (event.data.requestConnection && chosen) {
    const existing = sessions.find(
      (s) => s.id && JSON.stringify(s.target) === JSON.stringify(chosen),
    );
    if (existing) activate(existing);
    else
      connect(chosen, typeof incoming.path === "string" ? incoming.path : "");
  } else if (active) activate(active);
});
const sessions = [],
  limit = 8;
let active = null,
  sequence = 0;
const theme = {
  background: "#141617",
  foreground: "#e7ece8",
  cursor: "#c5aa7c",
  selectionBackground: "#788d7a66",
  black: "#20242c",
  red: "#ef9292",
  green: "#83d6a5",
  yellow: "#e4c589",
  blue: "#8baef8",
  magenta: "#c7a1ef",
  cyan: "#8cd4df",
  white: "#d9deea",
};
function state(text, error = false) {
  $("state").textContent = text;
  $("state").classList.toggle("error", error);
}
function sessionState(s, text, error = false) {
  s.status = text;
  s.error = error;
  if (active === s) state(text, error);
  renderTabs();
}
function controls() {
  const count = sessions.filter((s) => s.id || s.connecting).length;
  $("newSession").disabled = count >= limit;
  $("newSession").title = count >= limit ? "最多同时连接 8 个会话" : "新建会话";
  $("emptyTerminal").hidden = sessions.length > 0;
  $("terminal").hidden = !sessions.length;
}
function renderTabs() {
  for (const s of sessions) {
    s.tab.classList.toggle("active", s === active);
    s.tab.classList.toggle("live", !!s.id);
    s.tab.classList.toggle("connecting", s.connecting);
    s.select.setAttribute("aria-selected", String(s === active));
    s.select.tabIndex = s === active ? 0 : -1;
    s.select.title =
      s.id || s.connecting ? s.status : s.status + " · 点击重新连接";
    s.host.hidden = s !== active;
  }
  controls();
}
function fitVisible(s) {
  if (s.host.clientWidth > 0 && s.host.clientHeight > 0) s.fit.fit();
}
function activate(s) {
  active = s;
  renderTabs();
  if (!s) {
    state("点击标签旁的 ＋ 新建会话");
    document.title = "TermiusPlus · Terminal";
    return;
  }
  state(s.status, s.error);
  document.title = s.name + " · TermiusPlus Terminal";
  requestAnimationFrame(() => {
    if (active === s && !s.closed) {
      fitVisible(s);
      s.term.focus();
    }
  });
}
async function api(operation, data = {}) {
  const response = await fetch("/api/terminal/" + operation, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "终端请求失败");
  return result;
}
function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
async function poll(s, id, epoch) {
  let failures = 0;
  while (!s.closed && s.id === id && s.generation === epoch) {
    try {
      const result = await api("poll", { id, cursor: s.cursor });
      if (s.closed || s.id !== id || s.generation !== epoch) return;
      if (result.truncated)
        s.term.writeln("\r\n[较早的输出已超出缓冲区，跳到最近内容]\r\n");
      if (result.content) {
        const bytes = Uint8Array.from(atob(result.content), (c) =>
          c.charCodeAt(0),
        );
        await new Promise((resolve) => s.term.write(bytes, resolve));
      }
      s.cursor = result.cursor;
      failures = 0;
      if (result.done) {
        s.id = null;
        sessionState(
          s,
          "会话已结束 · " + s.name + " · 退出码 " + result.exitCode,
          result.exitCode !== 0,
        );
        s.term.writeln("\r\n[会话已结束，可重新连接]");
        return;
      }
      sessionState(s, "会话运行中 · " + s.name);
      await delay(80);
    } catch (error) {
      if (s.closed || s.id !== id || s.generation !== epoch) return;
      failures++;
      sessionState(s, "等待恢复连接 · " + s.name + "：" + error.message, true);
      if (failures >= 5) {
        await disconnect(s);
        sessionState(s, "终端连接中断，请重新连接 · " + s.name, true);
        return;
      }
      await delay(1000);
    }
  }
}
function send(s, data) {
  const id = s?.id,
    epoch = s?.generation;
  if (!id) return;
  s.writeChain = s.writeChain
    .then(async () => {
      if (!s.closed && id === s.id && epoch === s.generation)
        await api("write", { id, ...data });
    })
    .catch((error) => sessionState(s, error.message, true));
}
function createSession(target, path) {
  const s = {
    number: ++sequence,
    name: target.local ? "本地" : target.name || target.host,
    target,
    path,
    id: null,
    cursor: 0,
    generation: 0,
    connecting: false,
    closed: false,
    status: "准备连接",
    error: false,
    writeChain: Promise.resolve(),
    resizeTimer: null,
  };
  const repeated = sessions.filter(
    (x) =>
      x.target.local === target.local &&
      (target.local || x.target.host === target.host),
  ).length;
  if (repeated) s.name += " · " + (repeated + 1);
  s.tab = document.createElement("div");
  s.tab.className = "session-tab";
  s.select = document.createElement("button");
  s.select.setAttribute("role", "tab");
  s.select.id = "session-tab-" + s.number;
  const dot = document.createElement("span");
  dot.className = "dot";
  s.select.append(dot, document.createTextNode(s.name));
  s.select.onclick = () => {
    activate(s);
    if (!s.id && !s.connecting) openSession(s);
  };
  s.select.onkeydown = (e) => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const i = sessions.indexOf(s),
        offset = e.key === "ArrowRight" ? 1 : -1;
      activate(sessions[(i + offset + sessions.length) % sessions.length]);
    }
  };
  const close = document.createElement("button");
  close.textContent = "×";
  close.className = "close-tab";
  close.setAttribute("aria-label", "关闭会话 " + s.name);
  close.onclick = () => closeSession(s);
  s.tab.append(s.select, close);
  $("sessionTabs").append(s.tab);
  s.host = document.createElement("div");
  s.host.className = "terminal-host";
  s.host.setAttribute("role", "tabpanel");
  s.host.setAttribute("aria-labelledby", s.select.id);
  $("terminal").append(s.host);
  sessions.push(s);
  controls();
  s.term = new Terminal({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: "Menlo, Monaco, ui-monospace, monospace",
    scrollback: 5000,
    screenReaderMode: true,
    theme,
  });
  s.fit = new FitAddon.FitAddon();
  s.term.loadAddon(s.fit);
  s.term.open(s.host);
  s.term.textarea.setAttribute("aria-label", "终端输入 · " + s.name);
  s.term.onData((content) => {
    for (let i = 0; i < content.length; ) {
      let end = Math.min(i + 4000, content.length);
      const c = content.charCodeAt(end - 1);
      if (end < content.length && c >= 0xd800 && c <= 0xdbff) end--;
      send(s, { content: content.slice(i, end) });
      i = end;
    }
  });
  s.term.onBinary((content) => send(s, { binary: btoa(content) }));
  s.term.onResize(({ cols, rows }) => {
    clearTimeout(s.resizeTimer);
    s.resizeTimer = setTimeout(() => {
      if (!s.closed && s.id)
        api("resize", { id: s.id, cols, rows }).catch((error) =>
          sessionState(s, error.message, true),
        );
    }, 100);
  });
  s.term.attachCustomKeyEventHandler((event) => {
    if (
      event.type === "keydown" &&
      (event.ctrlKey || event.metaKey) &&
      event.shiftKey &&
      event.code === "KeyC"
    ) {
      copy(s);
      return false;
    }
    return true;
  });
  activate(s);
  return s;
}
async function openSession(s) {
  if (s.closed || s.id || s.connecting) return;
  s.connecting = true;
  sessionState(s, "正在连接 " + s.name + "…");
  fitVisible(s);
  const epoch = ++s.generation;
  try {
    const result = await api("open", {
      route: s.target.local ? undefined : s.target,
      local: !!s.target.local,
      path: s.path,
      cols: s.term.cols,
      rows: s.term.rows,
    });
    if (s.closed || s.generation !== epoch) {
      await api("close", { id: result.id });
      return;
    }
    s.id = result.id;
    s.cursor = 0;
    s.writeChain = Promise.resolve();
    s.term.reset();
    sessionState(s, "会话已打开 · " + s.name);
    poll(s, s.id, epoch);
    if (active === s) s.term.focus();
  } catch (error) {
    if (!s.closed) {
      sessionState(s, error.message, true);
      s.term.writeln("\r\n[连接失败：" + error.message + "]");
    }
  } finally {
    s.connecting = false;
    renderTabs();
  }
}
function connect(target = initialTarget, path = "") {
  if (sessions.filter((s) => s.id || s.connecting).length >= limit) {
    state("最多同时连接 8 个会话，请先断开一个", true);
    return;
  }
  if (!target) {
    state("请先在文件工具中添加远程连接", true);
    return;
  }
  if (!token) {
    state("缺少访问凭证，请从文件工具打开终端", true);
    return;
  }
  if (sessions.length >= 16) {
    state("请先关闭不再使用的会话标签", true);
    return;
  }
  return openSession(createSession(target, path));
}
async function disconnect(s = active) {
  if (!s) return;
  const id = s.id;
  s.id = null;
  s.generation++;
  renderTabs();
  if (id) {
    try {
      await api("close", { id });
      if (!s.closed) {
        sessionState(s, "已断开连接 · " + s.name);
        s.term.writeln("\r\n[已断开连接]");
      }
    } catch (error) {
      if (!s.closed) sessionState(s, error.message, true);
    }
  }
}
async function closeSession(s) {
  s.closed = true;
  const i = sessions.indexOf(s);
  sessions.splice(i, 1);
  s.tab.remove();
  clearTimeout(s.resizeTimer);
  if (active === s)
    activate(sessions[Math.min(i, sessions.length - 1)] || null);
  else renderTabs();
  await disconnect(s);
  s.term.dispose();
  s.host.remove();
}
new ResizeObserver(() => {
  if (active && !active.closed) fitVisible(active);
}).observe($("terminal"));
async function copy(s = active) {
  if (!s) return;
  const text = s.term.getSelection();
  if (!text) {
    sessionState(s, "请先在终端里选中要复制的内容");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    sessionState(s, "已复制选中内容");
  } catch {
    sessionState(s, "复制不可用，请使用系统复制快捷键", true);
  }
}
window.addEventListener("pagehide", () => {
  for (const s of sessions) {
    s.closed = true;
    s.generation++;
    if (s.id)
      fetch("/api/terminal/close", {
        method: "POST",
        headers: {
          Authorization: "Bearer " + token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ id: s.id }),
        keepalive: true,
      }).catch(() => {});
  }
});
controls();
if (config.autoConnect && initialTarget)
  connect(
    initialTarget,
    typeof config.path === "string" ? config.path : localPath,
  );
else state("点击标签旁的 ＋ 打开本地或远程终端");
