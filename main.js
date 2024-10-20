// 动态显示/隐藏代理设置
document.getElementById('use_proxies').addEventListener('change', (event) => {
    if (event.target.value === 'true') {
        document.getElementById('proxies_settings').style.display = 'block';
    } else {
        document.getElementById('proxies_settings').style.display = 'none';
    }
});

// 动态显示/隐藏 BUFF 自动发货插件配置的通知配置
document.getElementById('buff_auto_accept_offer_enable').addEventListener('change', (event) => {
    const isEnabled = event.target.value === 'true';
    // 根据需要显示或隐藏插件配置项
});

// 生成配置文件
document.getElementById('generateConfig').addEventListener('click', () => {
    // 收集表单数据，生成配置对象
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
