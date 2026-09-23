
function formatTimeHHMM(d = new Date()) {
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}
async function tabSwitch(name) {
  console.log("tabSwitch called with name:", name);
  const activePanel = document.querySelector(".panel.active");
  if (activePanel?.id === "panel-strategies" && name !== "strategies" && typeof window.strategyConfirmLeave === "function") {
    const ok = await window.strategyConfirmLeave();
    if (!ok) return false;
  }
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
  const panel = el("panel-" + name);
  const btn = document.querySelector(`.nav-menu [data-tab="${name}"]`);
  console.log("tabSwitch found panel:", panel, "btn:", btn);
  if (panel) {
    panel.classList.add("active");
    panel.style.animation = 'none';
    panel.offsetHeight;
    panel.style.animation = '';
  }
  if (btn) {
    btn.classList.add("active");
    const text = btn.querySelector("span")?.innerText;
    if (text && el("page-title-display")) el("page-title-display").innerText = text;
  }
  if (name === "debug") refreshLog();
  if (name === "inventory") refreshInventory(false);
  if (name === "purchases" || name === "sales" || name === "purchase-history") refreshTransactions();
  if (name === "analytics") refreshAnalytics();
  if (name === "accounts") refreshAccounts();
  if (name === "steam-guard") initSteamGuardPanel();
  if (name !== "steam-guard") stopSteamGuardTimer();
  if (name === "proxy") {
    loadProxyConfig();
  }
  if (name === "strategies" && typeof loadStrategies === "function") {
    loadStrategies();
  }
  if (name === "delivery" && typeof initDeliveryPanel === "function") {
    initDeliveryPanel();
  }
  return true;
}

let lastStatus = "idle";
let _lastLyricText = "";
// 是否已配置账号的缓存标志（避免 popup 在未配置时误弹出）
let _hasAnyAccount = false;

