let strategiesState = {
  loaded: false,
  view: "buy",
  strategies: [],
  modules: [],
  active: { buy: "", sell: "" },
  limits: { buy: 22, sell: 14 },
  availableData: {},
  selectedId: "",
  draft: null,
  draftSnapshot: "",
  selectedStep: 0,
  simulateResults: [],
};

let strategyDragState = null;
let strategySuppressClickUntil = 0;

const STRATEGY_BUY_FIXED_MODULES = [
  "buy.steamdt_top_n",
  "buy.basic_candidate_filter",
  "buy.buff_realtime_price",
  "buy.steam_sell_depth",
  "guard.target_balance",
  "action.buff_lock_pay",
];

const STRATEGY_SELL_LIST_FIXED_MODULES = [
  "sell.sellable_inventory_filter",
  "guard.max_listings_per_item",
  "action.steam_list",
];

const STRATEGY_SELL_PRICING_CORE_MODULES = [
  "pricing.steam_wall_price",
  "pricing.steam_wall_gap",
];

const STRATEGY_SELL_DEFAULT_PRICING_MODULE = "pricing.steam_wall_price";

function strategyClone(v) {
  if (v === undefined) return undefined;
  return JSON.parse(JSON.stringify(v));
}

function strategyModulesById() {
  return new Map((strategiesState.modules || []).map((m) => [m.id, m]));
}

function strategyById(id) {
  return (strategiesState.strategies || []).find((s) => s.id === id) || null;
}

function strategyLocalDraft() {
  const draft = strategiesState.draft;
  return draft && !draft.id && draft._local_id ? draft : null;
}

function strategyLocalDraftId(type) {
  return `draft.${type}.${Date.now()}.${Math.random().toString(16).slice(2)}`;
}

function strategyStepLimit(type = strategiesState.draft?.strategy_type || strategiesState.view) {
  const value = Number(strategiesState.limits?.[type]);
  if (Number.isFinite(value) && value > 0) return value;
  return type === "sell" ? 14 : 22;
}

function strategyModuleName(moduleId) {
  const mod = strategyModulesById().get(moduleId);
  return mod?.name || moduleId;
}

function strategyEnabledModuleIds(excludeIndex = -1) {
  return new Set((strategiesState.draft?.steps || [])
    .filter((step, idx) => idx !== excludeIndex && step.enabled !== false)
    .map((step) => step.module_id));
}

function strategyModuleCount(moduleId, excludeIndex = -1) {
  return (strategiesState.draft?.steps || [])
    .filter((step, idx) => idx !== excludeIndex && step.module_id === moduleId)
    .length;
}

function strategyIsSellPricingCore(moduleId) {
  return STRATEGY_SELL_PRICING_CORE_MODULES.includes(moduleId);
}

function strategyPricingReplacementIndexes(mod, options = {}) {
  const draft = strategiesState.draft;
  if (!options.allowPricingReplacement || !draft || draft.strategy_type !== "sell" || !strategyIsSellPricingCore(mod?.id)) {
    return [];
  }
  const conflicts = new Set([
    ...(mod.conflicts || []),
    ...STRATEGY_SELL_PRICING_CORE_MODULES.filter((moduleId) => moduleId !== mod.id),
  ]);
  return (draft.steps || [])
    .map((step, idx) => ({ step, idx }))
    .filter(({ step }) => step.module_id !== mod.id && conflicts.has(step.module_id))
    .map(({ idx }) => idx);
}

function strategyModuleAddStatus(mod, options = {}) {
  const draft = strategiesState.draft;
  if (!draft || draft.origin === "system") return { ok: false, reason: "系统预设结构只读；参数可直接编辑，改结构请先复制" };
  if (mod?.origin === "user" && (mod.module_kind !== "declarative" || mod.enabled === false)) {
    return { ok: false, reason: "外部代码模块仅登记展示，不能加入启用链" };
  }
  const steps = draft.steps || [];
  const ignoreIndex = Number.isInteger(options.ignoreIndex) ? options.ignoreIndex : -1;
  const isExistingStep = !!options.existing;
  const replacementIndexes = isExistingStep ? [] : strategyPricingReplacementIndexes(mod, options);
  const replacementSet = new Set(replacementIndexes);
  const limit = strategyStepLimit(draft.strategy_type);
  const stepCount = steps.filter((_, idx) => idx !== ignoreIndex && !replacementSet.has(idx)).length;
  if (!isExistingStep && stepCount >= limit) {
    return { ok: false, reason: `当前策略最多 ${limit} 个模块` };
  }
  const rawMaxInstances = Number(mod?.max_instances || 1);
  const maxInstances = Number.isFinite(rawMaxInstances) && rawMaxInstances > 0 ? rawMaxInstances : 1;
  const count = steps
    .filter((step, idx) => idx !== ignoreIndex && !replacementSet.has(idx) && step.module_id === mod.id)
    .length;
  if (!isExistingStep && count >= maxInstances) {
    return { ok: false, reason: maxInstances === 1 ? "该模块已添加" : `该模块最多 ${maxInstances} 个` };
  }
  const enabledIds = new Set(steps
    .filter((step, idx) => idx !== ignoreIndex && !replacementSet.has(idx) && step.enabled !== false)
    .map((step) => step.module_id));
  const modules = strategyModulesById();
  const conflicts = new Set(mod?.conflicts || []);
  for (const enabledId of enabledIds) {
    const other = modules.get(enabledId) || {};
    if (conflicts.has(enabledId) || (other.conflicts || []).includes(mod.id)) {
      return { ok: false, reason: `与「${strategyModuleName(enabledId)}」互斥` };
    }
  }
  return {
    ok: true,
    reason: "",
    replacementIndexes,
    replacementHint: replacementIndexes.length
      ? `将替换：${replacementIndexes.map((idx) => strategyModuleName(steps[idx]?.module_id)).join("、")}`
      : "",
  };
}

function strategyModuleDependencyHint(mod) {
  const enabledIds = strategyEnabledModuleIds();
  const missing = [];
  (mod?.requires || []).forEach((moduleId) => {
    if (!enabledIds.has(moduleId)) missing.push(strategyModuleName(moduleId));
  });
  (mod?.requires_any || []).forEach((group) => {
    if (Array.isArray(group) && group.length && !group.some((moduleId) => enabledIds.has(moduleId))) {
      missing.push(group.map(strategyModuleName).join(" / "));
    }
  });
  return missing.length ? `需要：${missing.join("；")}` : "";
}

function strategyStep(moduleId, enabled = true) {
  return { module_id: moduleId, enabled, params: defaultParamsForModule(moduleId) };
}

function strategyBlankSteps(type) {
  if (type === "sell") {
    return [
      strategyStep("sell.sellable_inventory_filter"),
      strategyStep("guard.max_listings_per_item"),
      strategyStep(STRATEGY_SELL_DEFAULT_PRICING_MODULE),
      strategyStep("action.steam_list"),
    ];
  }
  return STRATEGY_BUY_FIXED_MODULES.map((moduleId) => strategyStep(moduleId));
}

function strategyEnabledIdsForDraft(draft = strategiesState.draft) {
  return new Set((draft?.steps || [])
    .filter((step) => step.enabled !== false)
    .map((step) => step.module_id));
}

function strategyMergeDefaultParams(step) {
  const out = strategyClone(step || {});
  out.params = { ...defaultParamsForModule(out.module_id), ...(out.params || {}) };
  out.enabled = true;
  return out;
}

function strategyTakeStep(steps, moduleId, usedIndexes) {
  const idx = steps.findIndex((step, index) => !usedIndexes.has(index) && step.module_id === moduleId);
  if (idx >= 0) {
    usedIndexes.add(idx);
    return strategyMergeDefaultParams(steps[idx]);
  }
  return strategyStep(moduleId);
}

