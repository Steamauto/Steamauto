/**
 * AetherSwap + Steamauto 融合交易与全自动收发货控制台交互模块
 */

let _deliveryRefreshTimer = null;

async function fetchDeliveryStatus() {
  try {
    const res = await fetch("/api/delivery/status");
    if (!res.ok) return null;
    const data = await res.json();
    return data.status || null;
  } catch (e) {
    console.error("fetchDeliveryStatus error:", e);
    return null;
  }
}

async function fetchDeliverySettings() {
  try {
    const res = await fetch("/api/delivery/settings");
    if (!res.ok) return null;
    const data = await res.json();
    return data.delivery || null;
  } catch (e) {
    console.error("fetchDeliverySettings error:", e);
    return null;
  }
}

async function fetchDeliveryOrders(platform = null) {
  try {
    const url = platform && platform !== "all" 
      ? `/api/delivery/orders?limit=100&platform=${encodeURIComponent(platform)}`
      : `/api/delivery/orders?limit=100`;
    const res = await fetch(url);
    if (!res.ok) return [];
    const data = await res.json();
    return data.orders || [];
  } catch (e) {
    console.error("fetchDeliveryOrders error:", e);
    return [];
  }
}

function renderDeliveryStatusCards(status) {
  if (!status) return;

  // 1. Worker 守护线程状态
  const workerBadge = document.getElementById("delivery-worker-status-badge");
  if (workerBadge) {
    if (status.running) {
      workerBadge.className = "badge badge-success";
      workerBadge.innerText = "运行中";
    } else {
      workerBadge.className = "badge badge-secondary";
      workerBadge.innerText = "已停止";
    }
  }

  // 2. Steam 2FA 待签署数
  const confirmsBadge = document.getElementById("delivery-confirms-count");
  if (confirmsBadge) {
    confirmsBadge.innerText = status.pending_confirms_count || "0";
  }

  // 3. 平台状态指示
  const buffStatusEl = document.getElementById("delivery-buff-status-text");
  if (buffStatusEl) {
    buffStatusEl.innerText = status.buff_status || "空闲";
  }
  const uuStatusEl = document.getElementById("delivery-uu-status-text");
  if (uuStatusEl) {
    uuStatusEl.innerText = status.uu_status || "空闲";
  }
  const steamStatusEl = document.getElementById("delivery-steam-status-text");
  if (steamStatusEl) {
    steamStatusEl.innerText = status.steam_status || "空闲";
  }

  // 4. 最近一次轮询时间
  const lastCheckEl = document.getElementById("delivery-last-check-text");
  if (lastCheckEl) {
    if (status.last_check_at) {
      const dt = new Date(status.last_check_at * 1000);
      lastCheckEl.innerText = dt.toLocaleTimeString();
    } else {
      lastCheckEl.innerText = "尚未运行";
    }
  }
}

function renderDeliverySettings(settings) {
  if (!settings) return;

  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val !== undefined && val !== null ? val : "";
  };
  const setChecked = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.checked = Boolean(val);
  };

  setChecked("delivery-cfg-enabled", settings.enabled !== false);
  setChecked("delivery-cfg-buff-ship", settings.buff_auto_ship !== false);
  setChecked("delivery-cfg-buff-accept", settings.buff_auto_accept !== false);
  setChecked("delivery-cfg-uu-ship", settings.uu_auto_ship !== false);
  setChecked("delivery-cfg-uu-accept", settings.uu_auto_accept !== false);
  setChecked("delivery-cfg-uu-lease", settings.uu_auto_lease !== false);
  setChecked("delivery-cfg-steam-gifts", settings.steam_auto_accept_gifts !== false);

  setVal("delivery-cfg-poll-interval", settings.poll_interval_seconds || 15);
  setVal("delivery-cfg-uu-token", settings.uu_token || "");
  setVal("delivery-cfg-c5-key", settings.c5_app_key || "");
  setVal("delivery-cfg-c5-secret", settings.c5_app_secret || "");
  setVal("delivery-cfg-ecosteam-id", settings.ecosteam_partner_id || "");
  setVal("delivery-cfg-ecosteam-key", settings.ecosteam_api_key || "");
}

