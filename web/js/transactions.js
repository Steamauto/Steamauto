
let holdingsMultiSelectMode = false;
let historyMultiSelectMode = false;
let lastEnrichTime = 0;
let lastEnrichData = null;
function renderTxTable(tbody, list, isPurchase = false, resellRatio = 0.85, multiSelectMode = false) {
  const ratio = Math.max(0.01, Math.min(1, Number(resellRatio) || 0.85));
  const rowHtmls = [];
  for (const t of list) {
    const at = t.at ? new Date(t.at * 1000) : null;
    const timeStr = at ? `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")} ${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}:${String(at.getSeconds()).padStart(2, "0")}` : "—";
    const nameText = (t.name || "—").toString();
    const idx = t.idx;
    const type = t.type;
    const actHtml = isPurchase
      ? (multiSelectMode
        ? `<button type="button" class="btn btn-sm btn-edit tx-btn-edit" data-type="${escapeHtml(type)}" data-idx="${idx}">编辑</button>`
        : `<button type="button" class="btn btn-sm btn-edit tx-btn-edit" data-type="${escapeHtml(type)}" data-idx="${idx}">编辑</button> <button type="button" class="btn btn-sm btn-primary tx-btn-sell" data-type="${escapeHtml(type)}" data-idx="${idx}">售出</button> <button type="button" class="btn btn-sm btn-danger-outline tx-btn-del" data-type="${escapeHtml(type)}" data-idx="${idx}">删除</button>`)
      : `<button type="button" class="btn btn-sm btn-edit tx-btn-edit" data-type="${escapeHtml(type)}" data-idx="${idx}">编辑</button> <button type="button" class="btn btn-sm btn-danger-outline tx-btn-del" data-type="${escapeHtml(type)}" data-idx="${idx}">删除</button>`;
    const checkCell = isPurchase && multiSelectMode ? `<td class="holding-select-cell"><input type="checkbox" class="holding-checkbox" data-idx="${idx}" /></td>` : "";
    const priceCell = `<td class="mono">${escapeHtml(Number(t.price).toFixed(2))}</td>`;
    if (isPurchase) {
      const mp = t.market_price != null ? Number(t.market_price).toFixed(2) : "—";
      const cur = t.current_market_price != null ? Number(t.current_market_price) : null;
      const cmp = cur != null ? cur.toFixed(2) : "";
      const marketAtBuy = t.market_price != null ? Number(t.market_price) : null;
      let plCell = "<td></td>";
      if (cur != null && marketAtBuy != null && marketAtBuy > 0) {
        const diff = cur - marketAtBuy;
        const pct = ((diff / marketAtBuy) * 100).toFixed(2) + "%";
        const cls = diff > 0 ? "text-ok" : diff < 0 ? "text-bad" : "";
        plCell = `<td class="mono ${cls}">${diff >= 0 ? "+" : ""}${diff.toFixed(2)} (${diff >= 0 ? "+" : ""}${pct})</td>`;
      }
      const cost = Number(t.price) || 0;
      const afterTaxVal = cur != null && cur > 0 ? cur / 1.15 : null;
      const afterTax = afterTaxVal != null ? afterTaxVal.toFixed(2) : "";
      const discountRatio = afterTaxVal != null && afterTaxVal > 0 && cost > 0 ? (cost / afterTaxVal).toFixed(4) : "";
      const discountRatioClass = discountRatio ? (parseFloat(discountRatio) > ratio ? "text-bad" : "text-ok") : "";
      const cashProfit = afterTaxVal != null && cost > 0 ? (afterTaxVal * ratio - cost).toFixed(2) : "";
      const profitClass = cashProfit ? (parseFloat(cashProfit) > 0 ? "text-ok" : parseFloat(cashProfit) < 0 ? "text-bad" : "") : "";
      const selfUseProfit = afterTaxVal != null && cost > 0 ? (afterTaxVal - cost).toFixed(2) : "";
      const selfUseClass = selfUseProfit ? (parseFloat(selfUseProfit) > 0 ? "text-ok" : parseFloat(selfUseProfit) < 0 ? "text-bad" : "") : "";
      const afterTaxCell = afterTax ? `<td class="mono">${escapeHtml(afterTax)}</td>` : "<td></td>";
      const discountRatioCell = discountRatio ? `<td class="mono ${discountRatioClass}">${escapeHtml(discountRatio)}</td>` : "<td></td>";
      const profitCell = cashProfit ? `<td class="mono ${profitClass}">${escapeHtml(parseFloat(cashProfit) >= 0 ? "+" + cashProfit : cashProfit)}</td>` : "<td></td>";
      const selfUseCell = selfUseProfit ? `<td class="mono ${selfUseClass}">${escapeHtml(parseFloat(selfUseProfit) >= 0 ? "+" + selfUseProfit : selfUseProfit)}</td>` : "<td></td>";
      const assetidCell = `<td class="mono">${escapeHtml(t.assetid ?? "—")}</td>`;
      rowHtmls.push(`<tr>${checkCell}<td class="mono">${escapeHtml(timeStr)}</td><td>${escapeHtml(nameText)}</td>${assetidCell}${priceCell}<td class="mono">${escapeHtml(mp)}</td><td class="mono">${escapeHtml(cmp)}</td>${afterTaxCell}${discountRatioCell}${profitCell}${selfUseCell}${plCell}<td class="tx-actions">${actHtml}</td></tr>`);
    } else {
      const assetidCell = `<td class="mono">${escapeHtml(t.assetid ?? "—")}</td>`;
      rowHtmls.push(`<tr>${checkCell}<td class="mono">${escapeHtml(timeStr)}</td><td>${escapeHtml(nameText)}</td>${assetidCell}${priceCell}<td class="tx-actions">${actHtml}</td></tr>`);
    }
  }
  tbody.innerHTML = rowHtmls.join("");
  tbody.querySelectorAll(".tx-btn-del").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!(await appConfirm("确定删除这条记录？", { title: "删除记录", danger: true, confirmText: "删除" }))) return;
      const type = btn.dataset.type;
      const idx = parseInt(btn.dataset.idx, 10);
      try {
        const r = await fetchJson(API + "/transaction?" + new URLSearchParams({ type, idx }), { method: "DELETE" });
        if (r.ok) {
          toast("已删除");
          refreshTransactions();
        } else {
          toast("删除失败", r.error || "");
        }
      } catch (e) {
        toast("删除失败", e.message || "");
      }
    });
  });
  tbody.querySelectorAll(".tx-btn-edit").forEach(btn => {
    btn.addEventListener("click", () => {
      const type = btn.dataset.type;
      const idx = parseInt(btn.dataset.idx, 10);
      const t = list.find(x => x.type === type && x.idx === idx);
      if (!t) return;
      el("edit-tx-name").value = t.name || "";
      el("edit-tx-price").value = t.price ?? "";
      el("edit-tx-goods-id").value = t.goods_id ?? "";
      const mpWrap = el("edit-tx-market-price-wrap");
      if (mpWrap) mpWrap.style.display = type === "purchase" ? "" : "none";
      const mpEl = el("edit-tx-market-price");
      if (mpEl) mpEl.value = type === "purchase" ? (t.market_price ?? "") : "";
      const assetidWrap = el("edit-tx-assetid-wrap");
      if (assetidWrap) assetidWrap.style.display = type === "purchase" ? "" : "none";
      const assetidEl = el("edit-tx-assetid");
      if (assetidEl) assetidEl.value = type === "purchase" ? (t.assetid ?? "") : "";
      const listingWrap = el("edit-tx-listing-wrap");
      if (listingWrap) listingWrap.style.display = type === "purchase" ? "" : "none";
      const listingEl = el("edit-tx-listing");
      if (listingEl) {
        if (type !== "purchase") listingEl.value = "0";
        else if (t.pending_receipt) listingEl.value = "2";
        else if (t.listing) listingEl.value = "1";
        else listingEl.value = "0";
      }
      el("edit-tx-overlay").dataset.editType = type;
      el("edit-tx-overlay").dataset.editIdx = String(idx);
      el("edit-tx-overlay").classList.remove("hidden");
    });
  });
  tbody.querySelectorAll(".tx-btn-sell").forEach(btn => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.idx, 10);
      const t = list.find(x => x.type === "purchase" && x.idx === idx);
      if (!t) return;
      const nameEl = el("sell-tx-item-name");
      if (nameEl) nameEl.textContent = t.name || "—";
      el("sell-tx-overlay").dataset.sellIdx = String(idx);
      const priceEl = el("sell-tx-price");
      if (priceEl) priceEl.value = "";
      el("sell-tx-overlay").classList.remove("hidden");
      if (priceEl) priceEl.focus();
    });
  });
}
function renderPurchaseHistoryTable(tbody, list, resellRatio = 0.85, multiSelectMode = false) {
  const ratio = Math.max(0.01, Math.min(1, Number(resellRatio) || 0.85));
  const sorted = list.slice().sort((a, b) => (b.at || 0) - (a.at || 0));
  const rowHtmls = [];
  for (const t of sorted) {
    const at = t.at ? new Date(t.at * 1000) : null;
    const timeStr = at ? `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")} ${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}:${String(at.getSeconds()).padStart(2, "0")}` : "—";
    const nameText = (t.name || "—").toString();
    const idx = t.idx;
    const checkCell = multiSelectMode ? `<td class="holding-select-cell"><input type="checkbox" class="history-checkbox" data-idx="${idx}" /></td>` : "";
    const cost = Number(t.price) || 0;
    const mp = t.market_price != null ? Number(t.market_price).toFixed(2) : "—";
    const sold = t.sale_price != null && Number(t.sale_price) > 0;
    const listingError = t.listing_status === "error";
    const statusStr = t.pending_receipt ? "待收货" : sold ? "已出售" : listingError ? "ERROR" : t.listing ? "出售中" : "持有中";
    const statusCellClass = t.pending_receipt ? "status-pending" : sold ? "status-sold" : listingError ? "status-error" : t.listing ? "status-listing" : "status-holding";
    const salePriceStr = sold ? Number(t.sale_price).toFixed(2) : "—";
    let discountRatioStr = "—", cashProfitStr = "—", selfUseStr = "—", discountRatioClass = "";
    if (sold) {
      const afterTax = Number(t.sale_price) / 1.15;
      discountRatioStr = afterTax > 0 && cost > 0 ? (cost / afterTax).toFixed(4) : "—";
      discountRatioClass = discountRatioStr !== "—" ? (parseFloat(discountRatioStr) > ratio ? "text-bad" : "text-ok") : "";
      const cashProfit = afterTax > 0 && cost >= 0 ? afterTax * ratio - cost : 0;
      const selfUse = afterTax - cost;
      cashProfitStr = (cashProfit >= 0 ? "+" : "") + cashProfit.toFixed(2);
      selfUseStr = (selfUse >= 0 ? "+" : "") + selfUse.toFixed(2);
    }
    const cashClass = sold && parseFloat(cashProfitStr) !== 0 ? (parseFloat(cashProfitStr) > 0 ? "text-ok" : "text-bad") : "";
    const selfUseClass = sold && parseFloat(selfUseStr) !== 0 ? (parseFloat(selfUseStr) > 0 ? "text-ok" : "text-bad") : "";
    let deviationCell = "<td></td>";
    if (sold) {
      const marketAtBuy = t.market_price != null ? Number(t.market_price) : 0;
      const saleP = Number(t.sale_price) || 0;
      if (marketAtBuy > 0) {
        const diff = saleP - marketAtBuy;
        const pct = ((diff / marketAtBuy) * 100).toFixed(2) + "%";
        const devClass = diff > 0 ? "text-ok" : diff < 0 ? "text-bad" : "";
        deviationCell = `<td class="mono ${devClass}">${diff >= 0 ? "+" : ""}${diff.toFixed(2)} (${diff >= 0 ? "+" : ""}${pct})</td>`;
      } else {
        deviationCell = `<td class="mono">—</td>`;
      }
    } else {
      deviationCell = `<td class="mono">—</td>`;
    }
    const dbId = Number(t.db_id) || 0;
    const delistBtn = !multiSelectMode && t.listing ? `<button type="button" class="btn btn-sm btn-warning-outline ph-btn-delist" data-type="purchase" data-idx="${idx}" data-db-id="${dbId}">下架</button> ` : "";
    const actHtml = !multiSelectMode ? (delistBtn + `<button type="button" class="btn btn-sm btn-danger-outline ph-btn-del" data-type="purchase" data-idx="${idx}">删除</button>`) : "";
    const assetidStr = t.assetid ?? "—";
    rowHtmls.push(`<tr>${checkCell}<td class="mono">${escapeHtml(timeStr)}</td><td>${escapeHtml(nameText)}</td><td class="mono">${escapeHtml(assetidStr)}</td><td class="mono">${escapeHtml(Number(t.price).toFixed(2))}</td><td class="mono">${escapeHtml(mp)}</td><td class="status-cell ${statusCellClass}">${escapeHtml(statusStr)}</td><td class="mono">${escapeHtml(salePriceStr)}</td><td class="mono ${discountRatioClass}">${escapeHtml(discountRatioStr)}</td><td class="mono ${cashClass}">${escapeHtml(cashProfitStr)}</td><td class="mono ${selfUseClass}">${escapeHtml(selfUseStr)}</td>${deviationCell}<td class="tx-actions">${actHtml}</td></tr>`);
  }
  tbody.innerHTML = rowHtmls.join("");
  tbody.querySelectorAll(".ph-btn-delist").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!(await appConfirm("确定下架该饰品？下架后 assetid 会变更。", { title: "下架饰品", confirmText: "下架" }))) return;
      const idx = parseInt(btn.dataset.idx, 10);
      btn.disabled = true;
      toast("下架中", "请稍候…");
      try {
        const dbId = parseInt(btn.dataset.dbId, 10) || 0;
        const query = dbId ? "?" + new URLSearchParams({ db_id: dbId }) : "";
        const r = await fetchJson(API + "/purchase/" + idx + "/delist" + query, { method: "POST" });
        if (r.ok) {
          const assetDetail = r.assetid != null && r.assetid !== "" ? "新 assetid: " + r.assetid : "新 assetid 为空，请使用「同步售出/持有」补全";
          const detail = [assetDetail, r.warning || r.message || ""].filter(Boolean).join("；");
          toast("下架成功", detail);
          refreshTransactions();
          refreshStatus();
        } else {
          toast("下架失败", r.error || "接口未返回 error 字段");
        }
      } catch (e) {
        toast("下架失败", e.message || "请求异常");
      } finally {
        btn.disabled = false;
      }
    });
  });
  tbody.querySelectorAll(".ph-btn-del").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!(await appConfirm("确定删除这条记录？删除后将从持有饰品与操作记录中同时移除。", { title: "删除持有记录", danger: true, confirmText: "删除" }))) return;
      const idx = parseInt(btn.dataset.idx, 10);
      try {
        const r = await fetchJson(API + "/transaction?" + new URLSearchParams({ type: "purchase", idx }), { method: "DELETE" });
        if (r.ok) {
          toast("已删除");
          refreshTransactions();
        } else {
          toast("删除失败", r.error || "");
        }
      } catch (e) {
        toast("删除失败", e.message || "");
      }
    });
  });
}
function applyTransactionsToUI(all, summaryEl, tbodyP, tbodyS, tbodyHistory, resellRatio = 0.85) {
  const purchases = all.filter((t) => t.type === "purchase");
  const holdings = purchases.filter((t) => !(t.sale_price != null && Number(t.sale_price) > 0));
  const sales = all.filter((t) => t.type === "sale");
  const ratio = Math.max(0.01, Math.min(1, Number(resellRatio) || 0.85));
  if (tbodyP) renderTxTable(tbodyP, holdings, true, ratio, holdingsMultiSelectMode);
  if (tbodyHistory) renderPurchaseHistoryTable(tbodyHistory, purchases, ratio, historyMultiSelectMode);
  syncHistoryMultiSelectUI();
  const historySummaryEl = el("purchase-history-summary");
  if (historySummaryEl && purchases.length) {
    const totalPrice = purchases.reduce((s, t) => s + (Number(t.price) || 0), 0);
    const totalMp = purchases.reduce((s, t) => s + (t.market_price != null ? Number(t.market_price) : 0), 0);
    const totalSalePrice = purchases.reduce((s, t) => s + (t.sale_price != null && Number(t.sale_price) > 0 ? Number(t.sale_price) : 0), 0);
    const totalAfterTax = totalSalePrice > 0 ? totalSalePrice / 1.15 : null;
    const soldItems = purchases.filter((t) => t.sale_price != null && Number(t.sale_price) > 0);
    let ratioSum = 0, ratioCount = 0, totalCashProfit = 0, totalSelfUseProfit = 0;
    soldItems.forEach((t) => {
      const afterTax = Number(t.sale_price) / 1.15;
      const cost = Number(t.price) || 0;
      if (afterTax > 0 && cost > 0) { ratioSum += cost / afterTax; ratioCount += 1; }
      totalCashProfit += afterTax * ratio - cost;
      totalSelfUseProfit += afterTax - cost;
    });
    const discountRatio = ratioCount > 0 ? (ratioSum / ratioCount).toFixed(4) : "—";
    const discountRatioClass = discountRatio !== "—" ? (parseFloat(discountRatio) > ratio ? "text-bad" : "text-ok") : "";
    const cashProfitVal = soldItems.length > 0 ? totalCashProfit : null;
    const selfUseProfitVal = soldItems.length > 0 ? totalSelfUseProfit : null;
    const profitClass = cashProfitVal != null && cashProfitVal > 0 ? "text-ok" : cashProfitVal != null && cashProfitVal < 0 ? "text-bad" : "";
    const selfUseClass = selfUseProfitVal != null && selfUseProfitVal > 0 ? "text-ok" : selfUseProfitVal != null && selfUseProfitVal < 0 ? "text-bad" : "";
    const soldMp = purchases.reduce((s, t) => s + (t.sale_price != null && Number(t.sale_price) > 0 && t.market_price != null ? Number(t.market_price) : 0), 0);
    const totalDeviation = totalSalePrice > 0 && soldMp > 0 ? totalSalePrice - soldMp : null;
    const totalDeviationPct = totalDeviation != null && soldMp > 0 ? ((totalDeviation / soldMp) * 100).toFixed(2) + "%" : "—";
    const deviationClass = totalDeviation != null && totalDeviation > 0 ? "text-ok" : totalDeviation != null && totalDeviation < 0 ? "text-bad" : "";
    const deviationStr = totalDeviation != null ? `${totalDeviation >= 0 ? "+" : ""}${totalDeviation.toFixed(2)} (${totalDeviation >= 0 ? "+" : ""}${totalDeviationPct})` : "—";
    const salePriceStr = totalSalePrice > 0 ? totalSalePrice.toFixed(2) : "—";
    const afterTaxStr = totalAfterTax != null ? totalAfterTax.toFixed(2) : "—";
    const cashProfitStr = cashProfitVal != null ? (cashProfitVal >= 0 ? "+" : "") + cashProfitVal.toFixed(2) : "—";
    const selfUseProfitStr = selfUseProfitVal != null ? (selfUseProfitVal >= 0 ? "+" : "") + selfUseProfitVal.toFixed(2) : "—";
    historySummaryEl.innerHTML = [
      `<span class="summary-stat"><span class="summary-label">总购入价</span><span class="summary-value mono">${totalPrice.toFixed(2)}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">总购入市场价</span><span class="summary-value mono">${totalMp.toFixed(2)}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">总出售价</span><span class="summary-value mono">${salePriceStr}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">总税后价格</span><span class="summary-value mono">${afterTaxStr}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">实际折扣比率</span><span class="summary-value mono ${discountRatioClass}">${discountRatio}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">总变现收益</span><span class="summary-value mono ${profitClass}">${cashProfitStr}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">总自用收益</span><span class="summary-value mono ${selfUseClass}">${selfUseProfitStr}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">总价格偏离度</span><span class="summary-value mono ${deviationClass}">${deviationStr}</span></span>`,
    ].join("");
    historySummaryEl.style.display = "";
  } else if (historySummaryEl) {
    historySummaryEl.textContent = "";
    historySummaryEl.style.display = "none";
  }
  if (summaryEl && holdings.length) {
    const totalPrice = holdings.reduce((s, t) => s + (Number(t.price) || 0), 0);
    const totalMp = holdings.reduce((s, t) => s + (t.market_price != null ? Number(t.market_price) : 0), 0);
    const hasAnyCmp = holdings.some((t) => t.current_market_price != null);
    const totalCmp = hasAnyCmp ? holdings.reduce((s, t) => s + (t.current_market_price != null ? Number(t.current_market_price) : 0), 0) : null;
    const marketChangeTotals = holdings.reduce((acc, t) => {
      const cmp = t.current_market_price != null ? Number(t.current_market_price) : null;
      const mp = t.market_price != null ? Number(t.market_price) : null;
      if (cmp != null && Number.isFinite(cmp) && mp != null && Number.isFinite(mp) && mp > 0) {
        acc.current += cmp;
        acc.purchase += mp;
        acc.count += 1;
      }
      return acc;
    }, { current: 0, purchase: 0, count: 0 });
    const totalPl = marketChangeTotals.count > 0 ? marketChangeTotals.current - marketChangeTotals.purchase : null;
    const totalPlPct = totalPl != null && marketChangeTotals.purchase > 0 ? ((totalPl / marketChangeTotals.purchase) * 100).toFixed(2) + "%" : "—";
    const plClass = totalPl != null && totalPl > 0 ? "text-ok" : totalPl != null && totalPl < 0 ? "text-bad" : "";
    const cmpStr = totalCmp != null ? totalCmp.toFixed(2) : "—";
    const plStr = totalPl != null ? `${totalPl >= 0 ? "+" : ""}${totalPl.toFixed(2)} (${totalPl >= 0 ? "+" : ""}${totalPlPct})` : "—";
    const totalAfterTax = totalCmp != null && totalCmp > 0 ? totalCmp / 1.15 : null;
    const afterTaxStr = totalAfterTax != null ? totalAfterTax.toFixed(2) : "—";
    let ratioSum = 0, ratioCount = 0, totalCashProfit = 0, totalSelfUseProfit = 0;
    holdings.forEach((t) => {
      const cmp = t.current_market_price != null ? Number(t.current_market_price) : null;
      if (cmp == null || cmp <= 0) return;
      const afterTax = cmp / 1.15;
      const cost = Number(t.price) || 0;
      if (afterTax > 0 && cost > 0) { ratioSum += cost / afterTax; ratioCount += 1; }
      totalCashProfit += afterTax * ratio - cost;
      totalSelfUseProfit += afterTax - cost;
    });
    const discountRatio = ratioCount > 0 ? (ratioSum / ratioCount).toFixed(4) : "—";
    const discountRatioClass = discountRatio !== "—" ? (parseFloat(discountRatio) > ratio ? "text-bad" : "text-ok") : "";
    const cashProfitVal = ratioCount > 0 ? totalCashProfit : null;
    const selfUseProfitVal = ratioCount > 0 ? totalSelfUseProfit : null;
    const profitClass = cashProfitVal != null && cashProfitVal > 0 ? "text-ok" : cashProfitVal != null && cashProfitVal < 0 ? "text-bad" : "";
    const selfUseProfitClass = selfUseProfitVal != null && selfUseProfitVal > 0 ? "text-ok" : selfUseProfitVal != null && selfUseProfitVal < 0 ? "text-bad" : "";
    const cashProfitStr = cashProfitVal != null ? (cashProfitVal >= 0 ? "+" : "") + cashProfitVal.toFixed(2) : "—";
    const selfUseProfitStr = selfUseProfitVal != null ? (selfUseProfitVal >= 0 ? "+" : "") + selfUseProfitVal.toFixed(2) : "—";
    summaryEl.innerHTML = [
      `<span class="summary-stat"><span class="summary-label">数量</span><span class="summary-value mono">${holdings.length}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">总购入价</span><span class="summary-value mono">${totalPrice.toFixed(2)}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">总购入市场价</span><span class="summary-value mono">${totalMp.toFixed(2)}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">总现市场价</span><span class="summary-value mono">${cmpStr}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">总税后价格</span><span class="summary-value mono">${afterTaxStr}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">实际折扣比率</span><span class="summary-value mono ${discountRatioClass}">${discountRatio}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">总变现收益</span><span class="summary-value mono ${profitClass}">${cashProfitStr}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">总自用收益</span><span class="summary-value mono ${selfUseProfitClass}">${selfUseProfitStr}</span></span>`,
      `<span class="summary-stat"><span class="summary-label">总市场变动</span><span class="summary-value mono ${plClass}">${plStr}</span></span>`,
    ].join("");
  } else if (summaryEl) {
    summaryEl.textContent = "";
    summaryEl.style.display = "none";
  }
  if (summaryEl && holdings.length) summaryEl.style.display = "";
  if (summaryEl && !holdings.length) summaryEl.style.display = "none";
  if (tbodyS) renderTxTable(tbodyS, sales, false);
  refreshAnalytics(purchases, ratio);
  syncHoldingsMultiSelectUI();
}
function syncHoldingsMultiSelectUI() {
  const selectTh = el("holding-select-th");
  const batchBar = el("holdings-batch-actions");
  const multiselectBtn = el("btn-holdings-multiselect");
  if (holdingsMultiSelectMode) {
    if (selectTh) selectTh.classList.remove("hidden");
    if (batchBar) { batchBar.classList.remove("hidden"); batchBar.style.display = "flex"; }
    if (multiselectBtn) multiselectBtn.textContent = "取消多选";
  } else {
    if (selectTh) selectTh.classList.add("hidden");
    if (batchBar) { batchBar.classList.add("hidden"); batchBar.style.display = "none"; }
    if (multiselectBtn) multiselectBtn.textContent = "多选";
  }
}
function syncHistoryMultiSelectUI() {
  const selectTh = el("history-select-th");
  const batchBar = el("history-batch-actions");
  const multiselectBtn = el("btn-history-multiselect");
  if (historyMultiSelectMode) {
    if (selectTh) selectTh.classList.remove("hidden");
    if (batchBar) { batchBar.classList.remove("hidden"); batchBar.style.display = "flex"; }
    if (multiselectBtn) multiselectBtn.textContent = "取消多选";
  } else {
    if (selectTh) selectTh.classList.add("hidden");
    if (batchBar) { batchBar.classList.add("hidden"); batchBar.style.display = "none"; }
    if (multiselectBtn) multiselectBtn.textContent = "多选";
  }
}
function getCurrentPriceRefreshMinutes() {
  return Math.max(1, parseInt(el("cfg-current-price-refresh-minutes")?.value, 10) || 10);
}
async function refreshTransactions() {
  const tbodyP = document.querySelector("#transactions-table-purchases tbody");
  const tbodyS = document.querySelector("#transactions-table-sales tbody");
  const tbodyHistory = document.querySelector("#transactions-table-purchase-history tbody");
  const summaryEl = el("purchases-summary");
  if (!tbodyP && !tbodyS && !tbodyHistory) return;
  try {
    const d = await fetchJson(API + "/transactions?enrich_current_price=0");
    const all = d.transactions || [];
    const byKey = (t) => `${t.type}:${t.idx}`;
    const enrichedMap = new Map((lastEnrichData || []).map((t) => [byKey(t), t]));
    for (const t of all) {
      const e = enrichedMap.get(byKey(t));
      if (e && e.current_market_price != null) t.current_market_price = e.current_market_price;
    }
    lastEnrichData = all;
    applyTransactionsToUI(all, summaryEl, tbodyP, tbodyS, tbodyHistory, d.resell_ratio);
  } catch (e) {
    toast("加载操作记录失败", e.message || "");
  }
}
