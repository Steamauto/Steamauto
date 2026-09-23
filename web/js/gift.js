
; (function () {
    'use strict';
    let allFriends = [];
    let selectedFriend = null;
    let selectedEdition = null;
    let pollTimer = null;
    let walletInfo = null;
    const $ = id => document.getElementById(id);
    const elFetchFriends = $('btn-gift-fetch-friends');
    const elFriendsLoading = $('gift-friends-loading');
    const elFriendsGrid = $('gift-friends-grid');
    const elFriendsEmpty = $('gift-friends-empty');
    const elFriendsCount = $('gift-friends-count');
    const elFriendSearch = $('gift-friend-search');
    const elSelectedFriend = $('gift-selected-friend');
    const elSelAvatar = $('gift-sel-avatar');
    const elSelAvatarPH = $('gift-sel-avatar-placeholder');
    const elSelName = $('gift-sel-name');
    const elSelId = $('gift-sel-id');
    const elClearFriend = $('btn-gift-clear-friend');
    const elStoreUrl = $('gift-store-url');
    const elFetchEditions = $('btn-gift-fetch-editions');
    const elEditionsLoading = $('gift-editions-loading');
    const elGamePreview = $('gift-game-preview');
    const elGameImg = $('gift-game-img');
    const elGameTitle = $('gift-game-title');
    const elGameAppid = $('gift-game-appid');
    const elEditionsList = $('gift-editions-list');
    const elSendInfo = $('gift-send-info');
    const elSendBtn = $('btn-gift-send');
    const elProgressPanel = $('gift-progress-panel');
    const elProgressSpinner = $('gift-progress-spinner');
    const elProgressTitle = $('gift-progress-title-text');
    const elProgressSteps = $('gift-progress-steps');
    const elProgressResult = $('gift-progress-result');
    const elProgressResIcon = $('gift-progress-result-icon');
    const elProgressResMsg = $('gift-progress-result-msg');
    const elProgressClose = $('btn-gift-progress-close');
    function showToast(msg, type = 'info') {
        if (window.showToast) { window.showToast(msg, type); return; }
        const host = $('toast-host');
        if (!host) return;
        const el = document.createElement('div');
        el.className = `toast toast-${type}`;
        el.textContent = msg;
        host.appendChild(el);
        setTimeout(() => el.remove(), 3500);
    }
    function setHidden(el, hidden) {
        if (!el) return;
        el.classList.toggle('hidden', hidden);
    }
    function updateSendButton() {
        const ready = selectedFriend && selectedEdition;
        elSendBtn.disabled = !ready;
        if (ready) {
            const priceStr = selectedEdition.price ? ` (${selectedEdition.price})` : '';
            elSendInfo.textContent = `准备向「${selectedFriend.name}」赠送「${selectedEdition.name}」${priceStr}`;
            elSendInfo.className = 'gift-send-info gift-send-info--ready';
        } else if (selectedFriend && !selectedEdition) {
            elSendInfo.textContent = `已选好友：${selectedFriend.name}，请选择商品版本`;
            elSendInfo.className = 'gift-send-info';
        } else {
            elSendInfo.textContent = '请选择好友和商品';
            elSendInfo.className = 'gift-send-info';
        }
    }
    async function fetchBalance() {
        const elBalance = $('gift-wallet-balance');
        const elBalanceWrap = $('gift-wallet-wrap');
        if (!elBalance || !elBalanceWrap) return;
        elBalance.textContent = '获取中...';
        elBalanceWrap.className = 'gift-wallet-wrap gift-wallet-loading';
        try {
            const res = await fetch('/api/gift/balance');
            const data = await res.json();
            if (data.ok) {
                walletInfo = data;
                elBalance.textContent = data.balance_display;
                elBalanceWrap.className = 'gift-wallet-wrap gift-wallet-ok';
            } else {
                elBalance.textContent = '获取失败';
                elBalanceWrap.className = 'gift-wallet-wrap gift-wallet-error';
            }
        } catch (e) {
            elBalance.textContent = '网络错误';
            elBalanceWrap.className = 'gift-wallet-wrap gift-wallet-error';
        }
    }
    document.querySelectorAll('.nav-item[data-tab="gift"]').forEach(btn => {
        btn.addEventListener('click', () => fetchBalance());
    });
    function renderFriends(list) {
        elFriendsGrid.innerHTML = '';
        if (!list || list.length === 0) {
            setHidden(elFriendsEmpty, false);
            return;
        }
        setHidden(elFriendsEmpty, true);
        list.forEach(friend => {
            const card = document.createElement('div');
            card.className = 'gift-friend-card';
            card.dataset.steamid = friend.steamid;
            if (selectedFriend && selectedFriend.steamid === friend.steamid) {
                card.classList.add('selected');
            }
            const hasAvatar = friend.avatar && friend.avatar.length > 10;
            card.innerHTML = `
        <div class="gift-friend-avatar">
          ${hasAvatar
                    ? `<img src="${friend.avatar}" alt="${friend.name}" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
                    : ''}
          <div class="gift-friend-avatar-fallback" style="${hasAvatar ? 'display:none' : ''}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
          </div>
        </div>
        <div class="gift-friend-info">
          <div class="gift-friend-name" title="${friend.name}">${friend.name}</div>
          <div class="gift-friend-id">${friend.steamid}</div>
        </div>
        <div class="gift-friend-check">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" width="14" height="14"><polyline points="20 6 9 17 4 12"></polyline></svg>
        </div>
      `;
            card.addEventListener('click', () => selectFriend(friend));
            elFriendsGrid.appendChild(card);
        });
    }
    function selectFriend(friend) {
        selectedFriend = friend;
        document.querySelectorAll('.gift-friend-card').forEach(c => {
            c.classList.toggle('selected', c.dataset.steamid === friend.steamid);
        });
        setHidden(elSelectedFriend, false);
        elSelName.textContent = friend.name;
        elSelId.textContent = friend.steamid;
        if (friend.avatar && friend.avatar.length > 10) {
            elSelAvatar.src = friend.avatar;
            setHidden(elSelAvatar, false);
            setHidden(elSelAvatarPH, true);
        } else {
            setHidden(elSelAvatar, true);
            setHidden(elSelAvatarPH, false);
        }
        updateSendButton();
    }
    elClearFriend && elClearFriend.addEventListener('click', () => {
        selectedFriend = null;
        setHidden(elSelectedFriend, true);
        document.querySelectorAll('.gift-friend-card').forEach(c => c.classList.remove('selected'));
        updateSendButton();
    });
    elFriendSearch && elFriendSearch.addEventListener('input', () => {
        const q = elFriendSearch.value.trim().toLowerCase();
        if (!q) { renderFriends(allFriends); return; }
        renderFriends(allFriends.filter(f =>
            f.name.toLowerCase().includes(q) || f.steamid.includes(q)
        ));
    });
    elFetchFriends && elFetchFriends.addEventListener('click', async () => {
        setHidden(elFriendsLoading, false);
        setHidden(elFriendsEmpty, true);
        elFriendsGrid.innerHTML = '';
        elFetchFriends.disabled = true;
        try {
            const res = await fetch('/api/gift/friends');
            const data = await res.json();
            if (!data.ok) {
                showToast(data.error || '获取好友失败', 'error');
                setHidden(elFriendsEmpty, false);
                return;
            }
            allFriends = data.friends || [];
            elFriendsCount.textContent = allFriends.length;
            if (allFriends.length === 0) {
                setHidden(elFriendsEmpty, false);
                showToast('好友列表为空', 'warn');
            } else {
                renderFriends(allFriends);
                showToast(`已加载 ${allFriends.length} 位好友`, 'success');
            }
        } catch (e) {
            showToast('网络错误：' + e.message, 'error');
            setHidden(elFriendsEmpty, false);
        } finally {
            setHidden(elFriendsLoading, true);
            elFetchFriends.disabled = false;
        }
    });
    function renderEditions(editions) {
        elEditionsList.innerHTML = '';
        selectedEdition = null;
        updateSendButton();
        if (!editions || editions.length === 0) {
            elEditionsList.innerHTML = '<div class="gift-editions-empty">未找到可购买的版本，请确认链接有效且该游戏支持赠礼</div>';
            return;
        }
        editions.forEach((ed, idx) => {
            const item = document.createElement('div');
            item.className = 'gift-edition-item';
            let priceHtml = '';
            if (ed.price) {
                const hasDiscount = ed.discount_pct && ed.discount_pct !== '0%';
                if (hasDiscount) {
                    priceHtml = `
            <div class="gift-edition-price">
              <span class="gift-price-badge">${ed.discount_pct}</span>
              <span class="gift-price-original">${ed.original_price}</span>
              <span class="gift-price-final">${ed.price}</span>
            </div>`;
                } else {
                    priceHtml = `<div class="gift-edition-price"><span class="gift-price-final">${ed.price}</span></div>`;
                }
            }
            item.innerHTML = `
        <div class="gift-edition-radio">
          <div class="gift-edition-dot"></div>
        </div>
        <div class="gift-edition-details">
          <div class="gift-edition-name">${ed.name}</div>
          <div class="gift-edition-meta">${ed.type === 'bundleid' ? '捆绑包' : '标准包'} · ID: ${ed.id}</div>
        </div>
        ${priceHtml}
        <svg class="gift-edition-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" width="16" height="16"><polyline points="20 6 9 17 4 12"></polyline></svg>
      `;
            item.addEventListener('click', () => {
                document.querySelectorAll('.gift-edition-item').forEach(i => i.classList.remove('selected'));
                item.classList.add('selected');
                selectedEdition = ed;
                updateSendButton();
            });
            elEditionsList.appendChild(item);
            if (idx === 0) item.click();
        });
    }
    elFetchEditions && elFetchEditions.addEventListener('click', async () => {
        const url = elStoreUrl.value.trim();
        if (!url) { showToast('请输入 Steam 商店链接', 'warn'); return; }
        if (!url.includes('store.steampowered.com/app/')) {
            showToast('链接格式不正确，需包含 store.steampowered.com/app/', 'warn');
            return;
        }
        setHidden(elEditionsLoading, false);
        setHidden(elGamePreview, true);
        elEditionsList.innerHTML = '';
        selectedEdition = null;
        elFetchEditions.disabled = true;
        try {
            const res = await fetch('/api/gift/editions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ store_url: url }),
            });
            const data = await res.json();
            if (!data.ok) {
                showToast(data.error || '获取商品失败', 'error');
                return;
            }
            if (data.title) {
                elGameTitle.textContent = data.title;
                elGameAppid.textContent = `App ID: ${data.app_id}`;
                if (data.image) elGameImg.src = data.image;
                setHidden(elGamePreview, false);
            }
            renderEditions(data.editions);
            if ((data.editions || []).length > 0) {
                showToast(`已找到 ${data.editions.length} 个版本`, 'success');
            }
        } catch (e) {
            showToast('网络错误：' + e.message, 'error');
        } finally {
            setHidden(elEditionsLoading, true);
            elFetchEditions.disabled = false;
            updateSendButton();
        }
    });
    const STEP_LABELS = [
        '获取鉴权令牌',
        '清空购物车',
        '加入购物车',
        '提取订单流水号',
        '设定赠礼目标',
        '执行最终结账',
    ];
    function initProgressSteps() {
        elProgressSteps.innerHTML = '';
        STEP_LABELS.forEach((label, i) => {
            const el = document.createElement('div');
            el.className = 'gift-progress-step';
            el.id = `gift-step-${i}`;
            el.innerHTML = `
        <div class="gift-step-icon gift-step-icon--wait">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><circle cx="12" cy="12" r="10"></circle></svg>
        </div>
        <div class="gift-step-label">${label}</div>
        <div class="gift-step-msg" id="gift-step-msg-${i}"></div>
      `;
            elProgressSteps.appendChild(el);
        });
    }
    function updateStep(stepN, msg, status) {
        const el = $(`gift-step-${stepN}`);
        if (!el) return;
        const icon = el.querySelector('.gift-step-icon');
        const msgEl = $(`gift-step-msg-${stepN}`);
        icon.className = `gift-step-icon gift-step-icon--${status}`;
        if (status === 'running') {
            icon.innerHTML = '<div class="gift-step-spinner"></div>';
        } else if (status === 'ok') {
            icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" width="14" height="14"><polyline points="20 6 9 17 4 12"></polyline></svg>';
        } else if (status === 'error') {
            icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" width="14" height="14"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';
        } else {
            icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><circle cx="12" cy="12" r="10"></circle></svg>';
        }
        if (msg && msgEl) msgEl.textContent = msg;
    }
    function startPolling(taskId) {
        let lastStep = -1;
        pollTimer = setInterval(async () => {
            try {
                const res = await fetch(`/api/gift/task/${taskId}`);
                const data = await res.json();
                if (!data.ok) return;
                const task = data.task;
                const progress = task.progress || [];
                progress.forEach((p, idx) => {
                    const stepN = p.step;
                    for (let i = lastStep + 1; i < stepN; i++) {
                        updateStep(i, '', 'ok');
                    }
                    if (p.done) {
                        updateStep(stepN, p.msg, p.ok ? 'ok' : 'error');
                    } else {
                        updateStep(stepN, p.msg, 'running');
                    }
                    lastStep = stepN;
                });
                if (task.status === 'done' || task.status === 'error') {
                    clearInterval(pollTimer);
                    pollTimer = null;
                    const last = progress[progress.length - 1];
                    setHidden(elProgressSpinner, true);
                    elProgressTitle.textContent = last.ok ? '赠礼完成！' : '赠礼失败';
                    setHidden(elProgressResult, false);
                    elProgressResIcon.innerHTML = last.ok
                        ? '<span style="font-size:2rem">✓</span>'
                        : '<span style="font-size:2rem">❌</span>';
                    elProgressResMsg.textContent = last.msg;
                    elProgressResMsg.className = `gift-result-msg ${last.ok ? 'gift-result-msg--ok' : 'gift-result-msg--error'}`;
                    elSendBtn.disabled = false;
                    if (last.ok) showToast('赠礼成功', 'success');
                    else showToast('赠礼失败: ' + last.msg, 'error');
                }
            } catch (e) { }
        }, 1000);
    }
    elSendBtn && elSendBtn.addEventListener('click', async () => {
        if (!selectedFriend || !selectedEdition) return;
        if (walletInfo && selectedEdition.price) {
            const priceStr = selectedEdition.price;
            const numMatch = priceStr.replace(/,/g, '').match(/([\d.]+)/);
            if (numMatch) {
                const priceNum = parseFloat(numMatch[1]);
                const noDiv = [8, 16, 15].includes(walletInfo.currency_id);
                const balanceNum = noDiv ? walletInfo.balance_raw : walletInfo.balance_raw / 100.0;
                if (priceNum > balanceNum) {
                    showToast(
                        `❌ 余额不足！当前钱包 ${walletInfo.balance_display}，商品售价 ${selectedEdition.price}`,
                        'error'
                    );
                    return;
                }
            }
        }
        const priceHint = selectedEdition.price ? `\n当前钱包余额：${walletInfo ? walletInfo.balance_display : '未知'}\n商品售价：${selectedEdition.price}` : '';
        if (!(await appConfirm(`确认向「${selectedFriend.name}」赠送「${selectedEdition.name}」？${priceHint}\n\n此操作将直接从您的 Steam 钱包扣款，不可撤销！`, { title: "确认赠礼", danger: true, confirmText: "确认赠送" }))) return;
        setHidden(elProgressPanel, false);
        setHidden(elProgressSpinner, false);
        setHidden(elProgressResult, true);
        elProgressTitle.textContent = '赠礼进行中...';
        initProgressSteps();
        elSendBtn.disabled = true;
        try {
            const res = await fetch('/api/gift/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    friend_steamid: selectedFriend.steamid,
                    item_id: selectedEdition.id,
                    item_type: selectedEdition.type,
                }),
            });
            const data = await res.json();
            if (!data.ok) {
                showToast(data.error || '任务创建失败', 'error');
                setHidden(elProgressPanel, true);
                elSendBtn.disabled = false;
                return;
            }
            startPolling(data.task_id);
        } catch (e) {
            showToast('网络错误：' + e.message, 'error');
            setHidden(elProgressPanel, true);
            elSendBtn.disabled = false;
        }
    });
    elProgressClose && elProgressClose.addEventListener('click', () => {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        setHidden(elProgressPanel, true);
        elSendBtn.disabled = !(selectedFriend && selectedEdition);
    });
    const elRefreshBalance = $('btn-gift-refresh-balance');
    elRefreshBalance && elRefreshBalance.addEventListener('click', () => fetchBalance());
    setHidden(elFriendsEmpty, false);
    updateSendButton();
    fetchBalance();
})();