function renderDeliveryOrdersTable(orders) {
  const tbody = document.getElementById("delivery-orders-tbody");
  if (!tbody) return;

  if (!orders || orders.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding: 24px; color: var(--text-muted, #888);">暂无交易记录</td></tr>`;
    return;
  }

  tbody.innerHTML = orders.map((o) => {
    const platform = (o.platform || "steam").toUpperCase();
    let badgeClass = "badge-secondary";
    if (platform === "BUFF") badgeClass = "badge-primary";
    else if (platform === "UU") badgeClass = "badge-warning";
    else if (platform === "STEAM") badgeClass = "badge-info";

    let actionText = "发货";
    if (o.action === "receive") actionText = "自动收货";
    else if (o.action === "confirm_2fa") actionText = "2FA 移动端确认";
    else if (o.action === "gift") actionText = "接受礼物";
    else if (o.action === "ship") actionText = "自动发货";

    const dt = o.created_at ? new Date(o.created_at * 1000).toLocaleString() : "--";
    const statusText = o.status === "accepted" || o.status === "confirmed" ? "已完成" : (o.status || "处理中");
    const statusBadge = o.status === "accepted" || o.status === "confirmed" ? "color: var(--success, #10b981);" : "color: var(--warning, #f59e0b);";

    return `
      <tr>
        <td><span class="badge ${badgeClass}">${platform}</span></td>
        <td><strong>${actionText}</strong></td>
        <td><span title="${escapeHtml(o.item_name || '')}">${escapeHtml(o.item_name || '未知饰品')}</span></td>
        <td><code>${escapeHtml(o.trade_offer_id || o.order_id || '--')}</code></td>
        <td>${o.price ? '¥' + Number(o.price).toFixed(2) : '--'}</td>
        <td><span style="${statusBadge}">${statusText}</span></td>
        <td style="color: var(--text-muted, #888); font-size: 0.85em;">${dt}</td>
      </tr>
    `;
  }).join("");
}

async function saveDeliverySettings() {
  const btn = document.getElementById("btn-delivery-save-settings");
  if (btn) btn.disabled = true;

  try {
    const payload = {
      enabled: document.getElementById("delivery-cfg-enabled")?.checked,
      poll_interval_seconds: parseInt(document.getElementById("delivery-cfg-poll-interval")?.value || "15", 10),
      buff_auto_ship: document.getElementById("delivery-cfg-buff-ship")?.checked,
      buff_auto_accept: document.getElementById("delivery-cfg-buff-accept")?.checked,
      uu_auto_ship: document.getElementById("delivery-cfg-uu-ship")?.checked,
      uu_auto_accept: document.getElementById("delivery-cfg-uu-accept")?.checked,
      uu_auto_lease: document.getElementById("delivery-cfg-uu-lease")?.checked,
      steam_auto_accept_gifts: document.getElementById("delivery-cfg-steam-gifts")?.checked,
      uu_token: document.getElementById("delivery-cfg-uu-token")?.value?.trim() || "",
      c5_app_key: document.getElementById("delivery-cfg-c5-key")?.value?.trim() || "",
      c5_app_secret: document.getElementById("delivery-cfg-c5-secret")?.value?.trim() || "",
      ecosteam_partner_id: document.getElementById("delivery-cfg-ecosteam-id")?.value?.trim() || "",
      ecosteam_api_key: document.getElementById("delivery-cfg-ecosteam-key")?.value?.trim() || "",
    };

    const res = await fetch("/api/delivery/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res.ok) {
      if (typeof showToast === "function") {
        showToast("收发货配置已成功保存！", "success");
      } else {
        alert("收发货配置已成功保存！");
      }
    } else {
      throw new Error(`HTTP ${res.status}`);
    }
  } catch (e) {
    console.error("saveDeliverySettings failed:", e);
    if (typeof showToast === "function") {
      showToast("保存配置失败: " + e.message, "error");
    } else {
      alert("保存配置失败: " + e.message);
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function triggerManual2FAConfirm() {
  const btn = document.getElementById("btn-delivery-confirm-now");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch("/api/delivery/confirm-now", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      const msg = `已完成 2FA 移动端扫描，共签署了 ${data.confirmed_count} 笔确认！`;
      if (typeof showToast === "function") showToast(msg, "success");
      else alert(msg);
      refreshDeliveryPanel();
    } else {
      throw new Error(data.error || "签署失败");
    }
  } catch (e) {
    console.error("triggerManual2FAConfirm failed:", e);
    if (typeof showToast === "function") showToast("2FA确认失败: " + e.message, "error");
    else alert("2FA确认失败: " + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function triggerManualPoll() {
  const btn = document.getElementById("btn-delivery-poll-now");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch("/api/delivery/trigger-poll", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      if (typeof showToast === "function") showToast("已触发全平台收发货与2FA轮询", "info");
      setTimeout(refreshDeliveryPanel, 1500);
    }
  } catch (e) {
    console.error("triggerManualPoll failed:", e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function refreshDeliveryPanel() {
  const [status, settings, orders] = await Promise.all([
    fetchDeliveryStatus(),
    fetchDeliverySettings(),
    fetchDeliveryOrders(document.getElementById("delivery-platform-filter")?.value || "all"),
  ]);

  renderDeliveryStatusCards(status);
  renderDeliverySettings(settings);
  renderDeliveryOrdersTable(orders);
}

function initDeliveryPanel() {
  refreshDeliveryPanel();

  // 绑定事件
  const btnSave = document.getElementById("btn-delivery-save-settings");
  if (btnSave && !btnSave._bound) {
    btnSave.addEventListener("click", saveDeliverySettings);
    btnSave._bound = true;
  }

  const btnConfirm = document.getElementById("btn-delivery-confirm-now");
  if (btnConfirm && !btnConfirm._bound) {
    btnConfirm.addEventListener("click", triggerManual2FAConfirm);
    btnConfirm._bound = true;
  }

  const btnPoll = document.getElementById("btn-delivery-poll-now");
  if (btnPoll && !btnPoll._bound) {
    btnPoll.addEventListener("click", triggerManualPoll);
    btnPoll._bound = true;
  }

  const filterSel = document.getElementById("delivery-platform-filter");
  if (filterSel && !filterSel._bound) {
    filterSel.addEventListener("change", async () => {
      const orders = await fetchDeliveryOrders(filterSel.value);
      renderDeliveryOrdersTable(orders);
    });
    filterSel._bound = true;
  }

  const btnRefreshOrders = document.getElementById("btn-delivery-refresh-orders");
  if (btnRefreshOrders && !btnRefreshOrders._bound) {
    btnRefreshOrders.addEventListener("click", async () => {
      const orders = await fetchDeliveryOrders(filterSel?.value || "all");
      renderDeliveryOrdersTable(orders);
    });
    btnRefreshOrders._bound = true;
  }

  if (!_deliveryRefreshTimer) {
    _deliveryRefreshTimer = setInterval(() => {
      const panel = document.getElementById("panel-delivery");
      if (panel && panel.classList.contains("active")) {
        fetchDeliveryStatus().then(renderDeliveryStatusCards);
      }
    }, 10000);
  }
}

window.initDeliveryPanel = initDeliveryPanel;
window.refreshDeliveryPanel = refreshDeliveryPanel;
