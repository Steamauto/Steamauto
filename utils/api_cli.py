"""平台 API 命令行（阶段 1：只读命令）。

把 ``api/`` 下四个交易平台的 SDK 方法封装成命令行，独立进程**直连 API**
（复用 ``--login`` 缓存的凭据），无需后台服务常驻。

用法::

    python Steamauto.py --buff <op> [args] [--table|--json]
    python Steamauto.py --uu   <op> [args] [--table|--json]
    python Steamauto.py --c5   <op> [args] [--table|--json]
    python Steamauto.py --eco  <op> [args] [--table|--json]

输出默认 JSON（脚本 / Agent 友好），加 ``--table`` 转表格给人看。
本模块刻意**不导入** utils.logger（不生成日志文件），错误直接打到 stderr。
"""

import json
import os
import sys
import unicodedata

from utils import accounts, static


# ------------------------------------------------------------------ 显示宽度（表格对齐，中文占 2 列）

def _display_width(text):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(text))


def _pad(text, cols):
    text = str(text)
    return text + " " * max(0, cols - _display_width(text))


# ------------------------------------------------------------------ 输出

def _normalize(data):
    """把 SDK 返回统一成 JSON 可序列化的 dict/list/标量。

    各平台 SDK 返回类型不一致：BUFF 的 ``get_on_sale`` 返回 ``requests.Response``
    （未 .json()），而 ``get_user_brief_assest``/``search_goods`` 已返回 dict/list。
    这里对带 ``.json()`` 的对象（Response）统一转成 dict，其余原样返回。
    """
    if hasattr(data, "json"):
        return data.json()
    return data


def _extract_items(data):
    """尝试从 dict 返回中提取 items 列表（用于表格化）；无法提取则原样返回。

    很多平台接口返回 ``{"code": "OK", "data": {"items": [...]}}`` 结构，
    表格化时应直接显示 items，而不是把整个包装 dict 当一行。
    """
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("items"), list):
            return inner["items"]
    return data


