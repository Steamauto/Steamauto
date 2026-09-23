import argparse
import io
import json
import os
import pathlib
import sys
import re
import time
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from app.database import SteamDealGame, get_engine
from app.config_loader import load_app_config_validated
from sqlmodel import Session, select, col
from utils.proxy_manager import get_proxy_manager
from utils.time import (
    resolve_configured_timezone,
    timestamp_in_configured_timezone,
)

EXCHANGE_RATE_FILE = ROOT / "config" / "exchange_rate.json"
EXRATES = {}
if EXCHANGE_RATE_FILE.exists():
    try:
        j = json.loads(EXCHANGE_RATE_FILE.read_text("utf-8"))
        EXRATES = j.get("rates", {})
    except Exception as e:
        print(f"加载汇率失败: {e}")

DISPLAY_REGIONS = [
    ("ru", "俄罗斯"),
    ("ua", "乌克兰"),
    ("tr", "土耳其"),
    ("ar", "阿根廷"),
    ("kz", "哈萨克斯坦"),
    ("hk", "中国香港"),
    ("ph", "菲律宾"),
    ("id", "印尼"),
    ("in", "南亚/印度"),
    ("vn", "越南"),
    ("br", "巴西"),
    ("cl", "智利"),
    ("az", "阿塞拜疆"),
    ("jp", "日本"),
]

W, H = 1920, 1080

def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = []
    if bold:
        candidates.extend([
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
        ])
    candidates.extend([
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"
    ])
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size, index=0)
            except Exception:
                pass
    return ImageFont.load_default()

def _load_currency_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = []
    if bold:
        candidates.extend([
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
        ])
    candidates.extend([
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
    ])
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size, index=0)
            except Exception:
                pass
    return _load_font(size, bold=bold)

def _download_banner(url: str) -> Optional[Image.Image]:
    if not url:
        return None
        
    pm = get_proxy_manager()
    attempts = 3
    last_err = None
    
    for attempt in range(attempts):
        proxies = pm.get_proxies_for_request(failed=(attempt > 0))
        try:
            resp = requests.get(url, timeout=(5.0, 10.0), proxies=proxies)
            resp.raise_for_status()
            return Image.open(io.BytesIO(resp.content)).convert("RGBA")
        except Exception as e:
            last_err = e
            print(f"    [下载背景图] 第 {attempt + 1}/{attempts} 次尝试失败 -> {type(e).__name__}: {e}")
            time.sleep(1.5)
            
    print(f"  ❌ 背景图最终下载失败: {url}")
    raise RuntimeError("Failed to download banner image due to network issues.")

def _get_flag(country_code: str) -> Optional[Image.Image]:
    flag_dir = ROOT / "flags"
    flag_dir.mkdir(parents=True, exist_ok=True)
    
    code_map = {"uk": "gb", "kz": "kz"} 
    c_code = code_map.get(country_code.lower(), country_code.lower())
    
    flag_path = flag_dir / f"{c_code}.png"
    
    if not flag_path.exists():
        try:
            url = f"https://flagcdn.com/w40/{c_code}.png"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                with open(flag_path, "wb") as f:
                    f.write(resp.content)
            else:
                return None
        except:
            return None
            
    try:
        return Image.open(flag_path).convert("RGBA")
    except:
        return None

def draw_rounded_rect(canvas_im: Image.Image, xy, radius: int, fill, outline=None, width=1):
    x0, y0, x1, y1 = xy
    w = int(x1 - x0)
    h = int(y1 - y0)
    if w <= 0 or h <= 0: return
    
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    
    try:
        draw_ov.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=fill, outline=outline, width=width)
    except AttributeError:
        draw_ov.rectangle((0, 0, w - 1, h - 1), fill=fill, outline=outline, width=width)
        
    canvas_im.alpha_composite(overlay, (int(x0), int(y0)))

