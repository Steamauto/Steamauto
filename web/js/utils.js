
const API = '/api';
const REFRESH_INTERVAL_DEFAULT = 60;
window.RUNTIME_PROFILE = null;
function el(id) {
  return document.getElementById(id);
}
function toast(title, detail = "") {
  const host = el("toast-host");
  if (!host) return;
  const node = document.createElement("div");
  node.className = "toast";
  node.innerHTML = `<div class="t">${escapeHtml(title)}</div>${detail ? `<div class="d">${escapeHtml(compactErrorText(detail, 220))}</div>` : ""}`;
  host.appendChild(node);
  const ttl = 3500;
  setTimeout(() => {
    node.classList.add("toast-exit");
  }, ttl - 400);
  setTimeout(() => node.remove(), ttl);
}
function appModal(options = {}) {
  const {
    title = "确认操作",
    message = "",
    content = "",
    actions = null,
    variant = "default",
    input = null,
    defaultValue = "",
    autofocus = true,
    width = "460px",
    exportText = "",
    exportFileName = "",
  } = options;
  return new Promise((resolve) => {
    const host = document.createElement("div");
    host.className = "app-modal-host";
    const actionList = actions || [
      { label: "取消", value: false, kind: "secondary" },
      { label: "确认", value: true, kind: "primary" },
    ];
    const inputTag = input && input.type === "textarea"
      ? `<textarea class="app-modal-input" rows="${Number(input.rows || 6)}" placeholder="${escapeHtml(input.placeholder || "")}">${escapeHtml(defaultValue)}</textarea>`
      : `<input class="app-modal-input" type="${escapeHtml(input?.type || "text")}" value="${escapeHtml(defaultValue)}" placeholder="${escapeHtml(input?.placeholder || "")}" />`;
    const inputHtml = input ? `
      <label class="app-modal-field">
        ${input.label ? `<span>${escapeHtml(input.label)}</span>` : ""}
        ${inputTag}
      </label>
    ` : "";
    const hasCustomContent = !!content;
    const contentHtml = content instanceof HTMLElement ? "" : String(content || "");
    host.innerHTML = `
      <div class="app-modal-backdrop"></div>
      <section class="app-modal app-modal--${escapeHtml(variant)}" role="dialog" aria-modal="true" style="max-width:${escapeHtml(width)}">
        <div class="app-modal-head">
          <h3>${escapeHtml(title)}</h3>
          <button type="button" class="app-modal-close" aria-label="关闭">×</button>
        </div>
        ${message ? `<div class="app-modal-message">${escapeHtml(message).replace(/\n/g, "<br>")}</div>` : ""}
        ${exportText ? `<div class="app-modal-export"><button type="button" class="btn btn-secondary btn-sm" data-export-error>导出错误信息</button></div>` : ""}
        ${inputHtml}
        ${hasCustomContent ? `<div class="app-modal-content">${contentHtml}</div>` : ""}
        <div class="app-modal-actions">
          ${actionList.map((a, idx) => `<button type="button" class="btn btn-sm ${a.kind === "primary" ? "btn-primary" : a.kind === "danger" ? "btn-danger" : "btn-secondary"}" data-action="${idx}">${escapeHtml(a.label)}</button>`).join("")}
        </div>
      </section>
    `;
    document.body.appendChild(host);
    const modal = host.querySelector(".app-modal");
    const contentBox = host.querySelector(".app-modal-content");
    if (content instanceof HTMLElement && contentBox) contentBox.appendChild(content);
    host.querySelector("[data-export-error]")?.addEventListener("click", () => {
      downloadTextFile(exportFileName || buildErrorExportName("aetherswap-error"), exportText);
      toast("错误信息已导出");
    });
    const close = (value) => {
      host.classList.add("closing");
      setTimeout(() => host.remove(), 160);
      resolve(value);
    };
    host.querySelector(".app-modal-close")?.addEventListener("click", () => close(false));
    host.querySelector(".app-modal-backdrop")?.addEventListener("click", () => close(false));
    host.querySelectorAll("[data-action]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const action = actionList[Number(btn.dataset.action)];
        if (input && action.value !== false) {
          close(host.querySelector(".app-modal-input")?.value ?? "");
        } else {
          close(action.value);
        }
      });
    });
    host.addEventListener("keydown", (e) => {
      if (e.key === "Escape") close(false);
      if (e.key === "Enter" && input && !e.shiftKey && e.target?.tagName !== "TEXTAREA") {
        e.preventDefault();
        close(host.querySelector(".app-modal-input")?.value ?? "");
      }
    });
    requestAnimationFrame(() => {
      host.classList.add("shown");
      if (autofocus) (host.querySelector(".app-modal-input") || host.querySelector(".app-modal-actions .btn-primary") || host.querySelector(".app-modal-actions button"))?.focus();
    });
    if (typeof options.onOpen === "function") options.onOpen(host, close);
  });
}
function appConfirm(message, options = {}) {
  return appModal({
    title: options.title || "确认操作",
    message,
    variant: options.variant || "warning",
    actions: [
      { label: options.cancelText || "取消", value: false, kind: "secondary" },
      { label: options.confirmText || "确认", value: true, kind: options.danger ? "danger" : "primary" },
    ],
    width: options.width || "460px",
  });
}
function appPrompt(title, defaultValue = "", options = {}) {
  return appModal({
    title,
    message: options.message || "",
    input: { label: options.label || "", placeholder: options.placeholder || "", type: options.type || "text", rows: options.rows },
    defaultValue,
    actions: [
      { label: options.cancelText || "取消", value: false, kind: "secondary" },
      { label: options.confirmText || "确认", value: true, kind: "primary" },
    ],
    width: options.width || "440px",
    exportText: options.exportText || "",
    exportFileName: options.exportFileName || "",
  });
}
function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = String(s ?? "");
  return div.innerHTML;
}
async function fetchJson(url, opts = {}) {
  const res = await fetch(url, {
    ...opts,
    cache: opts.cache ?? "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(opts.headers || {}),
    },
  });
  const text = await res.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
  }
  if (!res.ok) {
    const msg = (data && (data.error || data.message)) || res.statusText || "请求失败";
    throw new Error(msg);
  }
  return data;
}
async function loadRuntimeProfile() {
  try {
    const data = await fetchJson(API + "/runtime");
    window.RUNTIME_PROFILE = data.runtime || null;
  } catch {
    window.RUNTIME_PROFILE = null;
  }
  applyRuntimeUiHints();
  return window.RUNTIME_PROFILE;
}
function getRuntimeProfile() {
  return window.RUNTIME_PROFILE || {};
}
function runtimeCanLaunchBrowser() {
  const profile = getRuntimeProfile();
  return profile.can_launch_headful_browser !== false;
}
function runtimeManualLoginMessage(type = "steam") {
  const label = type === "buff" ? "Buff" : "Steam";
  const profile = getRuntimeProfile();
  if (profile.can_launch_headful_browser === false) {
    return `当前后端运行在服务器/无桌面环境，无法弹出 ${label} 登录浏览器。请在你本机浏览器完成登录后，复制 ${label} Cookie 并粘贴保存。`;
  }
  return `请从已登录的浏览器复制 ${label} Cookie 后粘贴到下方。`;
}
function compactErrorText(reason, limit = 260) {
  let text = String(reason || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  text = text
    .replace(/Browser logs:.*/i, "完整浏览器日志见调试日志")
    .replace(/<launching>.*$/i, "完整浏览器日志见调试日志")
    .replace(/Call log:.*/i, "完整浏览器日志见调试日志");
  if (text.length > limit) {
    return text.slice(0, limit) + "…（完整信息可导出查看）";
  }
  return text;
}
function compactLoginError(reason) {
  return compactErrorText(reason, 220);
}
function buildErrorExportName(prefix = "aetherswap-error") {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  return `${prefix}-${stamp}.txt`;
}
function downloadTextFile(filename, text) {
  const blob = new Blob([String(text || "")], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || buildErrorExportName();
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function applyRuntimeUiHints() {
  const canLaunch = runtimeCanLaunchBrowser();
  const reloginOpen = el("relogin-btn-open");
  const reloginOk = el("relogin-btn-ok");
  if (reloginOpen) reloginOpen.textContent = canLaunch ? "打开浏览器并登录" : "手动填写 Cookie";
  if (reloginOk && !canLaunch) reloginOk.disabled = true;

  const wizardBuffOpen = el("wiz-buff-open");
  if (wizardBuffOpen) {
    const icon = wizardBuffOpen.querySelector("svg")?.outerHTML || "";
    wizardBuffOpen.innerHTML = `${icon}${canLaunch ? "打开浏览器登录 Buff" : "手动填写 Buff Cookie"}`;
  }
  const wizardBuffDesc = document.querySelector("#wizard-step-3 .wizard-desc");
  if (wizardBuffDesc && !canLaunch) {
    wizardBuffDesc.textContent = "AetherSwap 需要您的 Buff Cookie 来读取市场数据和下单。当前后端无法弹出图形浏览器，请在本机浏览器登录 Buff 后复制 Cookie 并粘贴保存。";
  }
  const accountGuideStep = document.querySelector("#accounts-guide-callout .agu-step:nth-child(2)");
  if (accountGuideStep && !canLaunch) {
    accountGuideStep.innerHTML = '<span class="agu-num">2</span> 点击账号卡片上的「<strong>验证</strong>」，若服务器无法自动处理则手动粘贴 Cookie';
  }
}
function buildAccountAvatar(name, avatarUrl, size = 40) {
  const initial = (name || "?").trim().charAt(0).toUpperCase() || "?";
  if (avatarUrl) {
    return `<div class="account-avatar-wrap" style="width:${size}px;height:${size}px">
      <img class="account-avatar" style="width:${size}px;height:${size}px" src="${escapeHtml(avatarUrl)}" alt="" onerror="this.style.display='none';var s=this.nextElementSibling;if(s)s.style.display='flex';" />
      <div class="account-avatar placeholder" style="display:none;width:${size}px;height:${size}px">${escapeHtml(initial)}</div>
    </div>`;
  }
  return `<div class="account-avatar placeholder" style="width:${size}px;height:${size}px">${escapeHtml(initial)}</div>`;
}
function deepMerge(a, b) {
  const out = { ...a };
  for (const k of Object.keys(b)) {
    if (b[k] != null && typeof b[k] === "object" && !Array.isArray(b[k]) && typeof a[k] === "object" && a[k] != null) {
      out[k] = deepMerge(a[k], b[k]);
    } else {
      out[k] = b[k];
    }
  }
  return out;
}
function animateValue(elem, end, duration = 600) {
  if (!elem) return;
  const text = elem.textContent || "0";
  const start = parseFloat(text.replace(/[^0-9.\-]/g, "")) || 0;
  if (Math.abs(start - end) < 0.005) { elem.textContent = end.toFixed(text.includes(".") ? (text.split(".")[1] || "").length || 2 : 0); return; }
  const decimals = text.includes(".") ? Math.max((text.split(".")[1] || "").length, 2) : 0;
  const startTime = performance.now();
  const step = (now) => {
    const t = Math.min((now - startTime) / duration, 1);
    const ease = 1 - Math.pow(1 - t, 3);
    elem.textContent = (start + (end - start) * ease).toFixed(decimals);
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