def _emit(data, as_table=True):
    """按表格（默认，人类可读）或 JSON 输出。表格仅对 list[dict] 有效，其余形态回落到 JSON。"""
    if not as_table:
        _out(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    data = _extract_items(data)
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        _render_table(data)
        return 0
    # 非表格友好形态：回落到 JSON（保证信息不丢）
    _out(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _render_table(rows):
    """把 list[dict] 渲染成对齐表格；只显示标量字段（过滤嵌套 dict/list 列，避免超宽不可读）。"""
    cols = []
    for row in rows:
        for k, v in row.items():
            if k not in cols and not isinstance(v, (dict, list)):
                cols.append(k)

    def cell(v):
        return "" if v is None else str(v)

    table = [[cell(r.get(c)) for c in cols] for r in rows]
    widths = [max(_display_width(c), *(_display_width(row[i]) for row in table)) for i, c in enumerate(cols)]

    _out("  ".join(_pad(c, w) for c, w in zip(cols, widths)))
    for row in table:
        _out("  ".join(_pad(cell, w) for cell, w in zip(row, widths)))


def _out(msg=""):
    print(msg)


def _err(msg):
    print(msg, file=sys.stderr)


# ------------------------------------------------------------------ 凭据与客户端

def _read(path):
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _proxies(cfg, platform):
    """按插件配置决定是否用全局代理；返回 dict 或 None。"""
    key = accounts.ENABLE_KEY.get(platform, "")
    section = cfg.get(key)
    if not isinstance(section, dict) or not section.get("use_proxies"):
        return None
    proxies = cfg.get("proxies")
    return proxies if isinstance(proxies, dict) else None


def _buff_client(cfg):
    session = _read(accounts.credential_path("buff"))
    if not session:
        raise RuntimeError("尚无 BUFF 登录凭据，请先 `python Steamauto.py --login buff`")
    from api.BuffApi import BuffAccount

    return BuffAccount(session, proxies=_proxies(cfg, "buff"))


def _uu_client(cfg):
    token = _read(accounts.credential_path("uu"))
    if not token:
        raise RuntimeError("尚无 UU 登录凭据，请先 `python Steamauto.py --login uu`")
    import api.uuyoupinapi as uuyoupinapi

    return uuyoupinapi.UUAccount(token, proxy=_proxies(cfg, "uu"))


def _c5_client(cfg):
    app_key = str((cfg.get("c5_auto_accept_offer") or {}).get("app_key", "") or "").strip()
    if not app_key:
        raise RuntimeError("未配置 C5 AppKey（config set c5_auto_accept_offer.app_key <key>）")
    from api.PyC5Game import C5Account

    return C5Account(app_key)


def _eco_client(cfg):
    section = cfg.get("ecosteam") or {}
    partner_id = str(section.get("partnerId", "") or "").strip()
    if not partner_id:
        raise RuntimeError("未配置 ECO partnerId（config set ecosteam.partnerId <id>）")
    rsa_key = _read(static.ECOSTEAM_RSAKEY_FILE)
    if not rsa_key:
        raise RuntimeError("缺少 ECO 私钥文件 %s" % static.ECOSTEAM_RSAKEY_FILE)
    from api.PyECOsteam import ECOsteamClient

    return ECOsteamClient(partner_id, rsa_key, qps=section.get("qps", 10))


_CLIENT_FACTORIES = {
    "buff": _buff_client,
    "uu": _uu_client,
    "c5": _c5_client,
    "eco": _eco_client,
}


# ------------------------------------------------------------------ 各平台操作

#: 表格显示的关键字段（只显示这些，避免表格过宽）
_BUFF_INVENTORY_FIELDS = ["assetid", "market_hash_name", "name", "goods_id", "sell_order_price", "sell_min_price", "buy_max_price", "state_text", "steam_price"]
_BUFF_ON_SALE_FIELDS = ["id", "goods_id", "price", "state_text", "description"]


def _project(rows, fields):
    """提取 rows 的关键字段（仅保留 fields 里存在的标量字段）；fields 为空则原样返回。"""
    if not fields or not isinstance(rows, list):
        return rows
    return [
        {k: r.get(k) for k in fields if k in r and not isinstance(r.get(k), (dict, list))}
        for r in rows if isinstance(r, dict)
    ]


def _enrich_search(client, results):
    """搜索结果补行情：在售最低价、求购最高价、在售数量。"""
    out = []
    for r in results:
        if not isinstance(r, dict):
            out.append(r)
            continue
        gid = r.get("goods_ids")
        if not gid:
            out.append(r)
            continue
        enriched = dict(r)
        try:
            sell = client.get_sell_order(gid)
            if isinstance(sell, dict):
                items = sell.get("items") or []
                enriched["sell_min"] = items[0].get("price") if items else None
                enriched["sell_num"] = sell.get("total_count")
        except Exception:
            pass
        try:
            enriched["buy_max"] = client.get_buy_order_max(gid)
        except Exception:
            pass
        out.append(enriched)
    return out


def _buff_ops():
    def balance(client, args):
        d = client.get_user_brief_assest() or {}
        return {
            "available": d.get("total_able_withdraw_amount"),
            "trading_only": d.get("total_unable_withdraw_amount"),
            "frozen": d.get("frozen_amount"),
            "total": d.get("cash_amount"),
        }

    def nickname(client, args):
        return client.get_user_nickname()

    def search(client, args):
        if not args:
            raise ValueError("search 需要关键词，如：--buff search \"AK-47\"")
        key = args[0]
        game = args[1] if len(args) > 1 else "csgo"
        return _enrich_search(client, client.search_goods(key, game))

    def on_sale(client, args):
        page = int(args[0]) if args else 1
        d = client.get_on_sale(page_num=page)
        if hasattr(d, "json"):
            d = d.json()
        items = (d.get("data") or {}).get("items", []) if isinstance(d, dict) else d
        return _project(items, _BUFF_ON_SALE_FIELDS)

    def sell_history(client, args):
        appid = int(args[0]) if args else 730
        return client.get_sell_order_history(appid)

    def waiting_offer(client, args):
        return client.get_buy_orders_waiting_to_send_offer()

    # ---- 写操作（上架/塞求购/下架/改价/购买；默认需二次确认）----

    def _find_item(client, assetid):
        for it in client.get_inventory_all():
            if str(it.get("assetid")) == str(assetid):
                return it
        return None

    def _make_asset(client, assetid, price):
        it = _find_item(client, assetid)
        if it is None:
            raise ValueError("库存中未找到 assetid=%s，请先确认该饰品在库存中" % assetid)
        from api.BuffApi import models

        return models.BuffOnSaleAsset(
            assetid=str(assetid),
            classid=int(it["classid"]),
            instanceid=int(it["instanceid"]),
            market_hash_name=it.get("market_hash_name") or "",
            price=price,
        )

    def list_item(client, args):
        if len(args) < 2:
            raise ValueError("list 需要 assetid 和 price，如：--buff list <assetid> <price>")
        assetid, price = args[0], float(args[1])
        return client.on_sale([_make_asset(client, assetid, price)])

    def sell_bidder(client, args):
        if len(args) < 2:
            raise ValueError("sell-bidder 需要 assetid 和 goods_id，如：--buff sell-bidder <assetid> <goods_id>")
        assetid, goods_id = args[0], args[1]
        buy_max = client.get_buy_order_max(goods_id)
        if buy_max is None:
            raise ValueError("该饰品（goods_id=%s）暂无求购单，无法塞求购" % goods_id)
        price = round(float(buy_max) - 0.01, 2)
        return client.on_sale([_make_asset(client, assetid, price)])

    def off_shelf(client, args):
        if not args:
            raise ValueError("off-shelf 需要至少一个 sell_order_id，如：--buff off-shelf <sell_order_id>...")
        return client.cancel_sale(list(args))

    def change_price(client, args):
        if len(args) < 2:
            raise ValueError("change-price 需要 sell_order_id 和 price，如：--buff change-price <sell_order_id> <price>")
        sell_order_id, price = args[0], args[1]
        return client.change_price([{"sell_order_id": sell_order_id, "price": float(price)}])

    def buy(client, args):
        if len(args) < 3:
            raise ValueError("buy 需要 goods_id、sell_order_id、price，如：--buff buy <goods_id> <sell_order_id> <price> [pay_method]")
        goods_id, sell_order_id, price = args[0], args[1], args[2]
        pay_method = args[3] if len(args) > 3 else "buff-bankcard"
        return client.buy_goods(
            sell_order_id=sell_order_id,
            goods_id=goods_id,
            price=price,
            pay_method=pay_method,
            ask_seller_send_offer=False,
        )

    def search_market(client, args):
        if not args:
            raise ValueError("search-market 需要关键词，如：--buff search-market \"AK-47\"")
        key = args[0]
        page = int(args[1]) if len(args) > 1 else 1
        return client.search_market(key, page_num=page)

    def inventory(client, args):
        return _project(client.get_inventory_all(), _BUFF_INVENTORY_FIELDS)

    def buy_order(client, args):
        if not args:
            raise ValueError("buy-order 需要 goods_id，如：--buff buy-order 33960")
        return client.get_buy_order(args[0])

    def highest_buy(client, args):
        if not args:
            raise ValueError("highest-buy 需要 goods_id（可从 --buff search-market 结果里拿），如：--buff highest-buy 33960")
        return client.get_buy_order_max(args[0])

    def lowest_sell(client, args):
        if not args:
            raise ValueError("lowest-sell 需要 goods_id（可从 --buff search-market 结果里拿），如：--buff lowest-sell 33960")
        return client.get_sell_min(args[0])

    return {
        "balance": (balance, "余额（可用/仅交易/冻结/总）"),
        "nickname": (nickname, "当前 BUFF 昵称"),
        "search": (search, "搜索建议：--buff search <关键词> [game]（仅 10 条）"),
        "search-market": (search_market, "搜索市场（完整结果）：--buff search-market <关键词> [页码]"),
        "inventory": (inventory, "库存：--buff inventory"),
        "on-sale": (on_sale, "我的在售：--buff on-sale [页码]"),
        "sell-history": (sell_history, "成交历史：--buff sell-history [appid]"),
        "buy-order": (buy_order, "求购单列表：--buff buy-order <goods_id>"),
        "highest-buy": (highest_buy, "求购最高价（市场最高求购单）：--buff highest-buy <goods_id>"),
        "lowest-sell": (lowest_sell, "在售最低价（市场最低卖单）：--buff lowest-sell <goods_id>"),
        "waiting-offer": (waiting_offer, "求购待发报价"),
        "list": (list_item, "上架：--buff list <assetid> <price>【写】"),
        "sell-bidder": (sell_bidder, "塞求购：--buff sell-bidder <assetid> <goods_id>【写】"),
        "off-shelf": (off_shelf, "下架：--buff off-shelf <sell_order_id>...【写】"),
        "change-price": (change_price, "改价：--buff change-price <sell_order_id> <price>【写】"),
        "buy": (buy, "购买：--buff buy <goods_id> <sell_order_id> <price> [pay_method]【写】"),
    }


def _uu_ops():
    def balance(client, args):
        return client.get_balance()

    def nickname(client, args):
        return client.get_user_nickname()

    def inventory(client, args):
        return client.get_inventory()

    def on_sale(client, args):
        return client.get_sell_list()

    def leased_out(client, args):
        return client.get_leased_out_list()

    def wait_deliver(client, args):
        return client.get_wait_deliver_list()

    def buy_order(client, args):
        page = int(args[0]) if args else 1
        return client.get_buy_order(pageIndex=page)

    def search(client, args):
        if not args:
            raise ValueError("search 需要关键词，如：--uu search \"印花胶囊\"")
        return client.search_market(args[0])

    def highest_buy(client, args):
        if not args:
            raise ValueError("highest-buy 需要 template_id（可从 --uu search 结果里拿），如：--uu highest-buy 45796")
        return client.get_buy_max(int(args[0]))

    def lowest_sell(client, args):
        if not args:
            raise ValueError("lowest-sell 需要 template_id（可从 --uu search 结果里拿），如：--uu lowest-sell 45796")
        return client.get_sell_min(int(args[0]))

    # ---- 写操作（上架/塞求购/下架/改价；默认需二次确认）----

    def sell(client, args):
        if len(args) < 2:
            raise ValueError("sell 需要 assetid 和 price，如：--uu sell <assetid> <price>")
        assetid, price = args[0], float(args[1])
        return client.sell_items({str(assetid): price})

    def off_shelf(client, args):
        if not args:
            raise ValueError("off-shelf 需要至少一个 commodity_id，如：--uu off-shelf <commodity_id>...")
        return client.off_shelf([str(x) for x in args])

    def change_price(client, args):
        if len(args) < 2:
            raise ValueError("change-price 需要 commodity_id 和 price，如：--uu change-price <commodity_id> <price>")
        commodity_id, price = args[0], float(args[1])
        return client.change_price({str(commodity_id): price})

    def buy(client, args):
        if len(args) < 2:
            raise ValueError("buy 需要 template_id 和 price，如：--uu buy <template_id> <price> [num]")
        template_id = int(args[0])
        price = float(args[1])
        num = int(args[2]) if len(args) > 2 else 1
        # 从库存查 template_id 对应的 hash_name + name（发求购单必需）
        hash_name = ""
        name = ""
        for it in client.get_inventory():
            ti = it.get("TemplateInfo") or {}
            if str(ti.get("Id")) == str(template_id):
                hash_name = it.get("MarketHashName") or ti.get("CommodityHashName") or ""
                name = ti.get("CommodityName") or ""
                break
        if not hash_name or not name:
            raise ValueError("库存中未找到 template_id=%s 的饰品，无法确定 hash_name/name，请先确认库存里有该饰品" % template_id)
        return client.publish_purchase_order(
            templateId=template_id,
            templateHashName=hash_name,
            commodityName=name,
            purchasePrice=price,
            purchaseNum=num,
        )

    return {
        "balance": (balance, "余额（可用/仅交易/冻结/总）"),
        "nickname": (nickname, "当前 UU 昵称"),
        "inventory": (inventory, "库存：--uu inventory"),
        "on-sale": (on_sale, "我的在售：--uu on-sale"),
        "leased-out": (leased_out, "已租出：--uu leased-out"),
        "wait-deliver": (wait_deliver, "待发货：--uu wait-deliver"),
        "buy-order": (buy_order, "求购单：--uu buy-order [页码]"),
        "search": (search, "搜索市场：--uu search <关键词>"),
        "highest-buy": (highest_buy, "求购最高价（市场最高求购单）：--uu highest-buy <template_id>"),
        "lowest-sell": (lowest_sell, "在售最低价（市场最低卖单）：--uu lowest-sell <template_id>"),
        "sell": (sell, "上架：--uu sell <assetid> <price>【写】"),
        "off-shelf": (off_shelf, "下架：--uu off-shelf <commodity_id>...【写】"),
        "buy": (buy, "发求购单（塞求购）：--uu buy <template_id> <price> [num]【写】"),
        "change-price": (change_price, "改价：--uu change-price <commodity_id> <price>【写】"),
    }


def _c5_ops():
    def balance(client, args):
        return client.balance()

    def orders(client, args):
        status = int(args[0]) if args else 0
        page = int(args[1]) if len(args) > 1 else 1
        return client.orderList(status=status, page=page)

    def check_key(client, args):
        return client.checkAppKey()

    return {
        "balance": (balance, "余额：--c5 balance"),
        "orders": (orders, "订单：--c5 orders [status] [page]（0 全部/10 完成/11 取消）"),
        "check-key": (check_key, "校验 AppKey：--c5 check-key"),
    }


def _eco_ops():
    def balance(client, args):
        return client.GetTotalMoney().json()

    def on_sale(client, args):
        return client.GetSellGoodsList(PageIndex=1, PageSize=100).json()

    def inventory(client, args):
        return client.QueryStock(1, 100).json()

    return {
        "balance": (balance, "余额：--eco balance"),
        "on-sale": (on_sale, "在售：--eco on-sale"),
        "inventory": (inventory, "库存：--eco inventory"),
    }


_COMMANDS = {
    "buff": _buff_ops,
    "uu": _uu_ops,
    "c5": _c5_ops,
    "eco": _eco_ops,
}

#: 写操作命令（默认需二次确认；加 --yes 跳过，--dry-run 只预览不执行）
_WRITE_OPS = {
    "buff": {"list", "sell-bidder", "off-shelf", "change-price", "buy"},
    "uu": {"sell", "off-shelf", "buy", "change-price"},
}


# ------------------------------------------------------------------ 帮助与入口

def _help(platform):
    ops = _COMMANDS[platform]()
    _out("Steamauto %s API 操作" % accounts.DISPLAY.get(platform, platform))
    _out("")
    _out("  python Steamauto.py --%s <op> [args] [--table|--json]" % platform)
    _out("")
    for op, (_fn, desc) in ops.items():
        _out("  %-14s %s" % (op, desc))
    _out("")
    _out("默认表格输出（人类可读）；加 --json 输出 JSON；--help 看本帮助。")
    return 0


def main(platform, argv):
    """平台 API 命令入口。argv 是 ``--buff`` 之后的原始参数列表。"""
    platform = accounts.resolve(platform) or platform
    if platform not in _COMMANDS:
        _err("未知平台：%s（可用：buff / uu / c5 / eco）" % platform)
        return 2

    as_table = True  # 默认表格（人类可读）；--json 输出 JSON
    as_yes = False
    dry_run = False
    positional = []
    for a in argv:
        if a in ("--table", "-t"):
            as_table = True
        elif a in ("--json", "-j"):
            as_table = False
        elif a in ("--yes", "-y"):
            as_yes = True
        elif a == "--dry-run":
            dry_run = True
        elif a in ("--help", "-h"):
            return _help(platform)
        else:
            positional.append(a)

    if not positional:
        return _help(platform)

    op = positional[0]
    args = positional[1:]
    ops = _COMMANDS[platform]()
    if op not in ops:
        _err("未知操作：--%s %s" % (platform, op))
        _help(platform)
        return 2

    # 平台 API 查询/交易需要程序在运行（运行时状态源）
    running, _state = accounts.daemon.is_running()
    if not running:
        _err("程序未运行，无法执行平台 API 操作（查询/交易需程序在运行）。")
        _err("请先启动：python Steamauto.py --start 或 --run")
        return 1

    fn, _desc = ops[op]

    # 写操作：二次确认（指令式安全；未来全自动交易用 --yes 跳过）
    if op in _WRITE_OPS.get(platform, set()):
        summary = "--%s %s %s" % (platform, op, " ".join(args))
        if dry_run:
            _out("（dry-run）将执行：%s" % summary)
            _out("已跳过真实请求。加 --yes 才会真实执行。")
            return 0
        if not as_yes:
            if accounts.stdin_is_interactive():
                _out("即将执行写操作：%s" % summary)
                _out("这是实盘操作，会真实改变挂单/资金状态！")
                resp = input("确认执行？输入 yes 继续，其他任意键取消：")
                if resp.strip().lower() != "yes":
                    _out("已取消")
                    return 0
            else:
                _err("写操作需要确认：%s" % summary)
                _err("非交互式终端请加 --yes 明确确认（或 --dry-run 预览）。")
                return 2

    try:
        cfg = accounts.load_config()
        client = _CLIENT_FACTORIES[platform](cfg)
        data = _normalize(fn(client, args))
    except Exception as e:  # noqa: BLE001 - 网络/凭据错误统一转为可读报错
        _err("错误：%s" % (e,))
        return 1

    return _emit(data, as_table=as_table)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