def draw_antialiased_circle(canvas_im: Image.Image, center, radius: int, fill, scale: int = 4):
    cx, cy = center
    pad = 2
    size = (radius * 2 + pad * 2) * scale
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_hi = ImageDraw.Draw(overlay)
    bbox = (
        pad * scale,
        pad * scale,
        (pad + radius * 2) * scale - 1,
        (pad + radius * 2) * scale - 1,
    )
    draw_hi.ellipse(bbox, fill=fill)
    overlay = overlay.resize((size // scale, size // scale), Image.LANCZOS)
    canvas_im.alpha_composite(overlay, (int(cx - radius - pad), int(cy - radius - pad)))

def get_text_bbox(draw, text, font):
    if hasattr(draw, "textbbox"):
        return draw.textbbox((0, 0), text, font=font)
    if hasattr(draw, "textsize"):
        w, h = draw.textsize(text, font=font)
    else:
        w, h = len(str(text)) * font.size * 0.6, font.size
    return (0, 0, w, h)

def get_text_size(draw, text, font):
    if hasattr(draw, "textbbox"):
        left, top, right, bottom = get_text_bbox(draw, text, font)
        return right - left, bottom - top
    elif hasattr(draw, "textsize"):
        return draw.textsize(text, font=font)
    else:
        return len(str(text)) * font.size * 0.6, font.size

def draw_text_middle(draw, xy, text, font, fill):
    x, y = xy
    left, top, right, bottom = get_text_bbox(draw, text, font)
    w = right - left
    h = bottom - top
    draw.text((x - w / 2 - left, y - h / 2 - top), text, font=font, fill=fill)
    return w, h

def draw_text_left(draw, xy, text, font, fill):
    x, y = xy
    left, top, right, bottom = get_text_bbox(draw, text, font)
    w = right - left
    h = bottom - top
    draw.text((x - left, y - h / 2 - top), text, font=font, fill=fill)
    return w, h

def parse_price_to_rmb(price_str: str) -> float:
    if not price_str or "锁区" in price_str or price_str == "—" or price_str == "":
        return 999999.0
    
    is_comma_decimal = "R$" in price_str or ",-" in price_str or ("," in price_str and "." not in price_str and len(price_str.split(",")[-1]) <= 2)
    
    s = price_str
    if is_comma_decimal:
        s = s.replace(".", "") 
        s = s.replace(",", ".") 
    else:
        if "CLP" in s or "₫" in s or "VND" in s or "Rp" in s:
            s = s.replace(".", "").replace(",", "") 
        else:
            s = s.replace(",", "") 

    s = s.replace(" ", "")
    m = re.findall(r"[\d]+(?:\.\d+)?", s)
    if not m:
        return 999999.0
    val = float(m[0])
    
    if "USD" in s or "$" in price_str and "HK" not in price_str and "CAD" not in price_str and "CLP" not in price_str and "R$" not in price_str:
        return val * EXRATES.get("USD", 7.2)
    if "руб" in s.lower() or "rub" in s.lower():
        return val * EXRATES.get("RUB", 0.08)
    if "HK" in s:
        return val * EXRATES.get("HKD", 0.9)
    if "UAH" in s or "₴" in s:
        return val * EXRATES.get("UAH", 0.18)
    if "KZT" in s or "₸" in s:
        return val * EXRATES.get("KZT", 0.016)
    if "₹" in s or "INR" in s:
        return val * EXRATES.get("INR", 0.086)
    if "Rp" in s:
        return val * EXRATES.get("IDR", 0.00045)
    if "₫" in s or "VND" in s:
        return val * EXRATES.get("VND", 0.00028)
    if "R$" in s or "BRL" in s:
        return val * EXRATES.get("BRL", 1.4)
    if "CLP" in s:
        return val * EXRATES.get("CLP", 0.007)
    if "TRY" in s or "TL" in s:
        return val * EXRATES.get("TRY", 0.22)
    if "₱" in price_str or "PHP" in s.upper() or ("P" in price_str and "Rp" not in price_str):
        return val * EXRATES.get("PHP", 0.12)
        
    return val 

def format_original_price(region_code: str, price_text: str) -> str:
    if region_code == "ph":
        return re.sub(r"^(?:PHP\s*|P\s*)(?=\d)", "₱", price_text.strip(), flags=re.IGNORECASE)
    return price_text

def wrap_text(draw, text, font, max_width):
    if not text:
        return []
    lines = []
    words = text.split(" ") 
    current_line = words[0]
    for word in words[1:]:
        separator = " " 
        test_line = current_line + separator + word
        tw, _ = get_text_size(draw, test_line, font)
        
        if tw <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines

def generate_card(game: SteamDealGame, out_path: str) -> bool:
    print(f"  生成: {game.name or game.name_en} (app_id={game.app_id})")

    banner = _download_banner(game.banner_url) if game.banner_url else None
    if banner:
        bg = banner.resize((W, H), Image.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=25))
    else:
        bg = Image.new("RGBA", (W, H), (15, 18, 24, 255))
        
    canvas = bg.copy().convert("RGBA")
    
    overlay = Image.new("RGBA", (W, H), (10, 12, 16, 150))
    canvas.alpha_composite(overlay)
    
    draw = ImageDraw.Draw(canvas)

    MARGIN_LEFT = 140
    LEFT_WIDTH = 620
    y_cursor = 240
    
    draw_rounded_rect(canvas, (MARGIN_LEFT, y_cursor - 60, MARGIN_LEFT + 80, y_cursor - 52), radius=4, fill=(58, 160, 255, 255))

    f_title = _load_font(90, bold=True)
    f_title_en = _load_font(40, bold=False)
    
    gname = game.name or game.name_en or "Unknown"
    title_lines = wrap_text(draw, gname, f_title, LEFT_WIDTH)
    
    if len(title_lines) > 2:
        f_title = _load_font(72, bold=True)
        title_lines = wrap_text(draw, gname, f_title, LEFT_WIDTH)

    for line in title_lines[:3]:
        tw, th = get_text_size(draw, line, f_title)
        draw.text((MARGIN_LEFT, y_cursor), line, font=f_title, fill=(255, 255, 255, 255))
        y_cursor += (th or 90) + 12
    
    y_cursor += 15

    if game.name_en and game.name_en != gname:
        en_str = game.name_en
        if len(en_str) > 50: en_str = en_str[:47] + "..."
        lines_en = wrap_text(draw, en_str, f_title_en, LEFT_WIDTH)
        for line in lines_en[:2]:
            tw, th = get_text_size(draw, line, f_title_en)
            draw.text((MARGIN_LEFT, y_cursor), line, font=f_title_en, fill=(150, 160, 175, 255))
            y_cursor += (th or 40) + 8
        y_cursor += 30
    else:
        y_cursor += 20

    disc_val = game.discount_percent or 0
    f_badge = _load_font(42, bold=True)
    
    if disc_val < 0:
        disc_str = f"{disc_val}% OFF".replace("-", "")
        tw, th = get_text_size(draw, disc_str, f_badge)
        
        bw = tw + 40
        bh = 66
        draw_rounded_rect(canvas, (MARGIN_LEFT, y_cursor, MARGIN_LEFT + bw, y_cursor + bh), radius=bh//2, fill=(171, 235, 45, 255))
        draw_text_middle(draw, (MARGIN_LEFT + bw/2, y_cursor + bh/2), disc_str, f_badge, fill=(16, 24, 16, 255))
        
        y_cursor += bh + 45
    else:
        y_cursor += 20

    f_tag = _load_font(28, bold=True)
    rx = MARGIN_LEFT
    ry = y_cursor
    
    tags = []
    
    raw_status = game.deal_status or "平史低"
    if "普通" not in raw_status:
        tags.append((raw_status, (239, 68, 68, 80), (255, 200, 200, 255), (239, 68, 68, 180))) 
    
    if game.positive_rate:
        rate = game.positive_rate
        if rate >= 95:
            rate_txt, c_bg, c_txt, c_bd = ("好评如潮", (59, 130, 246, 140), (210, 235, 255, 255), (59, 130, 246, 200))
        elif rate >= 80:
            rate_txt, c_bg, c_txt, c_bd = ("特别好评", (59, 130, 246, 140), (210, 235, 255, 255), (59, 130, 246, 200))
        elif rate >= 70:
            rate_txt, c_bg, c_txt, c_bd = ("多半好评", (59, 130, 246, 140), (210, 235, 255, 255), (59, 130, 246, 200))
        elif rate >= 40:
            rate_txt, c_bg, c_txt, c_bd = ("褒贬不一", (245, 158, 11, 140), (255, 240, 200, 255), (245, 158, 11, 200))
        elif rate >= 20:
            rate_txt, c_bg, c_txt, c_bd = ("多半差评", (239, 68, 68, 140), (255, 220, 220, 255), (239, 68, 68, 200))
        else:
            rate_txt, c_bg, c_txt, c_bd = ("差评如潮", (239, 68, 68, 140), (255, 220, 220, 255), (239, 68, 68, 200))
        tags.append((rate_txt, c_bg, c_txt, c_bd))

    for txt, bg_clr, txt_clr, border_clr in tags:
        tw, th = get_text_size(draw, txt, f_tag)
        bw = tw + 32
        bh = 48
        draw_rounded_rect(canvas, (rx, ry, rx + bw, ry + bh), radius=bh//2, fill=bg_clr, outline=border_clr, width=2)
        draw_text_middle(draw, (rx + bw/2, ry + bh/2), txt, f_tag, fill=txt_clr)
        rx += bw + 16

    f_footer_bold = _load_font(26, bold=True)
    f_footer_light = _load_font(24, bold=False)
    draw.text((MARGIN_LEFT, H - 140), "AETHER SWAP", font=f_footer_bold, fill=(255, 255, 255, 140))
    configured_timezone, _timezone_label = resolve_configured_timezone(
        (load_app_config_validated().get("system") or {})
    )
    dt_str = (
        timestamp_in_configured_timezone(
            game.fetched_at,
            configured_timezone,
        ).strftime("%Y.%m.%d")
        if game.fetched_at
        else "-"
    )
    draw.text((MARGIN_LEFT, H - 105), f"UPDATE • {dt_str}", font=f_footer_light, fill=(255, 255, 255, 90))

    PANEL_X = 860   
    PANEL_W = 960  

    f_row_reg = _load_font(34, bold=True)     
    f_row_prmb = _load_font(42, bold=True)    
    f_row_porg = _load_currency_font(26, bold=True)
    f_row_save = _load_font(22, bold=True)    

    cn_price_str = game.price_cn or ""
    cn_rmb = parse_price_to_rmb(cn_price_str) if cn_price_str else 999999.0

    regions_data = []
    for code, label in DISPLAY_REGIONS:
        d = getattr(game, f"price_{code}", None) or "锁区"
        if d == "锁区": continue
        rmb_val = parse_price_to_rmb(d)
        if rmb_val >= 999999.0: continue
        regions_data.append({"code": code, "label": label, "orig": d, "rmb": rmb_val})
        
    regions_data.sort(key=lambda x: x["rmb"])
    regions_data = regions_data[:10] 

    current_y = 25     
    ROW_H = 76         
    ROW_GAP = 20       
    draw_rounded_rect(canvas, (PANEL_X, current_y, PANEL_X + PANEL_W, current_y + ROW_H), 
                      radius=16, fill=(30, 15, 15, 180), outline=(239, 68, 68, 120), width=1)
    
    tag_cn_w = 70
    tag_cn_x = PANEL_X + 24
    draw_rounded_rect(canvas, (tag_cn_x, current_y + 14, tag_cn_x + tag_cn_w, current_y + ROW_H - 14), radius=8, fill=(234, 56, 76, 255))
    f_mini = _load_font(24, bold=True)
    draw_text_middle(draw, (tag_cn_x + tag_cn_w/2, current_y + ROW_H/2), "CNY", f_mini, fill=(255, 255, 255, 255))
    
    cn_flag = _get_flag("cn")
    if cn_flag:
        cn_flag = cn_flag.resize((36, 24), Image.LANCZOS)
        canvas.paste(cn_flag, (PANEL_X + 105, int(current_y + ROW_H/2 - 12)), cn_flag)
        
    draw_text_left(draw, (PANEL_X + 155, current_y + ROW_H/2), "中国", f_row_reg, fill=(240, 240, 240, 255))
    
    cn_price_disp = f"￥{cn_rmb:.2f}" if cn_rmb < 99999.0 else "—"
    pw_cn, _ = get_text_size(draw, cn_price_disp, f_row_prmb)
    draw_text_middle(draw, (PANEL_X + PANEL_W - 30 - pw_cn/2, current_y + ROW_H/2), cn_price_disp, f_row_prmb, fill=(255, 255, 255, 255))
    
    current_y += ROW_H + ROW_GAP 
    
    for idx, reg in enumerate(regions_data):
        row_cy = current_y + ROW_H/2
        
        draw_rounded_rect(canvas, (PANEL_X, current_y, PANEL_X + PANEL_W, current_y + ROW_H), 
                          radius=16, fill=(15, 20, 25, 180), outline=(255, 255, 255, 30), width=1)
        
        dot_cx = PANEL_X + 30
        rank_x = PANEL_X + 43
        
        if idx == 0:     dot_clr = (171, 235, 45, 255) 
        elif idx == 1:   dot_clr = (56, 189, 248, 255) 
        elif idx == 2:   dot_clr = (192, 132, 252, 255)
        else:            dot_clr = (156, 163, 175, 255)
        
        draw_antialiased_circle(canvas, (dot_cx, row_cy), 6, dot_clr)
        f_rank = _load_font(20, bold=True)
        draw_text_left(draw, (rank_x, row_cy), f"#{idx+1}", f_rank, fill=dot_clr)

        flag_im = _get_flag(reg["code"])
        if flag_im:
            flag_im = flag_im.resize((30, 20), Image.LANCZOS)
            canvas.paste(flag_im, (PANEL_X + 85, int(row_cy - 10)), flag_im)

        pill_x = PANEL_X + 130
        code_str = reg["code"].upper()
        draw_rounded_rect(canvas, (pill_x, row_cy - 14, pill_x + 46, row_cy + 14), radius=6, fill=(255, 255, 255, 20))
        f_code = _load_font(20, bold=True)
        draw_text_middle(draw, (pill_x + 23, row_cy), code_str, f_code, fill=(210, 215, 220, 255))
        
        label_x = PANEL_X + 195
        draw_text_left(draw, (label_x, row_cy), reg["label"], f_row_reg, fill=(240, 245, 250, 255))

        pr_str = f"￥{reg['rmb']:.2f}"
        pw, _ = get_text_size(draw, pr_str, f_row_prmb)
        right_x = PANEL_X + PANEL_W - 30
        draw_text_middle(draw, (right_x - pw/2, row_cy), pr_str, f_row_prmb, fill=(255, 255, 255, 255))
        
        obj_x = right_x - pw - 30
        
        if cn_rmb < 99999.0 and cn_rmb > reg["rmb"]:
            diff = cn_rmb - reg["rmb"]
            save_txt = f"省 ¥{diff:.1f}"
            tw_s, _ = get_text_size(draw, save_txt, f_row_save)
            bw_s = tw_s + 20
            
            draw_rounded_rect(canvas, (obj_x - bw_s, row_cy - 14, obj_x, row_cy + 14), radius=14, fill=(34, 197, 94, 30), outline=(74, 222, 128, 120), width=1)
            draw_text_middle(draw, (obj_x - bw_s/2, row_cy), save_txt, f_row_save, fill=(74, 222, 128, 255)) 
            obj_x -= bw_s + 24
            
        orig_str = format_original_price(reg["code"], reg["orig"])
        ow, _ = get_text_size(draw, orig_str, f_row_porg)
        
        orig_color = (74, 222, 128, 255) if "%" in orig_str else (160, 170, 180, 255)
        draw_text_middle(draw, (obj_x - ow/2, row_cy), orig_str, f_row_porg, fill=orig_color) 

        current_y += ROW_H + ROW_GAP

    final = canvas.convert("RGB")
    final.save(out_path, "PNG", quality=95, optimize=True)
    print(f"  ✅ [Floating Glass Cards] 已保存 → {out_path}")
    return True