function strategyEnsureRequiredSteps(draft = strategiesState.draft) {
  if (!draft || draft.origin === "system") return false;
  const original = JSON.stringify(draft.steps || []);
  const steps = Array.isArray(draft.steps) ? draft.steps : [];
  const used = new Set();

  if (draft.strategy_type === "buy") {
    const headIds = [
      "buy.steamdt_top_n",
      "buy.basic_candidate_filter",
      "buy.buff_realtime_price",
      "buy.steam_sell_depth",
    ];
    const tailIds = ["guard.target_balance", "action.buff_lock_pay"];
    const fixedIds = new Set([...headIds, ...tailIds]);
    const head = headIds.map((moduleId) => strategyTakeStep(steps, moduleId, used));
    const tail = tailIds.map((moduleId) => strategyTakeStep(steps, moduleId, used));
    const middle = steps
      .filter((step, idx) => !used.has(idx) && !fixedIds.has(step.module_id))
      .map((step) => strategyClone(step));
    const maxMiddle = Math.max(0, strategyStepLimit(draft.strategy_type) - head.length - tail.length);
    draft.steps = [...head, ...middle.slice(0, maxMiddle), ...tail];
  } else {
    const enabledIds = strategyEnabledIdsForDraft(draft);
    const hasPause = enabledIds.has("action.pause_auto_sell");
    const hasList = enabledIds.has("action.steam_list");
    if (hasPause && !hasList) {
      const pause = strategyTakeStep(steps, "action.pause_auto_sell", used);
      const middle = steps
        .filter((step, idx) => !used.has(idx) && step.module_id !== "action.steam_list")
        .map((step) => strategyClone(step));
      const maxMiddle = Math.max(0, strategyStepLimit(draft.strategy_type) - 1);
      draft.steps = [...middle.slice(0, maxMiddle), pause];
    } else {
      const pricingId = (steps.find((step) => step.enabled !== false && strategyIsSellPricingCore(step.module_id)) || {})
        .module_id || STRATEGY_SELL_DEFAULT_PRICING_MODULE;
      const pricingConflicts = new Set(pricingId === "pricing.steam_wall_gap"
        ? ["pricing.steam_wall_price", "pricing.price_offset"]
        : ["pricing.steam_wall_gap"]);
      const fixedIds = new Set([
        "sell.sellable_inventory_filter",
        "guard.max_listings_per_item",
        "action.steam_list",
      ]);
      const head = [
        strategyTakeStep(steps, "sell.sellable_inventory_filter", used),
        strategyTakeStep(steps, "guard.max_listings_per_item", used),
      ];
      const action = strategyTakeStep(steps, "action.steam_list", used);
      const middle = steps
        .filter((step, idx) => {
          if (used.has(idx) || fixedIds.has(step.module_id) || step.module_id === "action.pause_auto_sell") return false;
          if (pricingConflicts.has(step.module_id)) return false;
          if (strategyIsSellPricingCore(step.module_id) && step.module_id !== pricingId) return false;
          return true;
        })
        .map((step) => strategyClone(step));
      if (!middle.some((step) => step.module_id === pricingId && step.enabled !== false)) {
        middle.unshift(strategyStep(pricingId));
      }
      const pricingIndex = middle.findIndex((step) => step.module_id === pricingId);
      const offsetIndex = middle.findIndex((step) => step.module_id === "pricing.price_offset");
      if (pricingIndex >= 0 && offsetIndex >= 0 && offsetIndex < pricingIndex) {
        const [offset] = middle.splice(offsetIndex, 1);
        const nextPricingIndex = middle.findIndex((step) => step.module_id === pricingId);
        middle.splice(nextPricingIndex + 1, 0, offset);
      }
      const maxMiddle = Math.max(0, strategyStepLimit(draft.strategy_type) - head.length - 1);
      draft.steps = [...head, ...middle.slice(0, maxMiddle), action];
    }
  }

  strategiesState.selectedStep = Math.max(0, Math.min(strategiesState.selectedStep, draft.steps.length - 1));
  return JSON.stringify(draft.steps || []) !== original;
}

function isStrategyStepFixed(step, draft = strategiesState.draft) {
  if (!step || !draft || draft.origin === "system") return false;
  const moduleId = step.module_id;
  const enabledIds = strategyEnabledIdsForDraft(draft);
  if (draft.strategy_type === "buy") {
    return STRATEGY_BUY_FIXED_MODULES.includes(moduleId);
  }
  if (enabledIds.has("action.steam_list")) {
    return moduleId === "sell.sellable_inventory_filter"
      || moduleId === "guard.max_listings_per_item"
      || moduleId === "action.steam_list";
  }
  if (moduleId === "action.pause_auto_sell") return true;
  return false;
}

function strategyHasListingAction(draft = strategiesState.draft) {
  return !!(draft?.steps || []).some((step) => step.module_id === "action.steam_list" && step.enabled !== false);
}

function strategyIsLastEnabledPricingCore(idx, draft = strategiesState.draft) {
  const steps = draft?.steps || [];
  const step = steps[idx];
  if (!step || draft?.strategy_type !== "sell" || !strategyIsSellPricingCore(step.module_id) || step.enabled === false) {
    return false;
  }
  if (!strategyHasListingAction(draft)) return false;
  return steps.filter((item) => strategyIsSellPricingCore(item.module_id) && item.enabled !== false).length <= 1;
}

function strategyDefaultInsertIndex(draft = strategiesState.draft) {
  const steps = draft?.steps || [];
  const terminalIds = draft?.strategy_type === "buy"
    ? ["guard.target_balance", "action.buff_lock_pay"]
    : ["action.steam_list", "action.pause_auto_sell"];
  const idx = steps.findIndex((step) => terminalIds.includes(step.module_id));
  return idx >= 0 ? idx : steps.length;
}

function strategyCanMoveStep(fromIndex, insertIndex) {
  const steps = strategiesState.draft?.steps || [];
  const step = steps[fromIndex];
  if (!step || isStrategyStepFixed(step)) return false;
  const normalizedInsert = Math.max(0, Math.min(insertIndex, steps.length));
  if (normalizedInsert === fromIndex || normalizedInsert === fromIndex + 1) return true;
  if (normalizedInsert > fromIndex) {
    for (let i = fromIndex + 1; i < normalizedInsert; i += 1) {
      if (isStrategyStepFixed(steps[i])) return false;
    }
  } else {
    for (let i = normalizedInsert; i < fromIndex; i += 1) {
      if (isStrategyStepFixed(steps[i])) return false;
    }
  }
  return true;
}

function strategyReorderStep(fromIndex, targetIndex, dropAfter = false) {
  const steps = strategiesState.draft?.steps || [];
  if (fromIndex < 0 || fromIndex >= steps.length) return false;
  const targetStep = steps[targetIndex];
  if (!targetStep || isStrategyStepFixed(targetStep)) return false;
  return strategyMoveStepToInsertIndex(fromIndex, targetIndex + (dropAfter ? 1 : 0));
}

function strategyMoveStepToInsertIndex(fromIndex, insertIndex) {
  const steps = strategiesState.draft?.steps || [];
  if (fromIndex < 0 || fromIndex >= steps.length) return false;
  let normalizedInsert = Math.max(0, Math.min(insertIndex, steps.length));
  if (!strategyCanMoveStep(fromIndex, normalizedInsert)) return false;
  if (normalizedInsert === fromIndex || normalizedInsert === fromIndex + 1) {
    strategiesState.selectedStep = fromIndex;
    return true;
  }
  const [item] = steps.splice(fromIndex, 1);
  if (normalizedInsert > fromIndex) normalizedInsert -= 1;
  steps.splice(normalizedInsert, 0, item);
  strategiesState.selectedStep = normalizedInsert;
  return true;
}

function strategyViewTitle() {
  return strategiesState.view === "sell" ? "出售策略" : "购入策略";
}

function strategyDraftComparable(draft = strategiesState.draft) {
  if (!draft) return null;
  const out = strategyClone(draft);
  delete out._local_id;
  delete out.created_at;
  delete out.updated_at;
  delete out.imported;
  return out;
}

function strategyDraftSignature(draft = strategiesState.draft) {
  return JSON.stringify(strategyDraftComparable(draft));
}

function strategyMarkDraftSnapshot() {
  strategiesState.draftSnapshot = strategyDraftSignature(strategiesState.draft);
}

function strategyHasUnsavedChanges() {
  const draft = strategiesState.draft;
  if (!draft) return false;
  return strategyDraftSignature(draft) !== strategiesState.draftSnapshot;
}

function strategyDiscardDraftChanges() {
  const draft = strategiesState.draft;
  if (!draft) return;
  const localDraft = strategyLocalDraft();
  if (localDraft && strategiesState.selectedId === localDraft._local_id) {
    strategiesState.draft = null;
    strategiesState.selectedId = strategiesState.active[strategiesState.view] || "";
    selectStrategyDraft(strategiesState.selectedId, false);
    return;
  }
  const saved = draft.id ? strategyById(draft.id) : null;
  if (saved) {
    strategiesState.draft = strategyClone(saved);
    strategyEnsureRequiredSteps(strategiesState.draft);
    strategiesState.selectedId = saved.id;
    strategyMarkDraftSnapshot();
  }
}

