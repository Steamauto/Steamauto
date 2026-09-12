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

def _emit(data, as_table=False):
    """按 JSON 或表格输出。表格仅对 list[dict] 有效，其余形态回落到 JSON。"""
    if not as_table:
        _out(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        _render_table(data)
        return 0
    # 非表格友好形态：回落到 JSON（保证信息不丢）
    _out(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _render_table(rows):
    """把 list[dict] 渲染成对齐表格；列 = 所有行 key 的并集（按首次出现顺序）。"""
    cols = []
    for row in rows:
        for k in row:
            if k not in cols:
                cols.append(k)

    def cell(v):
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
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

def _buff_ops():
    def balance(client, args):
        return client.get_user_brief_assest()

    def nickname(client, args):
        return client.get_user_nickname()

    def search(client, args):
        if not args:
            raise ValueError("search 需要关键词，如：--buff search \"AK-47\"")
        key = args[0]
        game = args[1] if len(args) > 1 else "csgo"
        return client.search_goods(key, game)

    def on_sale(client, args):
        page = int(args[0]) if args else 1
        return client.get_on_sale(page_num=page)

    def sell_history(client, args):
        appid = int(args[0]) if args else 730
        return client.get_sell_order_history(appid)

    def waiting_offer(client, args):
        return client.get_buy_orders_waiting_to_send_offer()

    return {
        "balance": (balance, "余额与资产概览"),
        "nickname": (nickname, "当前 BUFF 昵称"),
        "search": (search, "搜索饰品：--buff search <关键词> [game]"),
        "on-sale": (on_sale, "我的在售：--buff on-sale [页码]"),
        "sell-history": (sell_history, "成交历史：--buff sell-history [appid]"),
        "waiting-offer": (waiting_offer, "求购待发报价"),
    }


def _uu_ops():
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

    return {
        "nickname": (nickname, "当前 UU 昵称"),
        "inventory": (inventory, "库存：--uu inventory"),
        "on-sale": (on_sale, "我的在售：--uu on-sale"),
        "leased-out": (leased_out, "已租出：--uu leased-out"),
        "wait-deliver": (wait_deliver, "待发货：--uu wait-deliver"),
        "buy-order": (buy_order, "求购单：--uu buy-order [页码]"),
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
    _out("默认输出 JSON；加 --table 转表格；--help 看本帮助。")
    return 0


def main(platform, argv):
    """平台 API 命令入口。argv 是 ``--buff`` 之后的原始参数列表。"""
    platform = accounts.resolve(platform) or platform
    if platform not in _COMMANDS:
        _err("未知平台：%s（可用：buff / uu / c5 / eco）" % platform)
        return 2

    as_table = False
    positional = []
    for a in argv:
        if a in ("--table", "-t"):
            as_table = True
        elif a in ("--json", "-j"):
            as_table = False
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

    fn, _desc = ops[op]
    try:
        cfg = accounts.load_config()
        client = _CLIENT_FACTORIES[platform](cfg)
        data = fn(client, args)
    except Exception as e:  # noqa: BLE001 - 网络/凭据错误统一转为可读报错
        _err("错误：%s" % (e,))
        return 1

    return _emit(data, as_table=as_table)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
