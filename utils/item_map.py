# -*- coding: utf-8 -*-
"""UU↔BUFF 饰品映射表缓存（assetid 主键）。

缓存文件 ``config/item_map.json``，结构::

    {
        "<assetid>": {
            "uu_name": "反恐精英20周年印花胶囊",
            "uu_template_id": 45796,
            "buff_name": "反恐精英20周年印花胶囊",
            "buff_goods_id": 773534
        },
        ...
    }

读策略：优先读缓存；缓存文件缺失/损坏（返回 None）或显式 ``refresh=True`` 时，
在线拉取（BUFF 库存 + UU 库存按 assetid 匹配）重建并写缓存。

关键事实（实测验证）：assetid = UU 的 ``SteamAssetId`` = BUFF 的 ``assetid``
（Steam 饰品实例 ID，两平台 100% 一致，实测 108/108 完全对应），是可靠的唯一主键。
名称（uu_name/buff_name）只是辅助展示字段，存在少量差异（磨损后缀、中英文、标点）。
"""

import json
import os

from utils import static


def _cache_path():
    """缓存文件路径（每次计算，支持多实例 set_base_dir 热切换）。"""
    return os.path.join(static.CONFIG_FOLDER, "item_map.json")


def load_item_map():
    """读缓存。文件缺失/损坏返回 None（触发在线拉取）。"""
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return None


def save_item_map(item_map):
    """写缓存。失败静默（缓存是优化，不阻塞主流程）。"""
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(item_map, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def build_item_map(buff_client, uu_client):
    """在线拉取：BUFF 库存 + UU 库存按 assetid 匹配，建立映射。

    单个平台拉取失败不影响另一个（各自独立 try），保证「缓存出错再在线拉」时
    至少能拉到可用的那一半。
    """
    item_map = {}

    try:
        for it in buff_client.get_inventory_all() or []:
            aid = str(it.get("assetid") or "")
            if not aid:
                continue
            entry = item_map.setdefault(aid, {})
            entry["buff_name"] = it.get("name") or it.get("market_hash_name") or ""
            entry["buff_goods_id"] = it.get("goods_id")
    except Exception:  # noqa: BLE001 - 单平台失败不阻塞
        pass

    try:
        for it in uu_client.get_inventory() or []:
            aid = str(it.get("SteamAssetId") or "")
            if not aid:
                continue
            ti = it.get("TemplateInfo") or {}
            entry = item_map.setdefault(aid, {})
            entry["uu_name"] = it.get("ShotName") or ti.get("CommodityName") or ""
            entry["uu_template_id"] = ti.get("Id")
    except Exception:  # noqa: BLE001
        pass

    return item_map


def get_item_map(buff_client=None, uu_client=None, refresh=False):
    """优先读缓存；缓存未命中/出错（None）再在线拉并写缓存。

    在线拉需要 buff_client 和 uu_client 都提供，否则只返回缓存（可能为空）。
    """
    if not refresh:
        cached = load_item_map()
        if cached is not None:
            return cached
    if buff_client is None or uu_client is None:
        return {}
    item_map = build_item_map(buff_client, uu_client)
    save_item_map(item_map)
    return item_map


def to_rows(item_map):
    """dict → list[dict]（每行含 assetid 键，供表格/JSON 输出）。"""
    return [{"assetid": aid, **v} for aid, v in item_map.items()]