async function strategyConfirmLeave() {
  if (!strategyHasUnsavedChanges()) return true;
  const choice = await appModal({
    title: "保存策略改动？",
    message: "当前策略有未保存改动。离开前可以保存，或放弃本次编辑继续切换页面。",
    width: "500px",
    actions: [
      { label: "继续编辑", value: "cancel", kind: "secondary" },
      { label: "不保存", value: "discard", kind: "danger" },
      { label: "保存", value: "save", kind: "primary" },
    ],
  });
  if (choice === "save") {
    return await saveStrategyDraft();
  }
  if (choice === "discard") {
    strategyDiscardDraftChanges();
    renderStrategies();
    return true;
  }
  return false;
}

function strategyConfirm(message) {
  return appConfirm(message, { title: "风险确认", confirmText: "确认" });
}

function strategyResetParamsToDefaults(strategy = strategiesState.draft) {
  if (!strategy?.steps) return false;
  (strategy.steps || []).forEach((step) => {
    step.params = defaultParamsForModule(step.module_id);
  });
  return true;
}

async function loadStrategies(force = false) {
  if (strategiesState.loaded && !force) {
    renderStrategies();
    return;
  }
  try {
    const data = await fetchJson(API + "/strategies");
    strategiesState.strategies = data.strategies || [];
    strategiesState.modules = data.modules || [];
    strategiesState.active = data.active || { buy: "", sell: "" };
    strategiesState.limits = data.limits || strategiesState.limits;
    strategiesState.availableData = data.available_data || {};
    strategiesState.loaded = true;
    const localDraft = strategyLocalDraft();
    if (localDraft && strategiesState.selectedId === localDraft._local_id) {
      renderStrategies();
      return;
    }
    if (!strategiesState.selectedId) {
      strategiesState.selectedId = strategiesState.active[strategiesState.view] || "";
    }
    if (!strategyById(strategiesState.selectedId)) {
      strategiesState.selectedId = strategiesState.active[strategiesState.view] || "";
    }
    selectStrategyDraft(strategiesState.selectedId, false);
    renderStrategies();
  } catch (e) {
    toast("加载策略失败", e.message || "");
  }
}

async function switchStrategyView(view) {
  if (view !== strategiesState.view && !(await strategyConfirmLeave())) return false;
  strategiesState.view = view;
  strategiesState.selectedStep = 0;
  strategiesState.simulateResults = [];
  document.querySelectorAll(".strategy-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.strategyView === view);
  });
  const workbench = el("strategy-workbench");
  const modulesView = el("strategy-modules-view");
  if (view === "modules") {
    if (workbench) workbench.classList.add("hidden");
    if (modulesView) modulesView.classList.remove("hidden");
  } else {
    if (workbench) workbench.classList.remove("hidden");
    if (modulesView) modulesView.classList.add("hidden");
    const activeId = strategiesState.active[view] || "";
    const current = strategyById(strategiesState.selectedId);
    if (!current || current.strategy_type !== view) {
      selectStrategyDraft(activeId, false);
    }
  }
  renderStrategies();
  return true;
}

function selectStrategyDraft(id, rerender = true) {
  const localDraft = strategyLocalDraft();
  if (localDraft && id === localDraft._local_id) {
    strategiesState.selectedId = id;
    strategyEnsureRequiredSteps(localDraft);
    strategiesState.selectedStep = 0;
    strategiesState.simulateResults = [];
    if (rerender) renderStrategies();
    return;
  }
  const strategy = strategyById(id);
  if (!strategy) return;
  const savedDraft = strategyClone(strategy);
  const savedSnapshot = strategyDraftSignature(savedDraft);
  strategiesState.selectedId = id;
  strategiesState.draft = savedDraft;
  strategyEnsureRequiredSteps(strategiesState.draft);
  strategiesState.draftSnapshot = savedSnapshot;
  strategiesState.selectedStep = 0;
  strategiesState.simulateResults = [];
  if (rerender) renderStrategies();
}

function renderStrategies() {
  renderStrategyTabs();
  renderStrategyList();
  renderStrategyEditor();
  renderModulesView();
}

function renderStrategyTabs() {
  document.querySelectorAll(".strategy-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.strategyView === strategiesState.view);
  });
}

function renderStrategyList() {
  const list = el("strategy-list");
  const title = el("strategy-list-title");
  const pill = el("strategy-active-pill");
  if (title) title.textContent = strategyViewTitle();
  if (pill) pill.textContent = strategiesState.active[strategiesState.view] || "未启用";
  if (!list) return;
  const items = strategiesState.strategies.filter((s) => s.strategy_type === strategiesState.view);
  const localDraft = strategyLocalDraft();
  const localDraftItems = localDraft && localDraft.strategy_type === strategiesState.view ? [localDraft] : [];
  const customItems = [
    ...localDraftItems,
    ...items.filter((s) => s.origin !== "system"),
  ];
  const groups = [
    ["系统预设", items.filter((s) => s.origin === "system")],
    ["我的策略", customItems],
  ];
  list.innerHTML = groups.map(([label, group]) => `
    <div class="strategy-list-group">
      <div class="strategy-list-group-title">${escapeHtml(label)}</div>
      ${group.length ? group.map((s) => renderStrategyListItem(s)).join("") : `<div class="strategy-empty">暂无${escapeHtml(label)}</div>`}
    </div>
  `).join("");
  list.querySelectorAll(".strategy-list-item").forEach((node) => {
    node.addEventListener("click", async () => {
      if (node.dataset.id === strategiesState.selectedId) return;
      if (!(await strategyConfirmLeave())) return;
      selectStrategyDraft(node.dataset.id);
    });
  });
}

function renderStrategyListItem(strategy) {
  const itemId = strategy.id || strategy._local_id || "";
  const isLocalDraft = !strategy.id && !!strategy._local_id;
  const isActive = !isLocalDraft && strategiesState.active[strategy.strategy_type] === strategy.id;
  const isSelected = strategiesState.selectedId === itemId;
  const badge = isLocalDraft ? "未保存" : strategy.origin === "system" ? "系统" : "自定义";
  return `
    <button type="button" class="strategy-list-item ${isSelected ? "selected" : ""} ${isLocalDraft ? "draft" : ""}" data-id="${escapeHtml(itemId)}">
      <span>
        <strong>${escapeHtml(strategy.name)}</strong>
        <small>${escapeHtml(strategy.description || strategy.id || "尚未保存到策略库")}</small>
      </span>
      <em class="${isActive ? "active" : ""}">${isActive ? "启用中" : badge}</em>
    </button>
  `;
}

function renderStrategyEditor() {
  const draft = strategiesState.draft;
  const nameEl = el("strategy-editor-name");
  const originEl = el("strategy-editor-origin");
  const descEl = el("strategy-editor-desc");
  const stepsEl = el("strategy-steps");
  const addBtn = el("strategy-btn-add-step");
  const addFoot = addBtn?.closest(".strategy-editor-foot");
  if (!draft) {
    if (nameEl) nameEl.textContent = "选择一个策略";
    if (originEl) originEl.textContent = "—";
    if (descEl) descEl.textContent = "";
    if (stepsEl) stepsEl.innerHTML = "";
    if (addFoot) addFoot.classList.add("hidden");
    return;
  }
  if (nameEl) nameEl.textContent = draft.name || draft.id;
  const stepCount = (draft.steps || []).length;
  const stepLimit = strategyStepLimit(draft.strategy_type);
  if (originEl) {
    const originText = draft.origin === "system" ? "系统预设 · 可编辑参数" : "自定义策略";
    originEl.textContent = `${originText} · ${stepCount}/${stepLimit} 模块`;
  }
  if (descEl) descEl.textContent = draft.description || draft.id;
  if (addFoot) addFoot.classList.toggle("hidden", draft.origin === "system");
  if (addBtn) addBtn.disabled = draft.origin === "system" || stepCount >= stepLimit;
  renderStepList();
  renderSimulationResultsPanel();
  renderEditorButtons();
}

function renderEditorButtons() {
  const draft = strategiesState.draft;
  const readonly = !draft;
  const systemDraft = draft?.origin === "system";
  const active = draft && strategiesState.active[draft.strategy_type] === draft.id;
  const save = el("strategy-btn-save");
  const reset = el("strategy-btn-reset-defaults");
  const del = el("strategy-btn-delete");
  const act = el("strategy-btn-activate");
  if (save) save.disabled = readonly;
  if (reset) reset.disabled = readonly || !systemDraft;
  if (del) del.disabled = readonly || systemDraft;
  if (act) act.disabled = !draft || active;
}

