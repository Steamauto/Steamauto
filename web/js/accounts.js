
let accountsCache = [];
let accountsCurrentId = null;
let selectedAccountId = null;
let accountEditId = null;
let accountsSearchTerm = '';
const accountVerificationsInFlight = new Set();
function renderAccountDetail(acc, currentId) {
  const detail = el("account-detail");
  if (!detail) return;
  if (!acc) {
    detail.innerHTML = `<div class="account-detail-empty">
      <div class="empty-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
          <circle cx="12" cy="7" r="4"></circle>
        </svg>
      </div>
      <h3>选择一个账号</h3>
      <p>在左侧选择账号查看详情，或点击「添加」创建新账号。</p>
    </div>`;
    return;
  }
  const isCurrent = acc.id === currentId;
  const name = acc.display_name || acc.username || acc.steam_id || "未命名";
  const meta = [acc.username, acc.steam_id].filter(Boolean).join(" · ") || "—";
  const avatar = buildAccountAvatar(name, acc.avatar_url, 56);
  const currency = (acc.currency_code || "").toUpperCase();
  let currencyLabel = currency || "—";
  if (currency === "CNY") currencyLabel = "人民币 (CNY)";
  else if (currency === "HKD") currencyLabel = "港币 (HKD)";
  else if (currency === "USD") currencyLabel = "美元 (USD)";
  else if (currency === "INR") currencyLabel = "印度卢比 (INR)";
  else if (currency === "RUB") currencyLabel = "卢布 (RUB)";
  else if (currency === "EUR") currencyLabel = "欧元 (EUR)";
  const region = (acc.region_code || "").toUpperCase();
  let regionLabel = region || "—";
  if (region === "CN") regionLabel = "中国 (CN)";
  else if (region === "HK") regionLabel = "中国香港 (HK)";
  else if (region === "US") regionLabel = "美国 (US)";
  else if (region === "IN") regionLabel = "印度 (IN)";
  else if (region === "RU") regionLabel = "俄罗斯 (RU)";
  else if (region === "EU") regionLabel = "欧元区 (EU)";
  detail.innerHTML = `
    <div class="account-detail-header">
      <div class="account-detail-main">
        ${avatar}
        <div style="min-width:0">
          <div class="account-detail-title">${escapeHtml(name)} ${isCurrent ? '<span class="badge badge-current">当前</span>' : ""}</div>
          <div class="account-detail-meta">${escapeHtml(meta)}</div>
        </div>
      </div>
      <div class="account-detail-actions">
        <button type="button" class="btn btn-secondary btn-sm" id="btn-acc-verify" data-id="${escapeHtml(acc.id)}" ${accountVerificationsInFlight.has(acc.id) ? 'disabled' : ''}>${accountVerificationsInFlight.has(acc.id) ? '验证中…' : '验证'}</button>
        ${!isCurrent ? `<button type="button" class="btn btn-primary btn-sm" id="btn-acc-set-current" data-id="${escapeHtml(acc.id)}">设为当前</button>` : ""}
        <button type="button" class="btn btn-edit btn-sm" id="btn-acc-edit" data-id="${escapeHtml(acc.id)}">编辑</button>
        <button type="button" class="btn btn-danger-outline btn-sm" id="btn-acc-del" data-id="${escapeHtml(acc.id)}">删除</button>
      </div>
    </div>
    <div class="account-detail-body">
      <div class="kv-grid">
        <div class="kv"><div class="k">Steam 用户名</div><div class="v mono">${escapeHtml(acc.username || "—")}</div></div>
        <div class="kv"><div class="k">Steam ID</div><div class="v mono">${escapeHtml(acc.steam_id || "—")}</div></div>
        <div class="kv"><div class="k">显示名</div><div class="v">${escapeHtml(acc.display_name || "—")}</div></div>
        <div class="kv"><div class="k">头像</div><div class="v">${acc.avatar_url ? "已获取" : "未获取"}</div></div>
        <div class="kv"><div class="k">结算币种</div><div class="v mono">${escapeHtml(currencyLabel)}</div></div>
        <div class="kv"><div class="k">地区</div><div class="v mono">${escapeHtml(regionLabel)}</div></div>
      </div>
      <div class="callout">
        <svg class="callout-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path d="M12 9v4"></path><path d="M12 17h.01"></path><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
        </svg>
        <div class="callout-text"><strong>安全提示：</strong>若你选择保存密码，仅用于自动填充登录。建议系统环境保持可信，定期更换密码并开启 Steam 令牌等二次验证。</div>
      </div>
    </div>
  `;
  detail.querySelector("#btn-acc-edit")?.addEventListener("click", (e) => {
    const id = e.currentTarget?.dataset?.id;
    if (id) openAccountForm(id);
  });
  detail.querySelector("#btn-acc-del")?.addEventListener("click", async (e) => {
    const id = e.currentTarget?.dataset?.id;
    if (!id) return;
    if (!(await appConfirm("确定删除此账号？", { title: "删除账号", danger: true, confirmText: "删除" }))) return;
    try {
      const r = await fetchJson(API + "/accounts/" + id, { method: "DELETE" });
      if (r.ok) {
        toast("已删除");
        if (selectedAccountId === id) selectedAccountId = null;
        refreshAccounts();
      } else toast("删除失败", r.error || "");
    } catch (err) {
      toast("删除失败", err.message || "");
    }
  });
  detail.querySelector("#btn-acc-set-current")?.addEventListener("click", async (e) => {
    const id = e.currentTarget?.dataset?.id;
    if (!id) return;
    try {
      const r = await fetchJson(API + "/accounts/" + id + "/set_current", { method: "POST" });
      if (r.ok) {
        toast("已切换当前账号");
        accountsCurrentId = id;
        refreshAccounts();
      } else toast("失败", r.error || "");
    } catch (err) {
      toast("失败", err.message || "");
    }
  });
  detail.querySelector("#btn-acc-verify")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const id = btn?.dataset?.id;
    if (!id || accountVerificationsInFlight.has(id)) return;
    accountVerificationsInFlight.add(id);
    btn.disabled = true;
    btn.textContent = "验证中…";
    try {
      const r = await fetchJson(API + "/accounts/" + id + "/verify", { method: "POST" });
      if (r.ok) {
        toast(r.status === "session_valid" ? "当前登录有效" : "验证通过", r.message || "已自动登录");
        refreshAccounts();
      } else if (r.status === "need_2fa") {
        toast(r.message || "需要二次验证");
        showReloginModal("steam");
        const btnOpen = el("relogin-btn-open");
        if (btnOpen) btnOpen.click();
      } else if (r.status === "network_error") {
        toast("Steam 网络连接失败", r.message || "请检查加速器或代理设置");
      } else if (r.status === "auth_pending") {
        toast("等待登录凭证超时", r.message || "本次已停止等待，请稍后重试");
      } else if (r.status === "busy") {
        toast("登录进行中", r.message || "请等待当前登录完成");
      } else if (r.status === "account_changed") {
        toast("账号已切换", r.message || "请重新验证当前账号");
      } else {
        toast("验证未通过", r.message || "请检查账号密码");
      }
    } catch (err) {
      toast("验证失败", err.message || "");
    } finally {
      accountVerificationsInFlight.delete(id);
      btn.disabled = false;
      btn.textContent = "验证";
      const visibleButton = el("btn-acc-verify");
      if (visibleButton?.dataset?.id === id) {
        visibleButton.disabled = false;
        visibleButton.textContent = "验证";
      }
    }
  });
}
async function saveManualCookie(type, cookies) {
  const payload = { cookies };
  if (type === "buff" && typeof navigator !== "undefined") {
    payload.user_agent = navigator.userAgent || "";
  }
  const r = await fetchJson(API + "/auth/" + type + "/manual_cookie", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(r.error || "Cookie 保存失败");
  return r;
}
async function promptManualCookieLogin(type, reason = "", options = {}) {
  const isSteam = type === "steam";
  const reasonText = compactLoginError(reason);
  const defaultHint = isSteam
    ? "请从已登录的浏览器复制 Cookie 后粘贴到下方。"
    : "请从已登录且尽量与本服务使用同一固定网络出口的浏览器复制 Cookie（必须包含 session 与 csrf_token）。保存前会执行一次只读在线验证，受限会话不会强行解除熔断。";
  const message = options.message || ((reasonText ? `浏览器打开失败：${reasonText}\n\n` : "") + defaultHint);
  const exportText = options.exportText || (reason ? String(reason) : "");
  const cookie = await appPrompt(isSteam ? "手动输入 Steam Cookie" : "手动输入 Buff Cookie", "", {
    message,
    label: "Cookie",
    placeholder: isSteam
      ? "sessionid=...; steamLoginSecure=...; steamCountry=..."
      : "session=...; csrf_token=...; ...",
    type: "textarea",
    rows: 8,
    width: "620px",
    confirmText: "保存 Cookie",
    exportText,
    exportFileName: buildErrorExportName(isSteam ? "steam-login-error" : "buff-login-error"),
  });
  if (cookie === false) return false;
  const raw = String(cookie || "").trim();
  if (!raw) {
    toast("未保存", "Cookie 不能为空");
    return false;
  }
  try {
    const r = await saveManualCookie(type, raw);
    toast("Cookie 已保存", r.message || "");
    if (options.refreshAfterSave !== false) {
      if (isSteam) {
        if (typeof refreshInventory === "function") await refreshInventory(true);
        await refreshAccounts();
      } else if (typeof refreshStatus === "function") {
        await refreshStatus();
      }
    }
    return true;
  } catch (e) {
    toast("Cookie 保存失败", e.message || "");
    return false;
  }
}
async function openBrowserAndLogin() {
  if (typeof runtimeCanLaunchBrowser === "function" && !runtimeCanLaunchBrowser()) {
    const saved = await promptManualCookieLogin(reloginType, "", {
      message: typeof runtimeManualLoginMessage === "function" ? runtimeManualLoginMessage(reloginType) : "",
    });
    if (saved) hideReloginModal();
    return;
  }
  try {
    const d = await fetchJson(API + "/auth/" + reloginType + "/relogin_start", { method: "POST" });
    if (d.ok) {
      toast("已打开浏览器", d.message || "");
      const btnOk = el("relogin-btn-ok");
      if (btnOk) btnOk.disabled = false;
    } else {
      toast("打开失败", compactLoginError(d.error || ""));
      const saved = await promptManualCookieLogin(reloginType, d.error || "");
      if (saved) hideReloginModal();
    }
  } catch (e) {
    toast("请求失败", compactLoginError(e.message || ""));
    const saved = await promptManualCookieLogin(reloginType, e.message || "");
    if (saved) hideReloginModal();
  }
}
async function finishRelogin(success) {
  const btnOk = el("relogin-btn-ok");
  const btnFail = el("relogin-btn-fail");
  const btnOpen = el("relogin-btn-open");
  if (btnOk) btnOk.disabled = true;
  if (btnFail) btnFail.disabled = true;
  if (btnOpen) btnOpen.disabled = true;
  const origText = btnOk?.textContent;
  if (btnOk) btnOk.textContent = "正在更新…";
  try {
    const d = await fetchJson(API + "/auth/" + reloginType + "/relogin_finish", {
      method: "POST",
      body: JSON.stringify({ success }),
    });
    if (success && d.ok) {
      hideReloginModal();
      toast("登录信息已更新");
      if (reloginType === "steam") {
        await refreshInventory(true);
        refreshAccounts();
      } else {
        await refreshStatus();
      }
    } else if (success && !d.ok) {
      toast("更新失败", d.error || "");
    } else {
      hideReloginModal();
    }
  } catch (e) {
    if (!success) hideReloginModal();
    toast("请求失败", e.message || "");
  } finally {
    if (btnOk) {
      btnOk.disabled = false;
      btnOk.textContent = origText || "完成登录";
    }
    if (btnFail) btnFail.disabled = false;
    if (btnOpen) btnOpen.disabled = false;
  }
}
function renderAccountsUI(accs, currentId) {
  const list = el("accounts-list");
  if (!list) return;
  const term = (accountsSearchTerm || "").trim().toLowerCase();
  const filtered = term
    ? accs.filter((a) => {
      const hay = [a.display_name, a.username, a.steam_id].filter(Boolean).join(" ").toLowerCase();
      return hay.includes(term);
    })
    : accs;
  if (!selectedAccountId || !accs.some((x) => x.id === selectedAccountId)) {
    selectedAccountId = currentId || (filtered[0] ? filtered[0].id : null) || (accs[0] ? accs[0].id : null);
  }
  const header = `
    <div class="accounts-list-header">
      <div class="title">账号列表</div>
      <div class="count">${filtered.length}${term ? ` / ${accs.length}` : ""}</div>
    </div>
  `;
  if (!filtered.length) {
    list.innerHTML = header + `<div style="padding:18px" class="text-muted">未找到匹配账号</div>`;
    renderAccountDetail(null, currentId);
    return;
  }
  const items = filtered
    .map((a) => {
      const name = a.display_name || a.username || a.steam_id || "未命名";
      const currency = (a.currency_code || "").toUpperCase();
      const region = (a.region_code || "").toUpperCase();
      const extras = [];
      if (currency) extras.push(currency);
      if (region) extras.push(region);
      const subMain = [a.username, a.steam_id].filter(Boolean).join(" · ") || "—";
      const sub = extras.length ? `${subMain} · ${extras.join(" / ")}` : subMain;
      const isCurrent = a.id === currentId;
      const active = a.id === selectedAccountId;
      const avatar = buildAccountAvatar(name, a.avatar_url, 40);
      return `
        <div class="account-item ${active ? "active" : ""}" data-id="${escapeHtml(a.id)}" role="button" tabindex="0">
          ${avatar}
          <div class="account-item-body">
            <div class="account-item-title">${escapeHtml(name)} ${isCurrent ? '<span class="badge badge-current">当前</span>' : ""}</div>
            <div class="account-item-sub">${escapeHtml(sub)}</div>
          </div>
        </div>
      `;
    })
    .join("");
  list.innerHTML = header + items;
  list.querySelectorAll(".account-item").forEach((node) => {
    const activate = () => {
      const id = node.dataset.id;
      if (!id) return;
      selectedAccountId = id;
      renderAccountsUI(accs, currentId);
    };
    node.addEventListener("click", activate);
    node.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        activate();
      }
    });
  });
  renderAccountDetail(accs.find((x) => x.id === selectedAccountId) || filtered[0], currentId);
}
async function refreshAccounts() {
  const list = el("accounts-list");
  if (!list) return;
  try {
    const d = await fetchJson(API + "/accounts");
    accountsCache = d.accounts || [];
    accountsCurrentId = d.current_id || null;
    // 同步账号存在标志，用于控制登录过期弹窗是否显示
    if (typeof _hasAnyAccount !== "undefined") {
      _hasAnyAccount = accountsCache.length > 0;
    }
    renderAccountsUI(accountsCache, accountsCurrentId);
  } catch (e) {
    toast("加载失败", e.message || "");
    list.innerHTML = '<div style="padding:18px" class="text-muted">加载失败</div>';
    renderAccountDetail(null, null);
  }
}

