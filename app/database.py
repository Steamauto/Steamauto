import json
import math
import os
import threading
from pathlib import Path
from typing import List, Optional
from sqlmodel import Field, Session, SQLModel, create_engine, select
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_DB_PATH = _CONFIG_DIR / "app.db"
_TRANSACTIONS_JSON = _CONFIG_DIR / "transactions.json"
_TRANSACTIONS_BAK = _CONFIG_DIR / "transactions.json.bak"
_WILSON_Z = 1.96
def _compute_wilson_score(positive_rate, total_reviews):
    # Wilson Score 置信下界，review少的游戏即使满分也会被降权
    # 参考: https://www.evanmiller.org/how-not-to-sort-by-average-rating.html
    n = total_reviews or 0
    if n <= 0 or positive_rate is None:
        return 0.0
    p = positive_rate / 100.0
    z = _WILSON_Z
    z2 = z * z
    numerator = p + z2 / (2 * n) - z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)
    denominator = 1 + z2 / n
    return max(0.0, numerator / denominator)
class Purchase(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    goods_id: int = 0
    price: float = 0.0
    at: float = 0.0
    market_price: Optional[float] = None
    sale_price: Optional[float] = None
    sold_at: Optional[float] = None
    pending_receipt: Optional[bool] = None
    assetid: Optional[str] = None
    listing: Optional[bool] = None
    listing_status: Optional[str] = None
    # External BUFF identifiers make a completed checkout reconcilable after a
    # restart and allow the database to reject accidental duplicate recording.
    buff_order_id: Optional[str] = Field(default=None, index=True)
    buff_sell_order_id: Optional[str] = None
    batch_id: Optional[str] = Field(default=None, index=True)
    bill_order_id: Optional[str] = None
class Sale(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    goods_id: int = 0
    price: float = 0.0
    at: float = 0.0
    assetid: Optional[str] = None
class ItemNameId(SQLModel, table=True):
    market_hash_name: str = Field(primary_key=True)
    item_nameid: str
class TradeOrder(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    platform: str = Field(default="buff", index=True)
    trade_offer_id: Optional[str] = Field(default=None, index=True)
    order_id: Optional[str] = Field(default=None, index=True)
    action: str = Field(default="ship", index=True)
    item_name: str = ""
    assetid: Optional[str] = None
    price: float = 0.0
    status: str = Field(default="pending", index=True)
    message: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0
class SteamDealGame(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    app_id: str = Field(index=True, unique=True)
    name: str = ""
    name_en: str = ""
    banner_url: str = ""
    positive_rate: Optional[float] = None   
    total_reviews: int = 0
    discount_percent: int = 0               
    deal_status: Optional[str] = None       
    price_cn: Optional[str] = None
    price_ru: Optional[str] = None
    price_kz: Optional[str] = None
    price_ua: Optional[str] = None
    price_pk: Optional[str] = None
    price_tr: Optional[str] = None
    price_ar: Optional[str] = None
    price_az: Optional[str] = None
    price_vn: Optional[str] = None
    price_id: Optional[str] = None
    price_in: Optional[str] = None
    price_br: Optional[str] = None
    price_cl: Optional[str] = None
    price_jp: Optional[str] = None
    price_hk: Optional[str] = None
    price_ph: Optional[str] = None
    original_cn: Optional[str] = None
    discount_cn: Optional[str] = None
    discount_ru: Optional[str] = None
    discount_kz: Optional[str] = None
    discount_ua: Optional[str] = None
    discount_pk: Optional[str] = None
    discount_tr: Optional[str] = None
    discount_ar: Optional[str] = None
    discount_az: Optional[str] = None
    discount_vn: Optional[str] = None
    discount_id: Optional[str] = None
    discount_in: Optional[str] = None
    discount_br: Optional[str] = None
    discount_cl: Optional[str] = None
    discount_jp: Optional[str] = None
    discount_hk: Optional[str] = None
    discount_ph: Optional[str] = None
    fetched_at: float = 0.0                 
    wilson_score: Optional[float] = None    
_engine = None
_engine_lock = threading.Lock()
def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{_DB_PATH}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
        return _engine
def get_session() -> Session:
    return Session(get_engine())
def init_db() -> None:
    """Create all tables if they don't exist, and run lightweight migrations."""
    from sqlalchemy import text as sa_text
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        try:
            conn.execute(sa_text("ALTER TABLE steamdealgame ADD COLUMN wilson_score REAL"))
            conn.commit()
        except Exception:
            pass  
    purchase_columns = (
        ("buff_order_id", "TEXT"),
        ("buff_sell_order_id", "TEXT"),
        ("batch_id", "TEXT"),
        ("bill_order_id", "TEXT"),
    )
    with engine.connect() as conn:
        for column_name, column_type in purchase_columns:
            try:
                conn.execute(
                    sa_text(
                        f"ALTER TABLE purchase ADD COLUMN {column_name} {column_type}"
                    )
                )
                conn.commit()
            except Exception:
                pass
        # Empty/NULL legacy values are intentionally excluded.  Every real
        # BUFF bill/order id may be recorded at most once.
        try:
            conn.execute(
                sa_text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ux_purchase_buff_order_id ON purchase(buff_order_id) "
                    "WHERE buff_order_id IS NOT NULL AND buff_order_id != ''"
                )
            )
            conn.commit()
        except Exception:
            pass
    with engine.connect() as conn:
        rows = conn.execute(
            sa_text("SELECT id, positive_rate, total_reviews FROM steamdealgame WHERE wilson_score IS NULL")
        ).fetchall()
        if rows:
            for row in rows:
                ws = _compute_wilson_score(row[1], row[2])
                conn.execute(
                    sa_text("UPDATE steamdealgame SET wilson_score = :ws WHERE id = :id"),
                    {"ws": ws, "id": row[0]},
                )
            conn.commit()
def _purchase_from_dict(d: dict) -> Purchase:
    return Purchase(
        name=d.get("name", ""),
        goods_id=int(d.get("goods_id", 0) or 0),
        price=float(d.get("price", 0)),
        at=float(d.get("at", 0)),
        market_price=float(d["market_price"]) if d.get("market_price") is not None else None,
        sale_price=float(d["sale_price"]) if d.get("sale_price") is not None else None,
        sold_at=float(d["sold_at"]) if d.get("sold_at") is not None else None,
        pending_receipt=bool(d["pending_receipt"]) if d.get("pending_receipt") is not None else None,
        assetid=str(d["assetid"]) if d.get("assetid") is not None else None,
        listing=bool(d["listing"]) if d.get("listing") is not None else None,
        listing_status=str(d["listing_status"]) if d.get("listing_status") is not None else None,
        buff_order_id=str(d["buff_order_id"]) if d.get("buff_order_id") else None,
        buff_sell_order_id=str(d["buff_sell_order_id"]) if d.get("buff_sell_order_id") else None,
        batch_id=str(d["batch_id"]) if d.get("batch_id") else None,
        bill_order_id=str(d["bill_order_id"]) if d.get("bill_order_id") else None,
    )
def _sale_from_dict(d: dict) -> Sale:
    return Sale(
        name=d.get("name", ""),
        goods_id=int(d.get("goods_id", 0) or 0),
        price=float(d.get("price", 0)),
        at=float(d.get("at", 0)),
        assetid=str(d["assetid"]) if d.get("assetid") is not None else None,
    )
def _purchase_to_dict(p: Purchase) -> dict:
    d = {
        "_db_id": p.id,  
        "name": p.name,
        "goods_id": p.goods_id,
        "price": p.price,
        "at": p.at,
    }
    if p.market_price is not None:
        d["market_price"] = p.market_price
    if p.sale_price is not None:
        d["sale_price"] = p.sale_price
    if p.sold_at is not None:
        d["sold_at"] = p.sold_at
    if p.pending_receipt is not None:
        d["pending_receipt"] = p.pending_receipt
    if p.assetid is not None:
        d["assetid"] = p.assetid
    if p.listing is not None:
        d["listing"] = p.listing
    if p.listing_status is not None:
        d["listing_status"] = p.listing_status
    if p.buff_order_id is not None:
        d["buff_order_id"] = p.buff_order_id
    if p.buff_sell_order_id is not None:
        d["buff_sell_order_id"] = p.buff_sell_order_id
    if p.batch_id is not None:
        d["batch_id"] = p.batch_id
    if p.bill_order_id is not None:
        d["bill_order_id"] = p.bill_order_id
    return d
def _sale_to_dict(s: Sale) -> dict:
    d = {
        "name": s.name,
        "goods_id": s.goods_id,
        "price": s.price,
        "at": s.at,
    }
    if s.assetid is not None:
        d["assetid"] = s.assetid
    return d
def migrate_from_json() -> bool:
    """
    One-time migration: read transactions.json → insert into SQLite →
    rename JSON to .bak.  Returns True if migration happened.
    """
    if not _TRANSACTIONS_JSON.exists():
        return False
    with get_session() as session:
        existing = session.exec(select(Purchase).limit(1)).first()
        if existing is not None:
            return False
    try:
        with open(_TRANSACTIONS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    purchases = data.get("purchases", [])
    sales = data.get("sales", [])
    with get_session() as session:
        for p in purchases:
            session.add(_purchase_from_dict(p))
        for s in sales:
            session.add(_sale_from_dict(s))
        session.commit()
    try:
        if _TRANSACTIONS_BAK.exists():
            os.remove(str(_TRANSACTIONS_BAK))
        os.rename(str(_TRANSACTIONS_JSON), str(_TRANSACTIONS_BAK))
    except OSError:
        pass
    return True
_PURCHASE_UPDATABLE = frozenset({
    "name", "price", "goods_id", "market_price", "sale_price",
    "sold_at", "pending_receipt", "assetid", "listing", "listing_status",
    "buff_order_id", "buff_sell_order_id", "batch_id", "bill_order_id",
})
_SALE_UPDATABLE = frozenset({"name", "price", "goods_id", "assetid", "at"})
def db_append_purchase(p: dict) -> None:
    with get_session() as session:
        session.add(_purchase_from_dict(p))
        session.commit()
def db_get_purchases() -> list:
    with get_session() as session:
        rows = session.exec(select(Purchase).order_by(Purchase.id)).all()
        return [_purchase_to_dict(r) for r in rows]
def db_append_sale(s: dict) -> None:
    with get_session() as session:
        session.add(_sale_from_dict(s))
        session.commit()
def db_get_sales() -> list:
    with get_session() as session:
        rows = session.exec(select(Sale).order_by(Sale.id)).all()
        return [_sale_to_dict(r) for r in rows]
def db_clear_transactions() -> None:
    from sqlmodel import delete as sql_delete
    with get_session() as session:
        session.exec(sql_delete(Purchase))
        session.exec(sql_delete(Sale))
        session.commit()
def db_replace_transactions(purchases: list, sales: list) -> None:
    from sqlmodel import delete as sql_delete
    with get_session() as session:
        session.exec(sql_delete(Purchase))
        session.exec(sql_delete(Sale))
        for p in purchases:
            session.add(_purchase_from_dict(p))
        for s in sales:
            session.add(_sale_from_dict(s))
        session.commit()
def db_delete_purchase(idx: int) -> bool:
    """Delete purchase by positional index (0-based, ordered by id)."""
    with get_session() as session:
        rows = session.exec(select(Purchase).order_by(Purchase.id)).all()
        if 0 <= idx < len(rows):
            session.delete(rows[idx])
            session.commit()
            return True
    return False
def db_delete_sale(idx: int) -> bool:
    with get_session() as session:
        rows = session.exec(select(Sale).order_by(Sale.id)).all()
        if 0 <= idx < len(rows):
            session.delete(rows[idx])
            session.commit()
            return True
    return False
def db_update_purchase(idx: int, data: dict) -> bool:
    """按位置索引更新（兼容旧接口，UI 路由使用）。"""
    with get_session() as session:
        rows = session.exec(select(Purchase).order_by(Purchase.id)).all()
        if 0 <= idx < len(rows):
            row = rows[idx]
            for k, v in data.items():
                if k in _PURCHASE_UPDATABLE:
                    setattr(row, k, v)
            session.add(row)
            session.commit()
            return True
    return False
def db_update_purchase_by_id(db_id: int, data: dict) -> bool:
    """按主键 ID 更新，O(1) 操作，推荐内部 worker 使用。"""
    if not db_id:
        return False
    with get_session() as session:
        row = session.get(Purchase, db_id)
        if row is None:
            return False
        for k, v in data.items():
            if k in _PURCHASE_UPDATABLE:
                setattr(row, k, v)
        session.add(row)
        session.commit()
        return True
def db_delete_purchase_by_id(db_id: int) -> bool:
    """按主键 ID 删除，O(1) 操作。"""
    if not db_id:
        return False
    with get_session() as session:
        row = session.get(Purchase, db_id)
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True
def db_update_sale(idx: int, data: dict) -> bool:
    with get_session() as session:
        rows = session.exec(select(Sale).order_by(Sale.id)).all()
        if 0 <= idx < len(rows):
            row = rows[idx]
            for k, v in data.items():
                if k in _SALE_UPDATABLE:
                    setattr(row, k, v)
            session.add(row)
            session.commit()
            return True
    return False
def db_delete_sale_by_id(db_id: int) -> bool:
    """Delete by primary ID, O(1) operation."""
    if not db_id:
        return False
    with get_session() as session:
        row = session.get(Sale, db_id)
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True
def db_get_item_nameid(market_hash_name: str) -> Optional[str]:
    with get_session() as session:
        item = session.exec(
            select(ItemNameId).where(ItemNameId.market_hash_name == market_hash_name)
        ).first()
        return item.item_nameid if item else None
def db_set_item_nameid(market_hash_name: str, item_nameid: str) -> None:
    with get_session() as session:
        item = session.exec(
            select(ItemNameId).where(ItemNameId.market_hash_name == market_hash_name)
        ).first()
        if item:
            item.item_nameid = item_nameid
        else:
            item = ItemNameId(market_hash_name=market_hash_name, item_nameid=item_nameid)
        session.add(item)
        session.commit()
_REGION_CODES = [
    "cn", "ru", "kz", "ua", "pk", "tr", "ar", "az",
    "vn", "id", "in", "br", "cl", "jp", "hk", "ph",
]
def _game_row_to_dict(r: SteamDealGame) -> dict:  # Refactored: was copy-pasted verbatim into both db_get_steam_deals and db_get_steam_deals_by_app_ids
    d = {
        "app_id": r.app_id,
        "name": r.name,
        "name_en": r.name_en,
        "banner_url": r.banner_url,
        "positive_rate": r.positive_rate,
        "total_reviews": r.total_reviews,
        "discount_percent": r.discount_percent,
        "deal_status": r.deal_status,
        "fetched_at": r.fetched_at,
        "prices": {},
        "discounts": {},
        "original_cn": r.original_cn,
    }
    for rc in _REGION_CODES:
        d["prices"][rc] = getattr(r, f"price_{rc}", None)
        d["discounts"][rc] = getattr(r, f"discount_{rc}", None)
    return d
def db_upsert_steam_deal(data: dict) -> None:
    """Insert or update a SteamDealGame by app_id."""
    data = dict(data)  
    data["wilson_score"] = _compute_wilson_score(
        data.get("positive_rate"), data.get("total_reviews")
    )
    with get_session() as session:
        existing = session.exec(
            select(SteamDealGame).where(SteamDealGame.app_id == str(data["app_id"]))
        ).first()
        if existing:
            for k, v in data.items():
                if k != "id" and hasattr(existing, k):
                    setattr(existing, k, v)
            session.add(existing)
        else:
            game = SteamDealGame(**{k: v for k, v in data.items() if hasattr(SteamDealGame, k)})
            session.add(game)
        session.commit()
def db_get_steam_deals(
    offset: int = 0,
    limit: int = 30,
    search: str = "",
    sort_by: str = "discount_percent",
    sort_dir: str = "asc",
    compare_region: str = "",
    deal_status_filter: str = "",
) -> list:
    """Paginated query with optional search and sorting."""
    from sqlmodel import col, text as sql_text, or_
    with get_session() as session:
        stmt = select(SteamDealGame)
        if search:
            stmt = stmt.where(
                or_(
                    col(SteamDealGame.name).contains(search),
                    col(SteamDealGame.name_en).contains(search)
                )
            )
        if deal_status_filter and deal_status_filter != "全部状态":
            stmt = stmt.where(SteamDealGame.deal_status == deal_status_filter)
        order_col = None
        if sort_by == "positive_rate":
            order_col = SteamDealGame.positive_rate
        elif sort_by == "total_reviews":
            order_col = SteamDealGame.total_reviews
        elif sort_by == "discount_percent":
            order_col = SteamDealGame.discount_percent
        elif sort_by == "name":
            order_col = SteamDealGame.name
        elif sort_by in ("default_recommend", "price_diff", "discount_abs", "region_value"):
            # price_diff/discount_abs 这俩路由层已经走内存排序了
            # 这里是 search+filter 组合时的回退，必须加分页防止全表返回
            stmt = stmt.order_by(col(SteamDealGame.wilson_score).desc())
            stmt = stmt.offset(offset).limit(limit)
        else:
            stmt = stmt.order_by(col(SteamDealGame.wilson_score).desc())
            stmt = stmt.offset(offset).limit(limit)
        if order_col is not None:
            if sort_dir == "desc":
                stmt = stmt.order_by(col(order_col).desc())
            else:
                stmt = stmt.order_by(col(order_col).asc())
            stmt = stmt.offset(offset).limit(limit)
        rows = session.exec(stmt).all()
        return [_game_row_to_dict(r) for r in rows]
def db_get_steam_deals_count(search: str = "") -> int:
    from sqlmodel import col, func, or_
    with get_session() as session:
        stmt = select(func.count()).select_from(SteamDealGame)
        if search:
            stmt = stmt.where(
                or_(
                    col(SteamDealGame.name).contains(search),
                    col(SteamDealGame.name_en).contains(search)
                )
            )
        return session.exec(stmt).one()
def db_get_steam_deals_last_update() -> Optional[float]:
    from sqlmodel import func
    with get_session() as session:
        result = session.exec(
            select(func.max(SteamDealGame.fetched_at))
        ).first()
        return result if result else None
def db_clear_steam_deals() -> None:
    from sqlmodel import delete as sql_delete
    with get_session() as session:
        session.exec(sql_delete(SteamDealGame))
        session.commit()
def db_delete_steam_deals_except(app_ids: List[str]) -> int:
    """Delete Steam deal rows whose app_id is not in the latest scan set."""
    from sqlmodel import col, delete as sql_delete
    keep = {str(app_id) for app_id in app_ids if app_id}
    if not keep:
        return 0
    with get_session() as session:
        existing = session.exec(select(SteamDealGame.app_id)).all()
        stale = [str(app_id) for app_id in existing if str(app_id) not in keep]
        deleted = 0
        for i in range(0, len(stale), 500):
            chunk = stale[i:i + 500]
            if not chunk:
                continue
            session.exec(
                sql_delete(SteamDealGame).where(col(SteamDealGame.app_id).in_(chunk))
            )
            deleted += len(chunk)
        session.commit()
        return deleted
def db_get_steam_deals_price_snapshot() -> list:
    """Lightweight fetch: only price-related columns for ALL games.
    Used to build an in-memory sort index (price_diff / discount_abs) without
    the cost of fetching every column for 20 000+ rows. Returns a list of
    plain dicts with keys: app_id, original_cn, price_<cc> for each region.
    """
    from sqlalchemy import text as sa_text
    price_cols = ", ".join(["app_id", "original_cn"] + [f"price_{rc}" for rc in _REGION_CODES])
    with get_engine().connect() as conn:
        rows = conn.execute(sa_text(f"SELECT {price_cols} FROM steamdealgame")).fetchall()
    result = []
    for row in rows:
        d = {"app_id": row[0], "original_cn": row[1]}
        for i, rc in enumerate(_REGION_CODES):
            d[f"price_{rc}"] = row[2 + i]
        result.append(d)
    return result
def db_get_steam_deals_review_snapshot() -> list:
    """Lightweight fetch: only app_id and total_reviews for ALL games.
    Used to filter games with >= 2000 reviews for region_value sort mode.
    """
    from sqlalchemy import text as sa_text
    with get_engine().connect() as conn:
        rows = conn.execute(sa_text("SELECT app_id, total_reviews FROM steamdealgame")).fetchall()
    return [{"app_id": row[0], "total_reviews": row[1]} for row in rows]
def db_get_steam_deals_by_app_ids(app_ids: List[str]) -> list:
    """Fetch full game data for a specific ordered list of app_ids.
    Only fetches the rows listed in app_ids and preserves the given order.
    Used after the sort index resolves which 30 games to show on this page.
    """
    if not app_ids:
        return []
    from sqlmodel import col
    with get_session() as session:
        rows = session.exec(
            select(SteamDealGame).where(col(SteamDealGame.app_id).in_(app_ids))
        ).all()
        id_to_row = {r.app_id: _game_row_to_dict(r) for r in rows}
        return [id_to_row[aid] for aid in app_ids if aid in id_to_row]
def db_add_trade_order(trade_dict: dict) -> dict:
    import time
    now = time.time()
    row = TradeOrder(
        platform=trade_dict.get("platform", "buff"),
        trade_offer_id=trade_dict.get("trade_offer_id"),
        order_id=trade_dict.get("order_id"),
        action=trade_dict.get("action", "ship"),
        item_name=trade_dict.get("item_name", ""),
        assetid=trade_dict.get("assetid"),
        price=float(trade_dict.get("price") or 0.0),
        status=trade_dict.get("status", "pending"),
        message=trade_dict.get("message"),
        created_at=float(trade_dict.get("created_at") or now),
        updated_at=now,
    )
    with get_session() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.model_dump()

def db_get_trade_orders(limit: int = 50, platform: Optional[str] = None) -> List[dict]:
    with get_session() as session:
        stmt = select(TradeOrder)
        if platform:
            stmt = stmt.where(TradeOrder.platform == platform)
        stmt = stmt.order_by(TradeOrder.created_at.desc()).limit(limit)
        rows = session.exec(stmt).all()
        return [r.model_dump() for r in rows]

def db_update_trade_order(trade_id: int, updates: dict) -> bool:
    import time
    with get_session() as session:
        row = session.get(TradeOrder, trade_id)
        if not row:
            return False
        for k, v in updates.items():
            if hasattr(row, k):
                setattr(row, k, v)
        row.updated_at = time.time()
        session.add(row)
        session.commit()
        return True