function renderStepList() {
  const stepsEl = el("strategy-steps");
  const draft = strategiesState.draft;
  if (!stepsEl || !draft) return;
  const modules = strategyModulesById();
  const structureReadonly = draft.origin === "system";
  const paramsReadonly = false;
  const steps = draft.steps || [];
  if (!steps.length) {
    stepsEl.innerHTML = `<div class="strategy-empty">暂无模块，请使用“添加模块”选择策略步骤。</div>`;
    return;
  }
  stepsEl.innerHTML = steps.map((step, idx) => {
    const mod = modules.get(step.module_id) || {};
    const selected = strategiesState.selectedStep === idx;
    const fixed = isStrategyStepFixed(step, draft);
    const canMoveUp = !structureReadonly && !fixed && idx > 0 && strategyCanMoveStep(idx, idx - 1);
    const canMoveDown = !structureReadonly && !fixed && idx < draft.steps.length - 1 && strategyCanMoveStep(idx, idx + 2);
    return `
      <div class="strategy-step ${selected ? "selected" : ""} ${fixed ? "fixed" : ""} ${step.enabled === false ? "disabled" : ""}" data-step="${idx}">
        <div class="strategy-step-index">${idx + 1}</div>
        <div class="strategy-step-main">
          <strong>${escapeHtml(mod.name || step.module_id)}${fixed ? `<em class="strategy-step-fixed">固定</em>` : ""}</strong>
          <span>${escapeHtml(mod.description || step.module_id)}</span>
        </div>
        <div class="strategy-step-actions">
          <button type="button" class="icon-btn strategy-step-toggle" title="${step.enabled === false ? "启用" : "禁用"}" ${structureReadonly || fixed ? "disabled" : ""}>${step.enabled === false ? "○" : "✓"}</button>
          <button type="button" class="icon-btn strategy-step-up" title="上移" ${!canMoveUp ? "disabled" : ""}>↑</button>
          <button type="button" class="icon-btn strategy-step-down" title="下移" ${!canMoveDown ? "disabled" : ""}>↓</button>
          <button type="button" class="icon-btn strategy-step-copy" title="复制" ${structureReadonly || fixed ? "disabled" : ""}>⧉</button>
          <button type="button" class="icon-btn strategy-step-delete" title="删除" ${structureReadonly || fixed ? "disabled" : ""}>×</button>
        </div>
        ${selected ? renderStepParamEditor(step, mod, idx, paramsReadonly) : ""}
      </div>
    `;
  }).join("");
  stepsEl.querySelectorAll(".strategy-step").forEach((node) => {
    node.addEventListener("click", (e) => {
      if (Date.now() < strategySuppressClickUntil) return;
      const target = e.target;
      const idx = Number(node.dataset.step);
      if (target.closest(".strategy-step-toggle")) return toggleStep(idx);
      if (target.closest(".strategy-step-up")) return moveStep(idx, -1);
      if (target.closest(".strategy-step-down")) return moveStep(idx, 1);
      if (target.closest(".strategy-step-copy")) return copyStep(idx);
      if (target.closest(".strategy-step-delete")) return deleteStep(idx);
      if (target.closest(".strategy-step-params")) return;
      strategiesState.selectedStep = idx;
      renderStrategies();
    });
  });
  bindStrategyStepDrag(stepsEl, structureReadonly);
  stepsEl.querySelectorAll("[data-param-key]").forEach((input) => {
    const eventName = input.type === "checkbox" ? "change" : "input";
    input.addEventListener(eventName, () => updateParam(input));
  });
}

function toggleStep(idx) {
  const step = strategiesState.draft?.steps?.[idx];
  if (!step) return;
  if (isStrategyStepFixed(step)) {
    toast("固定模块不可禁用");
    return;
  }
  if (strategyIsLastEnabledPricingCore(idx)) {
    toast("无法禁用模块", "出售上架策略至少需要一个定价核心，可通过添加另一个定价模块来替换");
    return;
  }
  if (step.enabled === false) {
    const mod = strategyModulesById().get(step.module_id) || { id: step.module_id };
    const status = strategyModuleAddStatus(mod, { ignoreIndex: idx, existing: true });
    if (!status.ok) {
      toast("无法启用模块", status.reason);
      return;
    }
  }
  step.enabled = step.enabled === false;
  renderStrategies();
}

function moveStep(idx, delta) {
  const steps = strategiesState.draft?.steps || [];
  if (!steps[idx]) return;
  if (isStrategyStepFixed(steps[idx])) {
    toast("固定模块不可移动");
    return;
  }
  const target = idx + delta;
  if (target < 0 || target >= steps.length) return;
  const ok = strategyReorderStep(idx, target, delta > 0);
  if (!ok) {
    toast("无法移动模块", "固定模块会保持在关键位置");
    return;
  }
  renderStrategies();
}

function copyStep(idx) {
  const steps = strategiesState.draft?.steps || [];
  const step = steps[idx];
  if (!step) return;
  if (isStrategyStepFixed(step)) {
    toast("固定模块不可复制");
    return;
  }
  const mod = strategyModulesById().get(step.module_id) || { id: step.module_id };
  const status = strategyModuleAddStatus(mod);
  if (!status.ok) {
    toast("无法复制模块", status.reason);
    return;
  }
  steps.splice(idx + 1, 0, strategyClone(step));
  strategiesState.selectedStep = idx + 1;
  renderStrategies();
}

function deleteStep(idx) {
  const steps = strategiesState.draft?.steps || [];
  if (isStrategyStepFixed(steps[idx])) {
    toast("固定模块不可删除");
    return;
  }
  if (strategyIsLastEnabledPricingCore(idx)) {
    toast("无法删除模块", "出售上架策略至少需要一个定价核心，可通过添加另一个定价模块来替换");
    return;
  }
  steps.splice(idx, 1);
  strategiesState.selectedStep = Math.max(0, Math.min(idx, steps.length - 1));
  renderStrategies();
}

function strategyIsInteractiveTarget(target) {
  return !!target.closest("button, input, textarea, select, label, .strategy-step-params");
}

function strategyClearDropMarks() {
  document.querySelectorAll(".strategy-step.drop-before, .strategy-step.drop-after").forEach((node) => {
    node.classList.remove("drop-before", "drop-after");
  });
}

function strategyMeasureCollapsedStepHeight(node) {
  if (!node?.parentElement) return 56;
  const rect = node.getBoundingClientRect();
  const clone = node.cloneNode(true);
  clone.classList.remove("selected", "dragging", "drop-before", "drop-after");
  clone.querySelector(".strategy-step-params")?.remove();
  Object.assign(clone.style, {
    position: "absolute",
    visibility: "hidden",
    pointerEvents: "none",
    width: `${Math.max(1, rect.width)}px`,
    left: "-9999px",
    top: "0",
  });
  node.parentElement.appendChild(clone);
  const height = clone.getBoundingClientRect().height;
  clone.remove();
  return Number.isFinite(height) && height > 0 ? height : 56;
}

function strategyEnsureDragPlaceholder(height = 56) {
  if (strategyDragState?.placeholder) return strategyDragState.placeholder;
  const node = document.createElement("div");
  node.className = "strategy-step-placeholder";
  node.style.height = `${Math.max(44, Math.round(height))}px`;
  if (strategyDragState) strategyDragState.placeholder = node;
  return node;
}

function strategyInsertIndexToTarget(insertIndex) {
  const state = strategyDragState;
  const steps = strategiesState.draft?.steps || [];
  if (!state || !steps.length) return { targetIndex: -1, dropAfter: false };
  const normalized = Math.max(0, Math.min(insertIndex, steps.length));
  if (normalized === 0) return { targetIndex: 0, dropAfter: false };
  if (normalized >= steps.length) return { targetIndex: steps.length - 1, dropAfter: true };
  return { targetIndex: normalized, dropAfter: false };
}

function strategyFindDragInsertIndex(clientY) {
  const state = strategyDragState;
  const steps = strategiesState.draft?.steps || [];
  if (!state?.container) return -1;
  const nodes = Array.from(state.container.querySelectorAll(".strategy-step"))
    .filter((node) => node !== state.node)
    .map((node) => ({ node, index: Number(node.dataset.step), rect: node.getBoundingClientRect() }))
    .filter((item) => Number.isInteger(item.index));
  let insertIndex = steps.length;
  for (const item of nodes) {
    if (clientY < item.rect.top + item.rect.height / 2) {
      insertIndex = item.index;
      break;
    }
  }
  insertIndex = Math.max(0, Math.min(insertIndex, steps.length));
  return strategyCanMoveStep(state.fromIndex, insertIndex) ? insertIndex : -1;
}