function openAccountForm(editId = null) {
  accountEditId = editId;
  const title = el("account-form-title");
  const un = el("acc-username");
  const pw = el("acc-password");
  const sid = el("acc-steam-id");
  const dn = el("acc-display-name");
  if (title) title.textContent = editId ? "编辑账号" : "添加账号";
  if (un) un.value = "";
  if (pw) pw.value = "";
  if (sid) sid.value = "";
  if (dn) dn.value = "";
  if (editId) {
    const accs = [];
    fetchJson(API + "/accounts").then((d) => {
      const a = (d.accounts || []).find((x) => x.id === editId);
      if (a) {
        if (un) un.value = a.username || "";
        if (pw) pw.placeholder = "已保存，留空不修改";
        if (sid) sid.value = a.steam_id || "";
        if (dn) dn.value = a.display_name || "";
      }
    }).catch(() => { });
  } else if (pw) pw.placeholder = "保存后仅用于自动填充";
  const ov = el("account-form-overlay");
  if (ov) ov.classList.remove("hidden");
}
function closeAccountForm() {
  accountEditId = null;
  const ov = el("account-form-overlay");
  if (ov) ov.classList.add("hidden");
}
async function saveAccountForm() {
  const un = (el("acc-username")?.value || "").trim();
  const pw = (el("acc-password")?.value || "").trim();
  const sid = (el("acc-steam-id")?.value || "").trim();
  const dn = (el("acc-display-name")?.value || "").trim();
  try {
    if (accountEditId) {
      const body = { username: un, steam_id: sid, display_name: dn };
      if (pw) body.password = pw;
      const r = await fetchJson(API + "/accounts/" + accountEditId, {
        method: "PUT",
        body: JSON.stringify(body),
      });
      if (r.ok) { toast("已保存"); closeAccountForm(); refreshAccounts(); }
      else toast("保存失败", r.error || "");
    } else {
      const r = await fetchJson(API + "/accounts", {
        method: "POST",
        body: JSON.stringify({ username: un, password: pw, steam_id: sid, display_name: dn, avatar_url: "" }),
      });
      if (r.ok) { toast("已添加"); closeAccountForm(); refreshAccounts(); }
      else toast("添加失败", r.error || "");
    }
  } catch (e) {
    toast("保存失败", e.message || "");
  }
}
