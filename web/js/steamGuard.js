
let steamGuardCode = '';
let steamGuardPeriod = 30;
let steamGuardServerOffsetMs = 0;
let steamGuardTimer = null;
let steamGuardVisible = false;
let steamGuardSlice = null;
let steamGuardHideTimer = null;
function updateSteamGuardDisplay() {
  const textEl = el("steam-code-text");
  const subEl = el("steam-code-subtitle");
  const tipEl = el("steam-token-tip");
  const ringEl = el("steam-progress-circle");
  if (!textEl || !subEl || !tipEl || !ringEl) return;
  if (!steamGuardVisible) {
    textEl.textContent = "-----";
    subEl.textContent = "点击显示令牌";
    tipEl.textContent = "您的账号受到 Steam Guard 保护";
    ringEl.style.opacity = "0";
  } else {
    textEl.textContent = steamGuardCode || "-----";
    subEl.textContent = "点击复制";
    tipEl.textContent = "令牌将在倒计时结束后自动刷新";
    ringEl.style.opacity = "1";
  }
}
async function refreshSteamGuardCode() {
  try {
    const d = await fetchJson(API + "/steam_guard");
    if (!d.ok) {
      throw new Error(d.error || "获取失败");
    }
    steamGuardCode = d.code || "";
    const serverTs = d.server_time || Math.floor(Date.now() / 1000);
    const period = d.period || 30;
    steamGuardPeriod = period;
    const nowMs = Date.now();
    steamGuardServerOffsetMs = serverTs * 1000 - nowMs;
    steamGuardSlice = Math.floor(serverTs / steamGuardPeriod);
    updateSteamGuardDisplay();
  } catch (e) {
    toast("获取令牌失败", e.message || "");
  }
}
function startSteamGuardTimer() {
  if (steamGuardTimer) return;
  steamGuardTimer = setInterval(() => {
    const ringEl = el("steam-progress-circle");
    if (!ringEl) return;
    const now = Date.now();
    const serverNow = now + steamGuardServerOffsetMs;
    const periodMs = steamGuardPeriod * 1000;
    if (periodMs <= 0) return;
    const phaseMs = ((serverNow % periodMs) + periodMs) % periodMs;
    const radius = parseFloat(ringEl.getAttribute("r") || "0");
    if (!radius) return;
    const circumference = 2 * Math.PI * radius;
    const currentSlice = Math.floor(serverNow / 1000 / steamGuardPeriod);
    if (steamGuardSlice == null) steamGuardSlice = currentSlice;
    if (currentSlice !== steamGuardSlice) {
      steamGuardSlice = currentSlice;
      refreshSteamGuardCode();
    }
    if (!steamGuardVisible) return;
    ringEl.style.strokeDasharray = circumference + " " + circumference;
    ringEl.style.strokeDashoffset = String(circumference * (phaseMs / periodMs));
  }, 50);
}
function stopSteamGuardTimer() {
  if (steamGuardTimer) {
    clearInterval(steamGuardTimer);
    steamGuardTimer = null;
  }
}
function copySteamGuardCode() {
  const code = steamGuardCode || "";
  if (!code) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard
      .writeText(code)
      .then(() => {
        toast("已复制到剪贴板");
      })
      .catch(() => {
        const ta = document.createElement("textarea");
        ta.value = code;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
        toast("已复制到剪贴板");
      });
  } else {
    const ta = document.createElement("textarea");
    ta.value = code;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    toast("已复制到剪贴板");
  }
}
function handleSteamGuardClick() {
  if (!steamGuardVisible) {
    if (steamGuardHideTimer) {
      clearTimeout(steamGuardHideTimer);
      steamGuardHideTimer = null;
    }
    steamGuardVisible = true;
    if (!steamGuardCode) {
      refreshSteamGuardCode();
    } else {
      updateSteamGuardDisplay();
    }
    startSteamGuardTimer();
    steamGuardHideTimer = setTimeout(() => {
      steamGuardVisible = false;
      steamGuardHideTimer = null;
      updateSteamGuardDisplay();
    }, 5000);
  } else {
    copySteamGuardCode();
  }
}
function initSteamGuardPanel() {
  updateSteamGuardDisplay();
  if (!steamGuardCode) {
    refreshSteamGuardCode();
  }
  if (steamGuardVisible) {
    startSteamGuardTimer();
  }
}