function strategyPlaceDragPlaceholder(insertIndex) {
  const state = strategyDragState;
  if (!state?.container) return;
  const placeholder = strategyEnsureDragPlaceholder(state.placeholderHeight || 56);
  const steps = strategiesState.draft?.steps || [];
  const normalized = Math.max(0, Math.min(insertIndex, steps.length));
  const beforeNode = Array.from(state.container.querySelectorAll(".strategy-step"))
    .find((node) => node !== state.node && Number(node.dataset.step) >= normalized);
  if (beforeNode) {
    state.container.insertBefore(placeholder, beforeNode);
  } else {
    state.container.appendChild(placeholder);
  }
}

function strategyCleanupDrag() {
  if (strategyDragState?.timer) clearTimeout(strategyDragState.timer);
  strategyDragState?.placeholder?.remove();
  document.removeEventListener("pointermove", strategyHandlePointerMove, true);
  document.removeEventListener("pointerup", strategyHandlePointerEnd, true);
  document.removeEventListener("pointercancel", strategyHandlePointerEnd, true);
  document.body.classList.remove("strategy-dragging-active");
  document.querySelectorAll(".strategy-step.dragging").forEach((node) => node.classList.remove("dragging"));
  strategyClearDropMarks();
  strategyDragState = null;
}

function strategyBeginDrag() {
  const state = strategyDragState;
  if (!state) return;
  state.active = true;
  state.placeholderHeight = strategyMeasureCollapsedStepHeight(state.node);
  state.node.classList.add("dragging");
  document.body.classList.add("strategy-dragging-active");
  strategySuppressClickUntil = Date.now() + 500;
}

function strategyHandlePointerMove(e) {
  const state = strategyDragState;
  if (!state || e.pointerId !== state.pointerId) return;
  const dx = Math.abs(e.clientX - state.startX);
  const dy = Math.abs(e.clientY - state.startY);
  if (!state.active && (dx > 12 || dy > 12)) {
    strategyCleanupDrag();
    return;
  }
  if (!state.active) return;
  e.preventDefault();
  strategyClearDropMarks();
  state.targetIndex = -1;
  state.dropAfter = false;
  const insertIndex = strategyFindDragInsertIndex(e.clientY);
  if (insertIndex < 0) {
    state.placeholder?.remove();
    return;
  }
  const target = strategyInsertIndexToTarget(insertIndex);
  const targetNode = state.container?.querySelector(`.strategy-step[data-step="${target.targetIndex}"]`);
  targetNode?.classList.add(target.dropAfter ? "drop-after" : "drop-before");
  strategyPlaceDragPlaceholder(insertIndex);
  state.insertIndex = insertIndex;
  state.targetIndex = target.targetIndex;
  state.dropAfter = target.dropAfter;
}

function strategyHandlePointerEnd(e) {
  const state = strategyDragState;
  if (!state || e.pointerId !== state.pointerId) return;
  const shouldReorder = state.active && state.targetIndex >= 0;
  const fromIndex = state.fromIndex;
  const insertIndex = state.insertIndex;
  strategyCleanupDrag();
  if (shouldReorder) {
    if (!strategyMoveStepToInsertIndex(fromIndex, insertIndex)) {
      toast("无法移动模块", "固定模块会保持在关键位置");
      return;
    }
    renderStrategies();
  }
}

function bindStrategyStepDrag(stepsEl, readonly) {
  if (!stepsEl || readonly) return;
  stepsEl.querySelectorAll(".strategy-step").forEach((node) => {
    node.addEventListener("pointerdown", (e) => {
      if (e.button !== undefined && e.button !== 0) return;
      if (strategyIsInteractiveTarget(e.target)) return;
      const idx = Number(node.dataset.step);
      const step = strategiesState.draft?.steps?.[idx];
      if (!step || isStrategyStepFixed(step)) return;
      strategyCleanupDrag();
      strategyDragState = {
        pointerId: e.pointerId,
        fromIndex: idx,
        insertIndex: idx,
        targetIndex: -1,
        dropAfter: false,
        node,
        container: stepsEl,
        startX: e.clientX,
        startY: e.clientY,
        active: false,
        timer: setTimeout(strategyBeginDrag, 240),
      };
      document.addEventListener("pointermove", strategyHandlePointerMove, true);
      document.addEventListener("pointerup", strategyHandlePointerEnd, true);
      document.addEventListener("pointercancel", strategyHandlePointerEnd, true);
    });
  });
}

function renderStepParamEditor(step, mod, idx, readonly) {
  const schema = mod.params_schema || {};
  const fields = Object.keys(schema);
  return `
    <div class="strategy-step-params">
      <div class="strategy-step-param-head">
        <div>
          <span class="strategy-label">参数</span>
          <strong>${escapeHtml(mod.name || step.module_id)}</strong>
        </div>
        <div class="strategy-module-id">${escapeHtml(step.module_id)}</div>
      </div>
      <div class="strategy-step-param-desc">${escapeHtml(mod.description || "无说明")}</div>
      ${fields.length ? `
        <div class="strategy-step-param-grid">
          ${fields.map((key) => renderParamField(key, schema[key], step.params || {}, readonly, idx)).join("")}
        </div>
      ` : `<div class="strategy-empty">此模块无需参数。</div>`}
    </div>
  `;
}

function renderParamField(key, meta, params, readonly, stepIndex) {
  const fallback = meta && Object.prototype.hasOwnProperty.call(meta, "default") ? meta.default : "";
  const value = params[key] ?? fallback ?? "";
  const type = meta?.type || "string";
  const label = meta?.label || key.replace(/_/g, " ");
  const attrs = [
    `data-step-index="${stepIndex}"`,
    `data-param-key="${escapeHtml(key)}"`,
    `data-param-type="${escapeHtml(type)}"`,
    readonly ? "disabled" : "",
  ];
  if (meta?.min !== undefined) attrs.push(`min="${escapeHtml(meta.min)}"`);
  if (meta?.max !== undefined) attrs.push(`max="${escapeHtml(meta.max)}"`);
  if (fallback !== "") attrs.push(`placeholder="${escapeHtml(fallback)}"`);
  if (type === "array") {
    const text = Array.isArray(value) ? value.join("\n") : "";
    return `<label class="strategy-param"><span>${escapeHtml(label)}</span><textarea ${attrs.join(" ")}>${escapeHtml(text)}</textarea></label>`;
  }
  if (type === "boolean") {
    return `<label class="strategy-param strategy-param--check"><input type="checkbox" ${attrs.join(" ")} ${value ? "checked" : ""} /><span>${escapeHtml(label)}</span></label>`;
  }
  const inputType = type === "integer" || type === "number" ? "number" : "text";
  const step = type === "integer" ? "1" : "0.01";
  return `<label class="strategy-param"><span>${escapeHtml(label)}</span><input type="${inputType}" step="${step}" ${attrs.join(" ")} value="${escapeHtml(value)}" /></label>`;
}

function updateParam(input) {
  const idx = Number.isInteger(Number(input.dataset.stepIndex)) ? Number(input.dataset.stepIndex) : strategiesState.selectedStep;
  const step = strategiesState.draft?.steps?.[idx];
  if (!step) return;
  step.params = step.params || {};
  const key = input.dataset.paramKey;
  const type = input.dataset.paramType;
  if (type !== "boolean" && input.value.trim() === "") {
    delete step.params[key];
    return;
  }
  if (type === "array") {
    step.params[key] = input.value.split(/\n/).map((s) => s.trim()).filter(Boolean);
  } else if (type === "integer") {
    const parsed = parseInt(input.value, 10);
    if (Number.isFinite(parsed)) step.params[key] = parsed;
  } else if (type === "number") {
    const parsed = parseFloat(input.value);
    if (Number.isFinite(parsed)) step.params[key] = parsed;
  } else if (type === "boolean") {
    step.params[key] = !!input.checked;
  } else {
    step.params[key] = input.value;
  }
}

function defaultParamsForModule(moduleId) {
  const mod = strategyModulesById().get(moduleId) || {};
  const schema = mod.params_schema || {};
  const params = {};
  Object.keys(schema).forEach((key) => {
    const meta = schema[key] || {};
    if (Object.prototype.hasOwnProperty.call(meta, "default")) {
      params[key] = strategyClone(meta.default);
    }
  });
  return params;
}

