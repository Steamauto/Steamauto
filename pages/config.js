// 动态显示/隐藏代理设置
document.getElementById('use_proxies').addEventListener('change', (event) => {
    if (event.target.value === 'true') {
        document.getElementById('proxies_settings').style.display = 'block';
    } else {
        document.getElementById('proxies_settings').style.display = 'none';
    }
});

// 生成配置文件
document.getElementById('generateConfig').addEventListener('click', () => {
    const config = {};

    // 通用设置
    config.steam_login_ignore_ssl_error = JSON.parse(document.getElementById('steam_login_ignore_ssl_error').value);
    config.steam_local_accelerate = JSON.parse(document.getElementById('steam_local_accelerate').value);
    config.use_proxies = JSON.parse(document.getElementById('use_proxies').value);
    if (config.use_proxies) {
        config.proxies = {
            http: document.getElementById('proxies_http').value,
            https: document.getElementById('proxies_https').value
        };
    }
    config.no_pause = JSON.parse(document.getElementById('no_pause').value);

    // BUFF 自动发货插件配置
    config.buff_auto_accept_offer = {
        enable: JSON.parse(document.getElementById('buff_auto_accept_offer_enable').value),
        interval: parseInt(document.getElementById('buff_auto_accept_offer_interval').value),
        sell_protection: JSON.parse(document.getElementById('buff_auto_accept_offer_sell_protection').value),
        protection_price: parseFloat(document.getElementById('buff_auto_accept_offer_protection_price').value),
        protection_price_percentage: parseFloat(document.getElementById('buff_auto_accept_offer_protection_price_percentage').value),
        sell_notification: {
            title: document.getElementById('sell_notification_title').value,
            body: document.getElementById('sell_notification_body').value
        },
        servers: document.getElementById('buff_auto_accept_offer_servers').value.split('\n').filter(line => line.trim() !== '')
    };

    // BUFF 自动备注购买价格插件配置
    config.buff_auto_comment = {
        enable: JSON.parse(document.getElementById('buff_auto_comment_enable').value),
        page_size: parseInt(document.getElementById('buff_auto_comment_page_size').value)
    };

    // BUFF 自动计算利润插件配置
    config.buff_profit_report = {
        enable: JSON.parse(document.getElementById('buff_profit_report_enable').value),
        servers: document.getElementById('buff_profit_report_servers').value.split('\n').filter(line => line.trim() !== ''),
        send_report_time: document.getElementById('buff_profit_report_send_report_time').value
    };

    // BUFF 自动上架插件配置
    config.buff_auto_on_sale = {
        enable: JSON.parse(document.getElementById('buff_auto_on_sale_enable').value),
        force_refresh: JSON.parse(document.getElementById('buff_auto_on_sale_force_refresh').value),
        use_range_price: JSON.parse(document.getElementById('buff_auto_on_sale_use_range_price').value),
        blacklist_time: document.getElementById('buff_auto_on_sale_blacklist_time').value.split(',').map(item => parseInt(item.trim())).filter(item => !isNaN(item)),
        whitelist_time: document.getElementById('buff_auto_on_sale_whitelist_time').value.split(',').map(item => parseInt(item.trim())).filter(item => !isNaN(item)),
        random_chance: parseInt(document.getElementById('buff_auto_on_sale_random_chance').value),
        description: document.getElementById('buff_auto_on_sale_description').value,
        interval: parseInt(document.getElementById('buff_auto_on_sale_interval').value),
        buy_order: {
            enable: JSON.parse(document.getElementById('buff_auto_on_sale_buy_order_enable').value),
            only_auto_accept: JSON.parse(document.getElementById('buff_auto_on_sale_buy_order_only_auto_accept').value),
            supported_payment_method: document.getElementById('buff_auto_on_sale_buy_order_supported_payment_method').value.split(',').map(item => item.trim()).filter(item => item !== ''),
            min_price: parseFloat(document.getElementById('buff_auto_on_sale_buy_order_min_price').value)
        },
        on_sale_notification: {
            title: document.getElementById('on_sale_notification_title').value,
            body: document.getElementById('on_sale_notification_body').value
        }
    };

    // Steam 自动接受礼物报价插件配置
    config.steam_auto_accept_offer = {
        enable: JSON.parse(document.getElementById('steam_auto_accept_offer_enable').value),
        interval: parseInt(document.getElementById('steam_auto_accept_offer_interval').value)
    };

    // 悠悠有品自动发货插件配置
    config.uu_auto_accept_offer = {
        enable: JSON.parse(document.getElementById('uu_auto_accept_offer_enable').value),
        interval: parseInt(document.getElementById('uu_auto_accept_offer_interval').value)
    };

    // 悠悠有品租赁自动上架配置
    config.uu_auto_lease_item = {
        enable: JSON.parse(document.getElementById('uu_auto_lease_item_enable').value),
        lease_max_days: parseInt(document.getElementById('uu_auto_lease_item_lease_max_days').value),
        filter_price: parseFloat(document.getElementById('uu_auto_lease_item_filter_price').value),
        run_time: document.getElementById('uu_auto_lease_item_run_time').value,
        interval: parseInt(document.getElementById('uu_auto_lease_item_interval').value),
        filter_name: document.getElementById('uu_auto_lease_item_filter_name').value.split('\n').map(item => item.trim()).filter(item => item !== '')
    };

    // 悠悠有品出售自动上架配置
    config.uu_auto_sell_item = {
        enable: JSON.parse(document.getElementById('uu_auto_sell_item_enable').value),
        take_profile: JSON.parse(document.getElementById('uu_auto_sell_item_take_profile').value),
        take_profile_ratio: parseFloat(document.getElementById('uu_auto_sell_item_take_profile_ratio').value),
        run_time: document.getElementById('uu_auto_sell_item_run_time').value,
        interval: parseInt(document.getElementById('uu_auto_sell_item_interval').value),
        name: document.getElementById('uu_auto_sell_item_name').value.split('\n').map(item => item.trim()).filter(item => item !== '')
    };

    // ecosteam 插件配置
    config.ecosteam = {
        enable: JSON.parse(document.getElementById('ecosteam_enable').value),
        partnerId: document.getElementById('ecosteam_partnerId').value,
        auto_accept_offer: {
            interval: parseInt(document.getElementById('ecosteam_auto_accept_offer_interval').value)
        },
        auto_sync_sell_shelf: {
            enable: JSON.parse(document.getElementById('ecosteam_auto_sync_sell_shelf_enable').value),
            main_platform: document.getElementById('ecosteam_auto_sync_sell_shelf_main_platform').value,
            enabled_platforms: document.getElementById('ecosteam_auto_sync_sell_shelf_enabled_platforms').value.split(',').map(item => item.trim()).filter(item => item !== ''),
            ratio: JSON.parse(document.getElementById('ecosteam_auto_sync_sell_shelf_ratio').value)
        },
        sync_interval: parseInt(document.getElementById('ecosteam_sync_interval').value),
        qps: parseInt(document.getElementById('ecosteam_qps').value)
    };

    // 日志设置
    config.log_level = document.getElementById('log_level').value;
    config.log_retention_days = parseInt(document.getElementById('log_retention_days').value);

    // 生成 JSON5 字符串
    const configString = JSON5.stringify(config, null, 2);

    // 显示在页面上
    document.getElementById('configOutput').textContent = configString;
});


// 复制到剪贴板
document.getElementById('copyConfig').addEventListener('click', () => {
    const configText = document.getElementById('configOutput').textContent;
    navigator.clipboard.writeText(configText).then(() => {
        alert('配置内容已复制到剪贴板！');
    });
});

// 下载 config.json5 文件
document.getElementById('downloadConfig').addEventListener('click', () => {
    const configText = document.getElementById('configOutput').textContent;
    const blob = new Blob([configText], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'config.json5';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
});
