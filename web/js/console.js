
let logLines = [];
let debugPaused = false;
let autoScroll = (localStorage.getItem('autoScroll') || '1') === '1';
function hasLogFilter() {
  const q = (el("log-search")?.value || "").trim();
  const lv = el("log-level")?.value || "all";
  return !!q || lv !== "all";
}
function _levelClass(level) {
  if (level === "error") return "log-line-error";
  if (level === "warn") return "log-line-warn";
  if (level === "debug") return "log-line-debug";
  return "log-line-info";
}
function _fmtTime(t) {
  if (t == null) return "";
  const d = new Date(t * 1000);
  return d.toTimeString().slice(0, 8);
}
function _lineToHtml(x) {
  const cls = _levelClass(x.level || "info");
  const time = _fmtTime(x.t);
  const txt = `${time} [${x.level || "info"}] ${x.msg || ""}`;
  // escape HTML special chars
  const safe = txt.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return `<span class="${cls}">${safe}</span>`;
}
function renderLogFull() {
  const out = el("log-output");
  if (!out) return;
  const q = (el("log-search")?.value || "").trim().toLowerCase();
  const lv = el("log-level")?.value || "all";
  const filtered = logLines.filter((x) => {
    if (lv !== "all" && (x.level || "info") !== lv) return false;
    if (!q) return true;
    const s = `[${x.level || "info"}] ${x.msg || ""}`.toLowerCase();
    return s.includes(q);
  });
  out.innerHTML = filtered.map(_lineToHtml).join("\n") + (filtered.length ? "\n" : "");
  if (autoScroll) out.scrollTop = out.scrollHeight;
}
async function refreshLog() {
  if (debugPaused) return;
  const out = el("log-output");
  if (!out) return;
  const since = (out.dataset.lastIndex || "0") | 0;
  try {
    const d = await fetchJson(API + "/log?since=" + since);
    const lines = d.lines || [];
    if (!lines.length) return;
    lines.forEach((l) => {
      logLines.push({ t: l.t, level: l.level || "info", msg: l.msg || "", id: l.id });
    });
    const nextSince = lines.length ? Math.max(...lines.map((l) => l.id || 0)) : since;
    out.dataset.lastIndex = String(nextSince);
    _updateBadge();
    if (hasLogFilter()) {
      renderLogFull();
    } else {
      // append color-coded spans for new lines
      const frag = document.createDocumentFragment();
      lines.forEach((l, i) => {
        if (i > 0) frag.appendChild(document.createTextNode("\n"));
        const span = document.createElement("span");
        span.className = _levelClass(l.level || "info");
        span.textContent = `${_fmtTime(l.t)} [${l.level || "info"}] ${l.msg || ""}`;
        frag.appendChild(span);
      });
      frag.appendChild(document.createTextNode("\n"));
      out.appendChild(frag);
      if (autoScroll) out.scrollTop = out.scrollHeight;
    }
  } catch {
  }
}
async function clearLog() {
  try {
    await fetch(API + "/log/clear", { method: "POST" });
    logLines = [];
    const out = el("log-output");
    if (out) {
      out.innerHTML = "";
      out.dataset.lastIndex = "0";
    }
    _updateBadge();
    toast("日志已清空");
  } catch (e) {
    toast("清空失败", e.message || "请稍后再试");
  }
}
function _updatePauseBtn() {
  const b = el("btn-toggle-pause");
  if (!b) return;
  const svgHtml = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';
  const playHtml = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
  b.innerHTML = `${debugPaused ? playHtml : svgHtml} ${debugPaused ? '继续' : '暂停'}`;
}
function _updateScrollBtn() {
  const b = el("btn-toggle-scroll");
  if (!b) return;
  const svgHtml = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="17 11 12 6 7 11"/><polyline points="17 18 12 13 7 18"/></svg>';
  b.innerHTML = `${svgHtml} 自动滚动：${autoScroll ? '开' : '关'}`;
}
function _updateBadge() {
  const badge = el("log-count-badge");
  if (badge) badge.textContent = logLines.length + ' 行';
}
function togglePause() {
  debugPaused = !debugPaused;
  _updatePauseBtn();
  toast(debugPaused ? "已暂停刷新" : "已继续刷新");
}
function toggleAutoScroll() {
  autoScroll = !autoScroll;
  localStorage.setItem("autoScroll", autoScroll ? "1" : "0");
  _updateScrollBtn();
  toast("自动滚动", autoScroll ? "开启" : "关闭");
}
function downloadLog() {
  function fmtTime(t) {
    if (t == null) return "";
    const d = new Date(t * 1000);
    return d.toTimeString().slice(0, 8);
  }
  const blob = new Blob([
    logLines.map((x) => `${fmtTime(x.t)} [${x.level || "info"}] ${x.msg || ""}`).join("\n") + "\n",
  ], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `log_${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  toast("已下载日志");
}
async function exportLog() {
  try {
    const r = await fetch(API + "/log/export", { method: "POST" });
    const d = await r.json();
    if (d.ok) {
      toast("日志已导出", `已保存 ${d.lines} 行 → ${d.path}`);
    } else {
      toast("导出失败", d.error || "请稍后再试");
    }
  } catch (e) {
    toast("导出失败", e.message || "请稍后再试");
  }
}