function renderSimulationResultsPanel() {
  const box = el("strategy-sim-results");
  if (!box) return;
  if (!strategiesState.simulateResults.length) {
    box.innerHTML = "";
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = `
    <div class="strategy-sim-title">
      <span class="strategy-label">模拟结果</span>
      <strong>真实交易动作已跳过</strong>
    </div>
    ${strategiesState.simulateResults.map((r) => `
      <div class="strategy-sim-row ${escapeHtml(r.status)}">
        <span>${escapeHtml(r.module_name || r.module_id)}</span>
        <em>${escapeHtml(r.status)}</em>
        <small>${escapeHtml(r.reason || "")}</small>
      </div>
    `).join("")}
  `;
}

function addStep(moduleId) {
  if (!strategiesState.draft || strategiesState.draft.origin === "system") return false;
  const mod = strategyModulesById().get(moduleId);
  if (!mod) {
    toast("无法添加模块", "模块不存在");
    return false;
  }
  const status = strategyModuleAddStatus(mod, { allowPricingReplacement: true });
  if (!status.ok) {
    toast("无法添加模块", status.reason);
    return false;
  }
  strategiesState.draft.steps = strategiesState.draft.steps || [];
  let insertIndex = status.replacementIndexes?.length
    ? Math.min(...status.replacementIndexes)
    : strategyDefaultInsertIndex(strategiesState.draft);
  (status.replacementIndexes || [])
    .slice()
    .sort((a, b) => b - a)
    .forEach((idx) => {
      strategiesState.draft.steps.splice(idx, 1);
      if (idx < insertIndex) insertIndex -= 1;
    });
  strategiesState.draft.steps.splice(insertIndex, 0, { module_id: moduleId, enabled: true, params: defaultParamsForModule(moduleId) });
  strategiesState.selectedStep = insertIndex;
  renderStrategies();
  if (status.replacementHint) toast("定价模块已替换", status.replacementHint);
  return true;
}

async function openModulePicker() {
  const draft = strategiesState.draft;
  if (!draft || draft.origin === "system") return;
  const modules = (strategiesState.modules || []).filter((m) => !m.hidden && (m.strategy_types || []).includes(strategiesState.view));
  if (!modules.length) {
    toast("暂无可用模块");
    return;
  }
  const content = document.createElement("div");
  content.className = "strategy-module-picker";
  const renderPicker = (keyword = "") => {
    const kw = keyword.trim().toLowerCase();
    const filtered = modules.filter((m) => {
      const text = `${m.id} ${m.name || ""} ${m.description || ""} ${m.category || ""}`.toLowerCase();
      return !kw || text.includes(kw);
    });
    content.querySelector(".strategy-module-picker-list").innerHTML = filtered.map((m) => {
      const status = strategyModuleAddStatus(m, { allowPricingReplacement: true });
      const dependencyHint = status.ok ? strategyModuleDependencyHint(m) : "";
      const hint = status.ok ? (status.replacementHint || dependencyHint) : status.reason;
      return `
        <button type="button" class="strategy-module-choice ${status.ok ? "" : "disabled"}" data-module-id="${escapeHtml(m.id)}" data-disabled="${status.ok ? "0" : "1"}" data-reason="${escapeHtml(status.reason || "")}">
          <span class="strategy-module-id">${escapeHtml(m.id)}</span>
          <strong>${escapeHtml(m.name || m.id)}</strong>
          <small>${escapeHtml(m.description || "")}</small>
          <span class="strategy-module-choice-foot">
            <em>${escapeHtml(m.category || "module")}</em>
            ${hint ? `<b class="${status.ok ? "hint" : "blocked"}">${escapeHtml(hint)}</b>` : ""}
          </span>
        </button>
      `;
    }).join("") || `<div class="strategy-empty">没有匹配的模块。</div>`;
  };
  content.innerHTML = `
    <div class="strategy-module-picker-head">
      <input type="search" class="strategy-module-search" placeholder="搜索模块名称、分类或 ID" />
    </div>
    <div class="strategy-module-picker-list"></div>
  `;
  renderPicker();
  await appModal({
    title: "添加模块",
    message: "选择一个模块加入当前策略，添加后可在步骤卡片里编辑参数。",
    content,
    width: "760px",
    actions: [{ label: "取消", value: false, kind: "secondary" }],
    onOpen: (host, close) => {
      const search = host.querySelector(".strategy-module-search");
      search?.addEventListener("input", () => renderPicker(search.value));
      host.querySelector(".strategy-module-picker-list")?.addEventListener("click", (e) => {
        const btn = e.target.closest(".strategy-module-choice");
        if (!btn) return;
        if (btn.dataset.disabled === "1") {
          toast("无法添加模块", btn.dataset.reason || "");
          return;
        }
        if (addStep(btn.dataset.moduleId)) close(true);
      });
      search?.focus();
    },
  });
}


function strategyModuleKindLabel(module) {
  if (module.origin !== "user") return "内置模块";
  if (module.module_kind === "declarative") return module.enabled === false ? "声明式 · 已禁用" : "声明式 · 可启用";
  return "外部代码 · 仅登记";
}

function strategyModuleMetaLine(module) {
  const parts = [
    (module.strategy_types || []).join(" / "),
    module.category || "module",
    `最多 ${module.max_instances || 1} 个`,
  ];
  if (module.origin === "user" && module.stages?.length) parts.push(`阶段 ${module.stages.join(" / ")}`);
  if (module.uses_modules?.length) parts.push(`读取 ${module.uses_modules.map(strategyModuleName).join(" / ")}`);
  return parts.filter(Boolean).join(" · ");
}

function strategyModuleDataSummary(module) {
  const outputs = module.data_outputs || {};
  const keys = Object.keys(outputs);
  if (!keys.length) return "";
  return `<small class="strategy-module-data">输出 ${keys.slice(0, 4).map(escapeHtml).join(" / ")}${keys.length > 4 ? " ..." : ""}</small>`;
}

function strategyAvailableDataSummary() {
  const data = strategiesState.availableData || {};
  const stages = data.stages || {};
  const ops = data.operators || [];
  return `
    <div class="strategy-module-help">
      <strong>用户声明式模块</strong>
      <span>可读取 item、buy_record、listing、config 和前置模块 outputs；支持 ${escapeHtml(ops.slice(0, 8).join(" / "))}${ops.length > 8 ? " ..." : ""}。</span>
      <span>可挂载阶段：购入 ${escapeHtml((stages.buy || []).join(" / ") || "-")}；出售 ${escapeHtml((stages.sell || []).join(" / ") || "-")}。</span>
    </div>
  `;
}

function renderModulesView() {
  const box = el("strategy-modules-list");
  if (!box) return;
  const rows = (strategiesState.modules || []).filter((m) => !m.hidden).map((m) => `
    <div class="strategy-module-row ${m.origin === "user" ? "user" : "builtin"}">
      <div>
        <strong>${escapeHtml(m.name || m.id)}</strong>
        <span>${escapeHtml(m.id)}</span>
        <small>${escapeHtml(strategyModuleMetaLine(m))}</small>
        ${strategyModuleDataSummary(m)}
      </div>
      <em>${escapeHtml(strategyModuleKindLabel(m))}</em>
    </div>
  `).join("");
  box.innerHTML = strategyAvailableDataSummary() + rows;
}

function strategyTemplateLabel(strategy) {
  const prefix = strategy.origin === "system" ? "系统" : "自定义";
  return `${prefix} · ${strategy.name || strategy.id}`;
}

async function createStrategyDraft() {
  if (!(await strategyConfirmLeave())) return;
  const type = strategiesState.view === "sell" ? "sell" : "buy";
  const templates = (strategiesState.strategies || []).filter((s) => s.strategy_type === type);
  const current = strategyById(strategiesState.selectedId);
  const defaultTemplate = (current && current.strategy_type === type ? current : null)
    || strategyById(strategiesState.active[type])
    || templates.find((s) => s.origin === "system")
    || templates[0];
  const content = document.createElement("div");
  content.className = "strategy-create-dialog";
  content.innerHTML = `
    <label class="strategy-param">
      <span>策略名称</span>
      <input type="text" class="strategy-create-name" value="${escapeHtml(`我的${type === "sell" ? "出售" : "购入"}策略`)}" />
    </label>
    <div class="strategy-create-options">
      <button type="button" class="strategy-create-option active" data-mode="template">
        <strong>从模板新建</strong>
        <small>复制一个现有策略作为起点</small>
      </button>
      <button type="button" class="strategy-create-option" data-mode="blank">
        <strong>从空白新建</strong>
        <small>只保留必要的固定模块</small>
      </button>
    </div>
    <label class="strategy-param strategy-template-field">
      <span>模板</span>
      <select class="strategy-create-template">
        ${templates.map((s) => `<option value="${escapeHtml(s.id)}" ${s.id === defaultTemplate?.id ? "selected" : ""}>${escapeHtml(strategyTemplateLabel(s))}</option>`).join("")}
      </select>
    </label>
  `;
  const result = await appModal({
    title: `新建${type === "sell" ? "出售" : "购入"}策略`,
    message: "",
    content,
    width: "560px",
    actions: [
      { label: "取消", value: false, kind: "secondary" },
      { label: "创建草稿", value: true, kind: "primary" },
    ],
    onOpen: (host) => {
      const options = Array.from(host.querySelectorAll(".strategy-create-option"));
      const templateField = host.querySelector(".strategy-template-field");
      const templateSelect = host.querySelector(".strategy-create-template");
      const syncMode = (mode) => {
        if (templateField) templateField.classList.toggle("muted", mode === "blank");
        if (templateSelect) templateSelect.disabled = mode === "blank";
      };
      options.forEach((btn) => {
        btn.addEventListener("click", () => {
          options.forEach((item) => item.classList.toggle("active", item === btn));
          syncMode(btn.dataset.mode || "template");
        });
      });
      syncMode("template");
      host.querySelector(".strategy-create-name")?.focus();
    },
  });
  if (!result) return;
  const mode = content.querySelector(".strategy-create-option.active")?.dataset.mode || "template";
  const name = (content.querySelector(".strategy-create-name")?.value || "").trim();
  if (!name) {
    toast("策略名称不能为空");
    return;
  }
  const templateId = content.querySelector(".strategy-create-template")?.value || defaultTemplate?.id || "";
  const base = mode === "template" ? strategyById(templateId) : null;
  const draft = base
    ? strategyClone(base)
    : { strategy_type: type, origin: "custom", readonly: false, description: "从空白策略新建", steps: strategyBlankSteps(type) };
  delete draft.id;
  draft.name = name;
  draft.origin = "custom";
  draft.readonly = false;
  draft.strategy_type = type;
  draft.description = base ? `由「${base.name || base.id}」新建` : draft.description;
  draft._local_id = strategyLocalDraftId(type);
  strategyEnsureRequiredSteps(draft);
  strategiesState.draft = draft;
  strategiesState.selectedId = draft._local_id;
  strategiesState.draftSnapshot = "";
  strategiesState.selectedStep = 0;
  strategiesState.simulateResults = [];
  renderStrategies();
}

async function saveStrategyDraft() {
  const draft = strategiesState.draft;
  if (!draft) return false;
  try {
    strategyEnsureRequiredSteps(draft);
    const payload = strategyClone(draft);
    delete payload._local_id;
    const res = await fetchJson(API + "/strategies", { method: "POST", body: JSON.stringify({ strategy: payload }) });
    if (!res.ok) throw new Error(res.error || "保存失败");
    toast(draft.origin === "system" ? "默认策略参数已保存" : "策略已保存");
    strategiesState.loaded = false;
    strategiesState.selectedId = res.strategy.id;
    await loadStrategies(true);
    return true;
  } catch (e) {
    toast("保存失败", e.message || "");
    return false;
  }
}

async function copyCurrentStrategy() {
  const draft = strategiesState.draft;
  if (!draft) return;
  if (!(await strategyConfirm("复制策略后会生成一份可编辑的自定义策略。自定义策略可能影响自动交易，请先模拟运行。"))) return;
  const copy = strategyClone(draft);
  delete copy.id;
  copy.origin = "custom";
  copy.readonly = false;
  copy.name = `${draft.name} 副本`;
  strategiesState.draft = copy;
  await saveStrategyDraft();
}

async function resetSystemStrategyDefaults() {
  const draft = strategiesState.draft;
  if (!draft || draft.origin !== "system") return;
  const ok = await appConfirm(
    "将把当前系统策略的所有模块参数恢复为内置默认值，并立即保存到配置。系统策略的模块结构不会改变。",
    { title: "恢复默认参数", confirmText: "恢复并保存", danger: true, width: "520px" },
  );
  if (!ok) return;
  strategyResetParamsToDefaults(draft);
  renderStrategies();
  await saveStrategyDraft();
}

function strategyStableValue(value) {
  if (Array.isArray(value)) return value.map(strategyStableValue);
  if (value && typeof value === "object") {
    return Object.keys(value).sort().reduce((out, key) => {
      out[key] = strategyStableValue(value[key]);
      return out;
    }, {});
  }
  return value;
}

function strategyStableString(value) {
  return JSON.stringify(strategyStableValue(value ?? null));
}

function strategyParamLabel(moduleId, key) {
  const schema = strategyModulesById().get(moduleId)?.params_schema || {};
  return schema[key]?.label || key.replace(/_/g, " ");
}

function strategyFormatParamValue(value) {
  if (Array.isArray(value)) return value.length ? value.join("、") : "空";
  if (value === undefined) return "未设置";
  if (value === null) return "空";
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  return String(value);
}

function strategyEnabledSteps(strategy) {
  return (strategy?.steps || []).filter((step) => step.enabled !== false);
}

function strategyStepMap(strategy) {
  const map = new Map();
  (strategy?.steps || []).forEach((step) => {
    if (!map.has(step.module_id)) map.set(step.module_id, step);
  });
  return map;
}

function strategyParamDiffLine(moduleId, beforeParams = {}, afterParams = {}) {
  const keys = Array.from(new Set([...Object.keys(beforeParams || {}), ...Object.keys(afterParams || {})]));
  const changed = keys.filter((key) => strategyStableString(beforeParams?.[key]) !== strategyStableString(afterParams?.[key]));
  if (!changed.length) return "";
  const detail = changed.slice(0, 3).map((key) => {
    const before = strategyFormatParamValue(beforeParams?.[key]);
    const after = strategyFormatParamValue(afterParams?.[key]);
    return `${strategyParamLabel(moduleId, key)}：${before} → ${after}`;
  }).join("；");
  return changed.length > 3 ? `${detail}；另 ${changed.length - 3} 项` : detail;
}

function strategyBuildActivationDiff(fromStrategy, toStrategy) {
  const rows = [];
  const fromEnabled = strategyEnabledSteps(fromStrategy);
  const toEnabled = strategyEnabledSteps(toStrategy);
  const fromIds = fromEnabled.map((step) => step.module_id);
  const toIds = toEnabled.map((step) => step.module_id);
  const fromSet = new Set(fromIds);
  const toSet = new Set(toIds);
  const added = toIds.filter((id) => !fromSet.has(id));
  const removed = fromIds.filter((id) => !toSet.has(id));
  if (!fromStrategy) {
    rows.push({ kind: "info", title: "当前没有启用策略作为对比", detail: "将直接启用所选策略。" });
  }
  if (added.length) {
    rows.push({ kind: "add", title: "新增启用模块", detail: added.map(strategyModuleName).join("、") });
  }
  if (removed.length) {
    rows.push({ kind: "remove", title: "移除启用模块", detail: removed.map(strategyModuleName).join("、") });
  }
  const sharedFrom = fromIds.filter((id) => toSet.has(id));
  const sharedTo = toIds.filter((id) => fromSet.has(id));
  if (sharedFrom.length > 1 && strategyStableString(sharedFrom) !== strategyStableString(sharedTo)) {
    rows.push({ kind: "order", title: "模块顺序变化", detail: sharedTo.map(strategyModuleName).join(" → ") });
  }
  const beforeMap = strategyStepMap(fromStrategy);
  const afterMap = strategyStepMap(toStrategy);
  const paramRows = [];
  for (const [moduleId, afterStep] of afterMap.entries()) {
    const beforeStep = beforeMap.get(moduleId);
    if (!beforeStep) continue;
    const line = strategyParamDiffLine(moduleId, beforeStep.params || {}, afterStep.params || {});
    if (line) paramRows.push(`${strategyModuleName(moduleId)}：${line}`);
  }
  paramRows.slice(0, 5).forEach((detail) => {
    rows.push({ kind: "param", title: "参数变化", detail });
  });
  if (paramRows.length > 5) {
    rows.push({ kind: "param", title: "更多参数变化", detail: `还有 ${paramRows.length - 5} 个模块的参数发生变化。` });
  }
  if (!rows.length) {
    rows.push({ kind: "same", title: "无明显差异", detail: "模块结构和参数与当前启用策略一致。" });
  }
  return rows;
}

function strategyActivationDiffContent(draft) {
  const activeId = strategiesState.active[draft.strategy_type] || "";
  const activeStrategy = strategyById(activeId);
  const rows = strategyBuildActivationDiff(activeStrategy, draft);
  const content = document.createElement("div");
  content.className = "strategy-activation-diff";
  content.innerHTML = `
    <div class="strategy-activation-target">
      <span>当前启用</span>
      <strong>${escapeHtml(activeStrategy?.name || activeId || "未启用")}</strong>
      <span>即将启用</span>
      <strong>${escapeHtml(draft.name || draft.id)}</strong>
    </div>
    <div class="strategy-activation-diff-list">
      ${rows.map((row) => `
        <div class="strategy-activation-diff-row ${escapeHtml(row.kind)}">
          <strong>${escapeHtml(row.title)}</strong>
          <span>${escapeHtml(row.detail)}</span>
        </div>
      `).join("")}
    </div>
  `;
  return content;
}

function confirmStrategyActivation(draft) {
  const custom = draft.origin !== "system";
  return appModal({
    title: custom ? "启用自定义策略" : "启用系统策略",
    message: custom
      ? "自定义策略会直接影响自动买入或出售，可能造成亏损、误买、误卖或错过交易机会。请确认你已理解风险并完成模拟运行。"
      : "启用系统策略会切换当前自动交易行为。请确认下方差异。",
    content: strategyActivationDiffContent(draft),
    variant: "warning",
    width: "680px",
    actions: [
      { label: "取消", value: false, kind: "secondary" },
      { label: custom ? "我理解风险并启用" : "确认启用", value: true, kind: "primary" },
    ],
  });
}

async function activateCurrentStrategy() {
  const draft = strategiesState.draft;
  if (!draft) return;
  if (!(await confirmStrategyActivation(draft))) return;
  try {
    if ((draft.origin !== "system" || strategyHasUnsavedChanges()) && !(await saveStrategyDraft())) return;
    const id = strategiesState.draft?.id || strategiesState.selectedId;
    if (!id) throw new Error("策略尚未保存");
    const res = await fetchJson(API + `/strategies/${encodeURIComponent(id)}/activate`, {
      method: "POST",
      body: JSON.stringify({ risk_confirmed: true }),
    });
    if (!res.ok) throw new Error(res.error || "启用失败");
    toast("策略已启用");
    strategiesState.loaded = false;
    strategiesState.selectedId = id;
    await loadStrategies(true);
  } catch (e) {
    toast("启用失败", e.message || "");
  }
}

async function deleteCurrentStrategy() {
  const draft = strategiesState.draft;
  if (!draft || draft.origin === "system") return;
  if (!(await appConfirm(`确定删除策略「${draft.name}」？`, { title: "删除策略", danger: true, confirmText: "删除" }))) return;
  if (!draft.id) {
    toast("草稿已丢弃");
    strategiesState.draft = null;
    strategiesState.selectedId = "";
    selectStrategyDraft(strategiesState.active[strategiesState.view] || "", false);
    renderStrategies();
    return;
  }
  try {
    const res = await fetchJson(API + `/strategies/${encodeURIComponent(draft.id)}`, { method: "DELETE" });
    if (!res.ok) throw new Error(res.error || "删除失败");
    toast("策略已删除");
    strategiesState.loaded = false;
    strategiesState.selectedId = "";
    await loadStrategies(true);
  } catch (e) {
    toast("删除失败", e.message || "");
  }
}

async function simulateCurrentStrategy() {
  const draft = strategiesState.draft;
  if (!draft) return;
  try {
    strategyEnsureRequiredSteps(draft);
    const res = await fetchJson(API + "/strategies/simulate", {
      method: "POST",
      body: JSON.stringify({ strategy: draft }),
    });
    if (!res.ok) throw new Error((res.errors || [res.error || "模拟失败"]).join("；"));
    strategiesState.simulateResults = res.results || [];
    renderSimulationResultsPanel();
    toast("模拟完成", "真实交易动作已跳过");
  } catch (e) {
    toast("模拟失败", e.message || "");
  }
}

async function exportCurrentStrategy() {
  const draft = strategiesState.draft;
  if (!draft) return;
  try {
    let payload = strategyClone(draft);
    delete payload._local_id;
    if (draft.id) {
      const res = await fetchJson(API + `/strategies/${encodeURIComponent(draft.id)}/export`);
      if (res.ok) payload = res.strategy;
    }
    downloadJson(`${draft.name || "strategy"}.json`, payload);
  } catch (e) {
    toast("导出失败", e.message || "");
  }
}

function downloadJson(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.replace(/[\\/:*?"<>|]/g, "_");
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function importStrategyFile(file) {
  if (!file) return;
  if (!(await strategyConfirm("导入策略会保存为草稿，不会自动启用。请在启用前检查模块和参数。"))) return;
  try {
    const json = JSON.parse(await file.text());
    const res = await fetchJson(API + "/strategies/import", {
      method: "POST",
      body: JSON.stringify({ strategy: json }),
    });
    if (!res.ok) throw new Error(res.error || "导入失败");
    toast("策略已导入", "已保存为自定义草稿");
    strategiesState.view = res.strategy.strategy_type || strategiesState.view;
    strategiesState.selectedId = res.strategy.id;
    strategiesState.loaded = false;
    await loadStrategies(true);
  } catch (e) {
    toast("导入失败", e.message || "");
  } finally {
    const input = el("strategy-import-file");
    if (input) input.value = "";
  }
}


async function importStrategyModuleFile(file) {
  if (!file) return;
  if (!(await strategyConfirm("导入用户模块会进入模块库。声明式规则可在策略中启用；带 code/source/entrypoint 的外部代码模块只登记展示，不会执行。"))) return;
  try {
    const json = JSON.parse(await file.text());
    const res = await fetchJson(API + "/strategy-modules/import", {
      method: "POST",
      body: JSON.stringify({ manifest: json }),
    });
    if (!res.ok) throw new Error(res.error || "导入失败");
    toast("模块已导入", res.module?.module_kind === "declarative" ? "声明式模块可加入自定义策略" : "外部代码模块仅登记展示");
    strategiesState.loaded = false;
    await loadStrategies(true);
  } catch (e) {
    toast("模块导入失败", e.message || "");
  } finally {
    const input = el("strategy-module-import-file");
    if (input) input.value = "";
  }
}

function bindStrategyEvents() {
  document.querySelectorAll(".strategy-tab").forEach((btn) => {
    btn.addEventListener("click", () => switchStrategyView(btn.dataset.strategyView));
  });
  el("strategy-btn-refresh")?.addEventListener("click", async () => {
    if (await strategyConfirmLeave()) loadStrategies(true);
  });
  el("strategy-btn-new")?.addEventListener("click", createStrategyDraft);
  el("strategy-btn-copy")?.addEventListener("click", copyCurrentStrategy);
  el("strategy-btn-reset-defaults")?.addEventListener("click", resetSystemStrategyDefaults);
  el("strategy-btn-save")?.addEventListener("click", saveStrategyDraft);
  el("strategy-btn-activate")?.addEventListener("click", activateCurrentStrategy);
  el("strategy-btn-delete")?.addEventListener("click", deleteCurrentStrategy);
  el("strategy-btn-simulate")?.addEventListener("click", simulateCurrentStrategy);
  el("strategy-btn-export")?.addEventListener("click", exportCurrentStrategy);
  el("strategy-btn-add-step")?.addEventListener("click", openModulePicker);
  el("strategy-btn-import")?.addEventListener("click", async () => {
    if (await strategyConfirmLeave()) el("strategy-import-file")?.click();
  });
  el("strategy-import-file")?.addEventListener("change", (e) => importStrategyFile(e.target.files?.[0]));
  el("strategy-btn-import-module")?.addEventListener("click", () => el("strategy-module-import-file")?.click());
  el("strategy-module-import-file")?.addEventListener("change", (e) => importStrategyModuleFile(e.target.files?.[0]));
}

window.strategyConfirmLeave = strategyConfirmLeave;
window.addEventListener("beforeunload", (e) => {
  if (!strategyHasUnsavedChanges()) return;
  e.preventDefault();
  e.returnValue = "";
});

document.addEventListener("DOMContentLoaded", bindStrategyEvents);