function pushLyricLine(text, subText) {
  if (text === _lastLyricText) return;
  _lastLyricText = text;
  const track = el("step-scroll-track");
  if (!track) return;
  const LINE_H = 24;
  const MAX_LINES = 10;
  const oldSub = track.querySelector(".lyric-sub");
  if (oldSub) oldSub.remove();
  const oldLines = track.querySelectorAll(".lyric-line");
  oldLines.forEach((l, i) => {
    const dist = oldLines.length - i;
    let op;
    if (dist === 1) op = 0.35;
    else if (dist === 2) op = 0.15;
    else op = 0.05;
    l.style.transition = "opacity 0.6s ease";
    l.style.opacity = String(op);
  });
  const line = document.createElement("div");
  line.className = "lyric-line";
  line.textContent = text;
  line.style.opacity = "0";
  line.style.transition = "none";
  track.appendChild(line);
  if (subText) {
    const sub = document.createElement("div");
    sub.className = "lyric-line lyric-sub";
    sub.textContent = subText;
    sub.style.opacity = "0";
    sub.style.transition = "none";
    track.appendChild(sub);
  }
  track.style.transition = "none";
  track.style.transform = `translateY(${LINE_H}px)`;
  void track.offsetHeight;
  track.style.transition = "";
  track.style.transform = "translateY(0)";
  requestAnimationFrame(() => {
    line.style.transition = "opacity 0.45s cubic-bezier(0.22, 1, 0.36, 1) 0.12s";
    line.style.opacity = "1";
    const newSub = track.querySelector(".lyric-sub");
    if (newSub) {
      newSub.style.transition = "opacity 0.45s cubic-bezier(0.22, 1, 0.36, 1) 0.2s";
      newSub.style.opacity = "0.3";
    }
  });
  while (track.querySelectorAll(".lyric-line:not(.lyric-sub)").length > MAX_LINES) {
    track.removeChild(track.firstChild);
  }
  const mainCount = track.querySelectorAll(".lyric-line:not(.lyric-sub)").length;
  if (mainCount > 1) {
    const vp = el("step-scroll-viewport");
    if (vp) vp.style.height = subText ? "72px" : "48px";
  }
}
async function refreshStatus() {
  try {
    const d = await fetchJson(API + "/status");
    if (d.buff_verification_required && _hasAnyAccount) {
      showReloginModal("buff", { reason: "verification_required", error: d.buff_verification_reason });
    } else if (d.buff_auth_expired && _hasAnyAccount) {
      showReloginModal("buff");
    }
    const raw = d.status || "idle";
    const statusText = raw === "running" ? "运行中" : raw === "error" ? "错误" : raw === "stopped" ? "已停止" : "空闲中";
    const top = el("status-text");
    if (top) top.textContent = statusText;
    const inline = el("status-text-inline");
    if (inline) inline.textContent = statusText;
    const stepDesc = d.step || "";
    const item = d.progress_item || "";
    const newText = stepDesc && item ? `${stepDesc}：${item}` : (stepDesc || item || "—");
    const nextItem = d.next_progress_item || "";
    const subText = stepDesc && nextItem ? `${stepDesc}：${nextItem}` : nextItem;
    pushLyricLine(newText, subText);
    const pill = el("status-pill");
    if (pill) {
      pill.classList.remove("status-idle", "status-running", "status-stopped", "status-error");
      pill.classList.add(raw === "running" ? "status-running" : raw === "error" ? "status-error" : raw === "stopped" ? "status-stopped" : "status-idle");
    }
    const controlBar = document.querySelector(".control-bar");
    if (controlBar) {
      if (raw === "running") controlBar.classList.add("running");
      else controlBar.classList.remove("running");
    }
    const lu = el("last-updated");
    if (lu) lu.textContent = formatTimeHHMM();
    if (raw !== lastStatus) {
      if (raw === "running") toast("已开始运行");
      if (raw === "stopped") toast("已停止");
      if (raw === "error") toast("发生错误", d.step || "请看调试日志");
      lastStatus = raw;
    }
  } catch {
  }
  try {
    const d = await fetchJson(API + "/pending_payment");
    const p = d.pending;
    const box = el("pending-payment");
    if (!box) return;
    if (p && p.pay_url) {
      box.classList.remove("hidden");
      const link = el("pay-link");
      if (link) {
        link.href = p.pay_url;
        link.textContent = p.pay_type === "wechat" ? "微信支付链接（可复制到浏览器）" : "打开支付链接";
      }
      const t = el("pay-type");
      if (t) t.textContent = p.name ? "订单: " + p.name : "";
      box.dataset.payUrl = p.pay_url;
      const qrWrap = el("pay-qrcode-wrap");
      const qrBox = el("pay-qrcode");
      if (p.pay_type === "wechat" && qrWrap && qrBox && typeof QRCode !== "undefined") {
        qrWrap.classList.remove("hidden");
        qrBox.innerHTML = "";
        try {
          new QRCode(qrBox, { text: p.pay_url, width: 200, height: 200 });
        } catch (e) {
          qrWrap.classList.add("hidden");
        }
      } else {
        if (qrWrap) qrWrap.classList.add("hidden");
        if (qrBox) qrBox.innerHTML = "";
      }
    } else {
      box.classList.add("hidden");
      box.dataset.payUrl = "";
      const qrWrap = el("pay-qrcode-wrap");
      const qrBox = el("pay-qrcode");
      if (qrWrap) qrWrap.classList.add("hidden");
      if (qrBox) qrBox.innerHTML = "";
    }
  } catch {
  }
  try {
    const s = await fetchJson(API + "/stats");
    const set = (id, text) => {
      const e = el(id);
      if (e) e.textContent = text;
    };
    animateValue(el("stat-total-purchased"), s.total_purchased ?? 0);
    animateValue(el("stat-total-sold"), s.total_sold ?? 0);
    const diffEl = el("stat-diff");
    if (s.total_profit != null) {
      animateValue(diffEl, s.total_profit);
      diffEl.classList.remove("text-ok", "text-bad");
      if (s.total_profit > 0) diffEl.classList.add("text-ok");
      else if (s.total_profit < 0) diffEl.classList.add("text-bad");
    } else set("stat-diff", "—");
    const ratioEl = el("stat-ratio");
    if (s.discount_ratio != null) {
      animateValue(ratioEl, s.discount_ratio, 400);
      const targetRatio = 0.85;
      ratioEl.classList.remove("text-ok", "text-bad");
      if (s.discount_ratio <= targetRatio) ratioEl.classList.add("text-ok");
      else ratioEl.classList.add("text-bad");
    } else set("stat-ratio", "—");
  } catch {
  }
}
let reloginType = "steam";
let inventoryRefreshInFlight = false;
function showReloginModal(type, opts = {}) {
  reloginType = type || "steam";
  const overlay = el("relogin-overlay");
  if (overlay) overlay.classList.remove("hidden");
  const title = el("relogin-title");
  const msg = el("relogin-message");
  if (reloginType === "buff") {
    if (opts.reason === "verification_required") {
      if (title) title.textContent = "Buff 需要验证";
      if (msg) msg.textContent = opts.error
        ? `Buff 返回：${compactErrorText(opts.error, 180)}。请打开内置浏览器，进入 Buff 市场或任意商品页，完成刷新/人机验证后点击完成。`
        : "Buff 需要刷新页面状态或完成人机验证。请打开内置浏览器，进入 Buff 市场或任意商品页，完成验证后点击完成。";
    } else {
      if (title) title.textContent = "Buff 登录已过期";
      if (msg) msg.textContent = "登录已过期，请按当前运行环境打开浏览器登录 Buff，或手动填写 Cookie 后继续。";
    }
  } else {
    if (title) title.textContent = "Steam 登录已过期";
    if (opts.reason === "need_2fa") {
      if (msg) msg.textContent = "需要二次验证（验证码），请点击下方按钮打开浏览器并完成 Steam 登录。";
    } else if (opts.error) {
      if (msg) msg.textContent = compactErrorText(opts.error, 220);
    } else {
      if (msg) msg.textContent = "登录已过期，请按当前运行环境打开浏览器登录 Steam，或手动填写 Cookie 后继续。";
    }
  }
  const btnOk = el("relogin-btn-ok");
  if (btnOk) btnOk.disabled = false;
  if (typeof runtimeCanLaunchBrowser === "function" && !runtimeCanLaunchBrowser()) {
    if (msg && typeof runtimeManualLoginMessage === "function") msg.textContent = runtimeManualLoginMessage(reloginType);
    if (btnOk) btnOk.disabled = true;
  }
  if (typeof applyRuntimeUiHints === "function") applyRuntimeUiHints();
}
function hideReloginModal() {
  const overlay = el("relogin-overlay");
  if (overlay) overlay.classList.add("hidden");
}
async function refreshInventory(forceRefresh = true) {
  if (inventoryRefreshInFlight) return;
  inventoryRefreshInFlight = true;
  const dataStatus = el("inv-data-status");
  if (dataStatus) {
    dataStatus.textContent = "正在读取库存…";
    dataStatus.title = "";
  }
  try {
    const d = await fetchJson(API + "/inventory" + (forceRefresh ? "?refresh=1" : ""));
    if (dataStatus) {
      const authLabels = {
        auth_pending: "登录凭证等待超时",
        busy: "登录进行中",
        network_error: "会话状态暂无法确认",
        account_changed: "账号已切换",
      };
      const sourceLabel = d.source === "live" ? "实时库存" : "缓存库存";
      const detail = authLabels[d.auth_status] || (d.auth_expired ? "登录已过期" : d.error ? "刷新失败" : "");
      dataStatus.textContent = detail ? `${sourceLabel} · ${detail}` : sourceLabel;
      dataStatus.title = d.error || "";
    }
    if (d.auth_expired && _hasAnyAccount) {
      showReloginModal("steam", { reason: d.auth_expired_reason, error: d.error });
      return;
    }
    const items = d.items || [];
    const tbody = document.querySelector("#inv-table tbody");
    if (!tbody) return;
    let totalValue = 0;
    const rowHtmls = [];
    for (const it of items) {
      const sellHtml =
        `<span class="${it.can_sell ? "text-ok" : "text-bad"}">${it.can_sell ? "是" : "否"}</span>` +
        (it.marketable ? "" : ' <span class="text-bad">(不可上架)</span>');
      const tradeHtml =
        `<span class="${it.can_trade ? "text-ok" : "text-bad"}">${it.can_trade ? "是" : "否"}</span>` +
        (it.tradable ? "" : ' <span class="text-bad">(不可交易)</span>');
      // Also extract timestamps from inventory cached before the backend parser fix.
      const dateMatch = (it.cooldown_text || "").match(/\[date\](\d+)\[\/date\]/);
      const cooldownSeconds = Number(it.cooldown_at || (dateMatch && dateMatch[1]));
      const rawTime = it.cooldown_at_iso || (cooldownSeconds > 0 ? cooldownSeconds * 1000 : null);
      let displayTime = "";
      if (rawTime !== null) {
        const d = new Date(rawTime);
        if (!isNaN(d.getTime())) {
          const pad = (value) => String(value).padStart(2, "0");
          displayTime = `${String(d.getFullYear()).padStart(4, "0")}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
        }
      }
      const timeHtml = displayTime ? `<span class="text-bad">${escapeHtml(displayTime)}</span>` : "—";
      const lowest = Number(it.lowest_price) || 0;
      totalValue += lowest;
      const lowestStr = lowest > 0 ? lowest.toFixed(2) : "—";
      const mhn = (it.market_hash_name || it.name || "").trim();
      const steamUrl = mhn
        ? "https://steamcommunity.com/market/listings/730/" + encodeURIComponent(mhn)
        : "";
      const buffUrl = mhn
        ? "https://buff.163.com/market/csgo?tab=selling&search=" + encodeURIComponent(mhn)
        : "";
      const linksHtml = mhn
        ? `<a href="${steamUrl}" target="_blank" rel="noopener" class="link-steam">Steam</a> <a href="${buffUrl}" target="_blank" rel="noopener" class="link-buff">Buff</a>`
        : "—";
      rowHtmls.push(`
        <tr><td>${escapeHtml(it.name || "")}</td>
        <td class="inv-links">${linksHtml}</td>
        <td>${sellHtml}</td>
        <td>${tradeHtml}</td>
        <td>${timeHtml}</td>
        <td class="mono">${escapeHtml(lowestStr)}</td>
        <td class="muted small">${escapeHtml(it.cooldown_text || "")}</td></tr>
      `);
    }
    tbody.innerHTML = rowHtmls.join("");
    const c = el("inv-count");
    if (c) c.textContent = items.length;
    const v = el("inv-total-value");
    if (v) v.textContent = totalValue.toFixed(2);
    const taxEl = el("inv-tax-value");
    if (taxEl) taxEl.textContent = (totalValue / 1.15).toFixed(2);
  } catch (e) {
    if (dataStatus) {
      dataStatus.textContent = "刷新失败 · 保留原有库存";
      dataStatus.title = e.message || "";
    }
    toast("刷新库存失败", e.message || "请检查 Steam Cookie");
  } finally {
    inventoryRefreshInFlight = false;
  }
}
async function refreshMarketPrices() {
  try {
    const d = await fetchJson(API + "/market-prices");
    const prices = d.prices || {};
    if (Object.keys(prices).length === 0) return;
    const invItems = getInventoryCache();
    if (invItems && invItems.length > 0) {
      let totalValue = 0;
      const tbody = document.querySelector("#inv-table tbody");
      if (tbody) {
        const rows = tbody.querySelectorAll("tr");
        rows.forEach((row) => {
          const nameTd = row.querySelector("td:first-child");
          const priceTd = row.querySelectorAll("td")[5];
          if (!nameTd || !priceTd) return;
          const name = nameTd.textContent.trim();
          const price = prices[name];
          if (price != null) {
            priceTd.textContent = price.toFixed(2);
          }
        });
        rows.forEach((row) => {
          const priceTd = row.querySelectorAll("td")[5];
          const v = parseFloat(priceTd?.textContent);
          if (!isNaN(v)) totalValue += v;
        });
        const v = el("inv-total-value");
        if (v) v.textContent = totalValue.toFixed(2);
        const taxEl = el("inv-tax-value");
        if (taxEl) taxEl.textContent = (totalValue / 1.15).toFixed(2);
      }
    }
    if (!lastEnrichData) {
      await refreshTransactions();
    }
    if (lastEnrichData && lastEnrichData.length > 0) {
      for (const t of lastEnrichData) {
        if (t.type !== "purchase" || t.sale_price != null) continue;
        const p = prices[t.name];
        if (p != null) t.current_market_price = p;
      }
      lastEnrichTime = Date.now();
      refreshTransactions();
    }
  } catch (e) {
  }
}
function getInventoryCache() {
  const tbody = document.querySelector("#inv-table tbody");
  if (!tbody) return [];
  return Array.from(tbody.querySelectorAll("tr")).map((row) => {
    const tds = row.querySelectorAll("td");
    return { name: tds[0]?.textContent.trim() || "" };
  });
}
function aggregateByItemName(purchases, resellRatio = 0.85) {
  const ratio = Math.max(0.01, Math.min(1, Number(resellRatio) || 0.85));
  const byName = new Map();
  for (const t of purchases) {
    const name = (t.name || "—").toString();
    if (!byName.has(name)) {
      byName.set(name, {
        name,
        count: 0,
        totalPrice: 0,
        totalMp: 0,
        mpCount: 0,
        soldCount: 0,
        totalSalePrice: 0,
        totalDiscountRatio: 0,
        totalCashProfit: 0,
        totalDeviation: 0,
        totalSoldMp: 0,
        deviationCount: 0,
      });
    }
    const r = byName.get(name);
    r.count += 1;
    r.totalPrice += Number(t.price) || 0;
    if (t.market_price != null) {
      r.totalMp += Number(t.market_price);
      r.mpCount += 1;
    }
    const sold = t.sale_price != null && Number(t.sale_price) > 0;
    if (sold) {
      const saleP = Number(t.sale_price);
      const cost = Number(t.price) || 0;
      const mp = t.market_price != null ? Number(t.market_price) : 0;
      const afterTax = saleP / 1.15;
      r.soldCount += 1;
      r.totalSalePrice += saleP;
      if (afterTax > 0 && cost > 0) r.totalDiscountRatio += cost / afterTax;
      r.totalCashProfit += afterTax * ratio - cost;
      if (mp > 0) {
        r.totalDeviation += saleP - mp;
        r.totalSoldMp += mp;
        r.deviationCount += 1;
      }
    }
  }
  return Array.from(byName.values())
    .map((r) => ({
      name: r.name,
      count: r.count,
      avgPrice: r.count > 0 ? r.totalPrice / r.count : 0,
      avgMp: r.mpCount > 0 ? r.totalMp / r.mpCount : null,
      totalSaleAmount: r.soldCount > 0 ? r.totalSalePrice : null,
      avgSalePrice: r.soldCount > 0 ? r.totalSalePrice / r.soldCount : null,
      avgDiscountRatio: r.soldCount > 0 && r.totalDiscountRatio > 0 ? r.totalDiscountRatio / r.soldCount : null,
      totalCashProfit: r.soldCount > 0 ? r.totalCashProfit : null,
      avgDeviation: r.deviationCount > 0 ? r.totalDeviation / r.deviationCount : null,
      avgDeviationPct: r.deviationCount > 0 && r.totalSoldMp > 0 ? (r.totalDeviation / r.totalSoldMp) * 100 : null,
    }))
    .sort((a, b) => b.count - a.count);
}
function refreshAnalytics(purchases, resellRatio) {
  const tbody = document.querySelector("#analytics-table tbody");
  if (!tbody) return;
  const render = (list, ratio) => {
    const rows = aggregateByItemName(list, ratio);
    const rowHtmls = rows.map((r) => {
      const avgPriceStr = r.avgPrice > 0 ? r.avgPrice.toFixed(2) : "—";
      const avgMpStr = r.avgMp != null ? r.avgMp.toFixed(2) : "—";
      const totalSaleStr = r.totalSaleAmount != null ? r.totalSaleAmount.toFixed(2) : "—";
      const avgSaleStr = r.avgSalePrice != null ? r.avgSalePrice.toFixed(2) : "—";
      const avgDiscountStr = r.avgDiscountRatio != null ? r.avgDiscountRatio.toFixed(4) : "—";
      const discountRatioClass = avgDiscountStr !== "—" ? (parseFloat(avgDiscountStr) > ratio ? "text-bad" : "text-ok") : "";
      const cashProfitStr = r.totalCashProfit != null ? (r.totalCashProfit >= 0 ? "+" : "") + r.totalCashProfit.toFixed(2) : "—";
      const cashClass = r.totalCashProfit != null ? (r.totalCashProfit > 0 ? "text-ok" : r.totalCashProfit < 0 ? "text-bad" : "") : "";
      const avgDevPctStr = r.avgDeviationPct != null ? (r.avgDeviationPct >= 0 ? "+" : "") + r.avgDeviationPct.toFixed(2) + "%" : "";
      const avgDevStr = r.avgDeviation != null ? (r.avgDeviation >= 0 ? "+" : "") + r.avgDeviation.toFixed(2) + (avgDevPctStr ? " (" + avgDevPctStr + ")" : "") : "—";
      const devClass = r.avgDeviation != null ? (r.avgDeviation > 0 ? "text-ok" : r.avgDeviation < 0 ? "text-bad" : "") : "";
      return `<tr><td>${escapeHtml(r.name)}</td><td class="mono">${r.count}</td><td class="mono">${avgPriceStr}</td><td class="mono">${avgMpStr}</td><td class="mono">${totalSaleStr}</td><td class="mono">${avgSaleStr}</td><td class="mono ${discountRatioClass}">${avgDiscountStr}</td><td class="mono ${cashClass}">${cashProfitStr}</td><td class="mono ${devClass}">${avgDevStr}</td></tr>`;
    });
    tbody.innerHTML = rowHtmls.length ? rowHtmls.join("") : "<tr><td colspan='9' class='text-muted'>暂无数据</td></tr>";
  };
  if (purchases != null && resellRatio != null) {
    render(purchases, resellRatio);
  } else {
    fetchJson(API + "/transactions?enrich_current_price=0")
      .then((d) => {
        const list = (d.transactions || []).filter((t) => t.type === "purchase");
        const ratio = Math.max(0.01, Math.min(1, Number(d.resell_ratio) || 0.85));
        render(list, ratio);
      })
      .catch((e) => {
        toast("加载数据分析失败", e.message || "");
        tbody.innerHTML = "<tr><td colspan='9' class='text-muted'>加载失败</td></tr>";
      });
  }
}
async function copyPayLink() {
  const box = el("pending-payment");
  const url = box?.dataset.payUrl;
  if (!url) return;
  try {
    await navigator.clipboard.writeText(url);
    toast("已复制链接");
  } catch {
    const ta = document.createElement("textarea");
    ta.value = url;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    toast("已复制链接");
  }
}
function bindEvents() {
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => tabSwitch(btn.dataset.tab));
  });
  el("btn-quit")?.addEventListener("click", async () => {
    if (await appConfirm("确定要退出程序吗？退出后后台所有的交易、保活和查询任务都将停止。", { title: "退出程序", danger: true, confirmText: "退出" })) {
      fetchJson(API + "/system/shutdown", { method: "POST" })
        .then(() => {
          toast("程序正在退出", "您可以安全地直接关闭此窗口", 100000);
          setTimeout(() => window.close(), 1000);
        })
        .catch(() => {
          toast("程序已退出", "您可以安全地直接关闭此窗口", 100000);
        });
    }
  });
  el("btn-start")?.addEventListener("click", startPipeline);
  el("btn-stop")?.addEventListener("click", stopPipeline);
  el("btn-paid")?.addEventListener("click", () => confirmPayment(true));
  el("btn-fail")?.addEventListener("click", () => confirmPayment(false));
  el("btn-copy-pay")?.addEventListener("click", copyPayLink);
  el("btn-save-config")?.addEventListener("click", () =>
    saveConfigFromForm()
      .then(() => toast("设置已保存"))
      .catch((e) => toast("保存失败", e.message || "请稍后再试"))
  );
  el("btn-refresh-inventory")?.addEventListener("click", () => refreshInventory(true));
  el("btn-refresh-sales")?.addEventListener("click", () => refreshTransactions());
  el("btn-add-account")?.addEventListener("click", () => openAccountForm());
  el("accounts-search")?.addEventListener("input", (e) => {
    accountsSearchTerm = e.target?.value || "";
    renderAccountsUI(accountsCache || [], accountsCurrentId);
  });
  el("account-form-cancel")?.addEventListener("click", closeAccountForm);
  el("account-form-save")?.addEventListener("click", () => saveAccountForm());
  el("btn-add-purchase")?.addEventListener("click", async () => {
    const nameEl = el("add-purchase-name");
    const steamLinkEl = el("add-purchase-steam-link");
    const priceEl = el("add-purchase-price");
    const qtyEl = el("add-purchase-quantity");
    const goodsIdEl = el("add-purchase-goods-id");
    const name = (nameEl?.value || "").trim();
    const steamLink = (steamLinkEl?.value || "").trim();
    const price = priceEl ? parseFloat(priceEl.value) : NaN;
    let qty = qtyEl ? parseInt(qtyEl.value, 10) : 1;
    if (!name && !steamLink) {
      toast("请填写物品名称或 Steam 市场链接");
      return;
    }
    if (!Number.isFinite(price) || price <= 0) {
      toast("请填写有效价格");
      return;
    }
    if (!Number.isFinite(qty) || qty < 1) qty = 1;
    const goodsId = goodsIdEl?.value ? parseInt(goodsIdEl.value, 10) : null;
    if (goodsId !== null && isNaN(goodsId)) {
      toast("goods_id 须为数字");
      return;
    }
    try {
      const payload = { name, price, quantity: qty };
      if (steamLink) payload.steam_link = steamLink;
      if (goodsId != null && goodsId > 0) payload.goods_id = goodsId;
      const res = await fetchJson(API + "/purchase", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (res.ok !== true) {
        toast(res.error || "添加失败");
        return;
      }
      if (nameEl) nameEl.value = "";
      if (steamLinkEl) steamLinkEl.value = "";
      if (priceEl) priceEl.value = "";
      if (qtyEl) qtyEl.value = "1";
      if (goodsIdEl) goodsIdEl.value = "";
      await refreshTransactions();
      refreshStatus();
      toast(res.added > 1 ? `已添加 ${res.added} 条操作记录` : "已添加操作记录");
    } catch (e) {
      toast("添加失败", e.message || "");
    }
  });
  el("btn-clear-log")?.addEventListener("click", clearLog);
  el("btn-toggle-pause")?.addEventListener("click", togglePause);
  el("btn-toggle-scroll")?.addEventListener("click", toggleAutoScroll);
  el("btn-download-log")?.addEventListener("click", downloadLog);
  el("btn-export-log")?.addEventListener("click", exportLog);
  el("log-search")?.addEventListener("input", () => renderLogFull());
  el("log-level")?.addEventListener("change", () => renderLogFull());
  el("cfg-verbose-debug")?.addEventListener("change", async () => {
    try {
      const result = await fetchJson(API + "/config", {
        method: "POST",
        body: JSON.stringify({
          config: {
            pipeline: {
              verbose_debug: el("cfg-verbose-debug").checked,
            },
          },
        }),
      });
      if (!result.ok) throw new Error(result.error || "配置写入失败");
      toast("已保存", "详细调试 " + (el("cfg-verbose-debug").checked ? "已开启，下次运行生效" : "已关闭"));
    } catch (e) {
      toast("保存失败", e.message || "");
    }
  });
  el("cfg-steam-listings-debug")?.addEventListener("change", async () => {
    try {
      const result = await fetchJson(API + "/config", {
        method: "POST",
        body: JSON.stringify({
          config: {
            pipeline: {
              steam_listings_debug: el("cfg-steam-listings-debug").checked,
            },
          },
        }),
      });
      if (!result.ok) throw new Error(result.error || "配置写入失败");
      toast("已保存", "Steam 在售/历史调试 " + (el("cfg-steam-listings-debug").checked ? "已开启" : "已关闭"));
    } catch (e) {
      toast("保存失败", e.message || "");
    }
  });
  el("btn-export-config")?.addEventListener("click", exportConfig);
  el("btn-data-init")?.addEventListener("click", async () => {
    if (!(await appConfirm("警告：此操作将清空所有数据（包括交易记录、自动挂刀配置、绑定的 Steam 账号与凭据）！\n\n您确定要进行“恢复出厂设置”吗？", { title: "恢复出厂设置", danger: true, confirmText: "继续" }))) {
      return;
    }
    if (!(await appConfirm("再次确认：数据一旦清空将无法恢复。是否继续？", { title: "最终确认", danger: true, confirmText: "清空数据" }))) {
      return;
    }
    try {
      await fetchJson(API + "/data/init", { method: "POST" });
      await refreshStatus();
      toast("数据已清空，即将刷新页面");
      setTimeout(() => location.reload(), 1500);
    } catch (e) {
      toast("初始化失败", e.message || "");
    }
  });
  el("btn-sync-sold")?.addEventListener("click", async () => {
    const btn = el("btn-sync-sold");
    if (btn?.disabled) return;
    btn.disabled = true;
    toast("正在同步", "请稍候…");
    try {
      const r = await fetchJson(API + "/sync_sold_from_history", { method: "POST" });
      if (r.ok) {
        await refreshTransactions();
        await refreshStatus();
        const u = r.updated ?? 0;
        const f = r.filled ?? 0;
        if (u || f) toast("同步成功", `售出更新 ${u} 条，填充 assetid ${f} 条`);
        else toast("同步成功", "无变更");
      } else {
        toast("同步失败", r.error || "接口请求未返回 error 字段");
      }
    } catch (e) {
      toast("同步失败", e.message || "请求异常");
    } finally {
      btn.disabled = false;
    }
  });
  el("btn-repair-error")?.addEventListener("click", async () => {
    const btn = el("btn-repair-error");
    if (btn?.disabled) return;
    btn.disabled = true;
    toast("正在紧急修复", "请稍候…");
    try {
      const r = await fetchJson(API + "/repair_error_records", { method: "POST" });
      if (r.ok) {
        await refreshTransactions();
        await refreshStatus();
        const filled = r.filled ?? 0;
        const missing = r.missing ?? 0;
        const total = r.total ?? 0;
        const changed = r.changed ?? 0;
        if (total === 0) toast("无需紧急修复", "没有操作记录可重建");
        else if (missing === 0) toast("紧急修复成功", `已确认 ${filled}/${total} 条，写入 ${changed} 条变更`);
        else toast("紧急修复完成", `已确认 ${filled}/${total} 条，仍有 ${missing} 条待处理`);
      } else {
        toast("紧急修复失败", r.error || "接口未返回 error 字段");
      }
    } catch (e) {
      toast("紧急修复失败", e.message || "请求异常");
    } finally {
      btn.disabled = false;
    }
  });
  el("cfg-import-file")?.addEventListener("change", (e) => importConfigFromFile(e.target.files?.[0]));
  el("relogin-btn-open")?.addEventListener("click", openBrowserAndLogin);
  el("relogin-btn-ok")?.addEventListener("click", () => finishRelogin(true));
  el("relogin-btn-fail")?.addEventListener("click", () => finishRelogin(false));
  el("edit-tx-cancel")?.addEventListener("click", () => {
    el("edit-tx-overlay")?.classList.add("hidden");
    delete el("edit-tx-overlay")?.dataset.editType;
    delete el("edit-tx-overlay")?.dataset.editIdx;
  });
  el("edit-tx-save")?.addEventListener("click", async () => {
    const ov = el("edit-tx-overlay");
    const type = ov?.dataset?.editType;
    const idx = parseInt(ov?.dataset?.editIdx ?? "", 10);
    if (!type || !Number.isFinite(idx)) return;
    const nameVal = (el("edit-tx-name")?.value ?? "").trim();
    const priceVal = el("edit-tx-price")?.value;
    const price = priceVal ? parseFloat(priceVal) : NaN;
    const goodsIdVal = el("edit-tx-goods-id")?.value;
    const goodsId = goodsIdVal ? parseInt(goodsIdVal, 10) : null;
    const marketPriceVal = el("edit-tx-market-price")?.value;
    const marketPrice = marketPriceVal ? parseFloat(marketPriceVal) : null;
    if (!Number.isFinite(price) || price <= 0) {
      toast("请输入有效金额");
      return;
    }
    const payload = { type, idx, name: nameVal || null, price: Math.round(price * 100) / 100 };
    if (goodsId !== null && !isNaN(goodsId)) payload.goods_id = goodsId;
    if (type === "purchase") {
      payload.market_price = (marketPriceVal === "" || !Number.isFinite(marketPrice) || marketPrice < 0) ? 0 : Math.round(marketPrice * 100) / 100;
      const assetidVal = (el("edit-tx-assetid")?.value ?? "").trim();
      payload.assetid = assetidVal || null;
      const statusVal = el("edit-tx-listing")?.value;
      payload.pending_receipt = statusVal === "2";
      payload.listing = statusVal === "1";
    }
    try {
      const r = await fetchJson(API + "/transaction", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      if (r.ok) {
        ov.classList.add("hidden");
        delete ov.dataset.editType;
        delete ov.dataset.editIdx;
        refreshTransactions();
        toast("已保存");
      } else {
        toast("保存失败", r.error || "");
      }
    } catch (e) {
      toast("保存失败", e.message || "");
    }
  });
  el("sell-tx-cancel")?.addEventListener("click", () => {
    el("sell-tx-overlay")?.classList.add("hidden");
    delete el("sell-tx-overlay")?.dataset.sellIdx;
    delete el("sell-tx-overlay")?.dataset.sellIdxList;
  });
  el("sell-tx-confirm")?.addEventListener("click", async () => {
    const ov = el("sell-tx-overlay");
    const priceVal = el("sell-tx-price")?.value;
    const salePrice = priceVal ? parseFloat(priceVal) : NaN;
    if (!Number.isFinite(salePrice) || salePrice <= 0) {
      toast("请输入有效售出价格");
      return;
    }
    const listJson = ov?.dataset?.sellIdxList;
    const idxList = listJson ? (() => { try { return JSON.parse(listJson); } catch { return []; } })() : null;
    const singleIdx = listJson ? null : parseInt(ov?.dataset?.sellIdx ?? "", 10);
    const indices = Array.isArray(idxList) && idxList.length ? idxList : (Number.isFinite(singleIdx) ? [singleIdx] : []);
    if (!indices.length) return;
    try {
      const priceRounded = Math.round(salePrice * 100) / 100;
      for (const idx of indices) {
        const r = await fetchJson(API + "/transaction", {
          method: "PUT",
          body: JSON.stringify({ type: "purchase", idx, sale_proceeds: priceRounded }),
        });
        if (!r.ok) {
          toast("操作失败", r.error || "");
          return;
        }
      }
      ov.classList.add("hidden");
      delete ov.dataset.sellIdx;
      delete ov.dataset.sellIdxList;
      if (indices.length > 1) holdingsMultiSelectMode = false;
      refreshTransactions();
      toast(indices.length > 1 ? `已批量记录售出 ${indices.length} 条` : "已记录售出");
    } catch (e) {
      toast("操作失败", e.message || "");
    }
  });
  el("btn-holdings-multiselect")?.addEventListener("click", () => {
    holdingsMultiSelectMode = !holdingsMultiSelectMode;
    refreshTransactions();
  });
  el("btn-history-multiselect")?.addEventListener("click", () => {
    historyMultiSelectMode = !historyMultiSelectMode;
    refreshTransactions();
  });
  el("btn-history-batch-del")?.addEventListener("click", async () => {
    const checked = document.querySelectorAll("#transactions-table-purchase-history .history-checkbox:checked");
    const indices = Array.from(checked).map((cb) => parseInt(cb.dataset.idx, 10)).filter((n) => Number.isFinite(n));
    if (!indices.length) {
      toast("请先勾选要删除的项");
      return;
    }
    if (!(await appConfirm(`确定删除所选 ${indices.length} 条记录？删除后将从持有饰品与操作记录中同时移除。`, { title: "批量删除记录", danger: true, confirmText: "删除" }))) return;
    try {
      const sorted = indices.slice().sort((a, b) => b - a);
      for (const idx of sorted) {
        const r = await fetchJson(API + "/transaction?" + new URLSearchParams({ type: "purchase", idx }), { method: "DELETE" });
        if (!r.ok) { toast("删除失败", r.error || ""); return; }
      }
      historyMultiSelectMode = false;
      refreshTransactions();
      toast(`已删除 ${indices.length} 条`);
    } catch (e) {
      toast("删除失败", e.message || "");
    }
  });
  el("btn-holdings-batch-del")?.addEventListener("click", async () => {
    const checked = document.querySelectorAll("#transactions-table-purchases .holding-checkbox:checked");
    const indices = Array.from(checked).map((cb) => parseInt(cb.dataset.idx, 10)).filter((n) => Number.isFinite(n));
    if (!indices.length) {
      toast("请先勾选要删除的项");
      return;
    }
    if (!(await appConfirm(`确定删除所选 ${indices.length} 条记录？`, { title: "批量删除记录", danger: true, confirmText: "删除" }))) return;
    try {
      const sorted = indices.slice().sort((a, b) => b - a);
      for (const idx of sorted) {
        const r = await fetchJson(API + "/transaction?" + new URLSearchParams({ type: "purchase", idx }), { method: "DELETE" });
        if (!r.ok) { toast("删除失败", r.error || ""); return; }
      }
      holdingsMultiSelectMode = false;
      refreshTransactions();
      toast(`已删除 ${indices.length} 条`);
    } catch (e) {
      toast("删除失败", e.message || "");
    }
  });
  el("btn-holdings-batch-sell")?.addEventListener("click", () => {
    const checked = document.querySelectorAll("#transactions-table-purchases .holding-checkbox:checked");
    const indices = Array.from(checked).map((cb) => parseInt(cb.dataset.idx, 10)).filter((n) => Number.isFinite(n));
    if (!indices.length) {
      toast("请先勾选要售出的项");
      return;
    }
    const nameEl = el("sell-tx-item-name");
    if (nameEl) nameEl.textContent = `批量售出（共 ${indices.length} 条）`;
    el("sell-tx-overlay").dataset.sellIdxList = JSON.stringify(indices);
    delete el("sell-tx-overlay").dataset.sellIdx;
    el("sell-tx-price").value = "";
    el("sell-tx-overlay").classList.remove("hidden");
    el("sell-tx-price")?.focus();
  });
  el("btn-theme")?.addEventListener("click", () => Theme.cycle());
  el("steam-token-circle")?.addEventListener("click", handleSteamGuardClick);
}
function setupScrollToTop() {
  const btn = el("scroll-top-btn");
  if (!btn) return;
  const panels = document.querySelectorAll(".panel");
  const check = () => {
    const active = document.querySelector(".panel.active");
    if (active && active.scrollTop > 200) btn.classList.add("visible");
    else btn.classList.remove("visible");
  };
  panels.forEach(p => p.addEventListener("scroll", check, { passive: true }));
  btn.addEventListener("click", () => {
    const active = document.querySelector(".panel.active");
    if (active) active.scrollTo({ top: 0, behavior: "smooth" });
  });
}
function setupKeyboardShortcuts() {
  const tabMap = { "1": "auto", "2": "inventory", "3": "purchases", "4": "purchase-history", "5": "analytics", "6": "sales", "7": "accounts", "8": "steam-guard", "9": "settings", "0": "debug" };
  document.addEventListener("keydown", async (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT" || e.target.isContentEditable) return;
    if (e.altKey && tabMap[e.key]) {
      e.preventDefault();
      await tabSwitch(tabMap[e.key]);
    }
  });
}
async function init() {
  Theme.apply(Theme.get());
  if (window.matchMedia) {
    const mm = window.matchMedia("(prefers-color-scheme: dark)");
    mm.addEventListener?.("change", () => {
      if (Theme.get() === "system") Theme.apply("system");
    });
  }
  if (typeof _updateScrollBtn === "function") _updateScrollBtn();
  bindEvents();
  setupScrollToTop();
  setupKeyboardShortcuts();
  setupButtonInteractions();
  bindUXEvents();
  if (typeof loadRuntimeProfile === "function") {
    await loadRuntimeProfile();
  }

  try {
    await loadConfig();
    await loadProxyConfig();
  } catch (e) {
    toast("加载配置失败", e.message || "请检查后端是否可用");
  }

  // 异步加载库存，避免因 Steam 网络问题阻塞页面其余部分的初始化和展示
  refreshInventory(true).then(() => {
    setupInventoryAutoRefresh();
  });

  await refreshStatus();
  setInterval(refreshStatus, 2000);
  setInterval(() => {
    if (document.querySelector("#panel-debug.active")) refreshLog();
  }, 1500);
}
function setupButtonInteractions() {
  document.addEventListener('mousemove', (e) => {
    const btn = e.target.closest('.btn');
    if (btn) {
      const rect = btn.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width * 100).toFixed(0);
      const y = ((e.clientY - rect.top) / rect.height * 100).toFixed(0);
      btn.style.setProperty('--ripple-x', x + '%');
      btn.style.setProperty('--ripple-y', y + '%');
    }
  });
}
init();
