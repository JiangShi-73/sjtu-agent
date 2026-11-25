"""把「新活动」画成一张图，给 QQ 机器人发。

900 宽、浅色卡片风，和日历图（calrender.py）同一套配色；不过这里用 Pillow
直接画，不走浏览器截图——机器人发图不能等浏览器起来。

每条卡片：序号、标题、时间 + 地点、属性标签（劳动学时 / 新时代讲座）。

缓存：~/.xinge/activities/ 下按「小时窗口 + 当前小时」命名，10 分钟内复用。
"""

from __future__ import annotations

import io
import os
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".xinge", "activities")
TTL_SECONDS = 600               # 10 分钟内复用同一张
WIDTH = 900
PAD = 28                        # 画布左右留白
MAX_ITEMS = 8                   # 一张图最多画几条，太多就画不下
RADIUS = 16

# 配色：和 calrender 的浅色卡片一致
BG = (246, 247, 250)
CARD = (255, 255, 255)
TEXT = (31, 36, 48)
SUB = (110, 117, 134)
MUTED = (154, 161, 178)
ACCENT = (26, 79, 160)
ACCENT_SOFT = (230, 240, 255)
LABOR_BG = (233, 242, 255)
LABOR_FG = (26, 79, 160)
NEWERA_BG = (243, 237, 255)
NEWERA_FG = (95, 63, 170)
DIVIDER = (235, 239, 245)

REGULAR_FONTS = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
)
BOLD_FONTS = (
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
)

_font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


# ---------------- 字体 / 文字 ----------------

def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """找系统中文字体；找不到退回 Pillow 自带位图字体（至少不崩）。"""
    key = (size, bold)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    font: ImageFont.FreeTypeFont | None = None
    for path in (BOLD_FONTS if bold else REGULAR_FONTS):
        try:
            font = ImageFont.truetype(path, size)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return draw.textlength(text, font=font)


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> str:
    """单行放不下就截断加省略号。"""
    text = " ".join(str(text or "").split())
    if not text or _text_width(draw, text, font) <= max_width:
        return text
    ellipsis = "…"
    keep = ""
    for char in text:
        if _text_width(draw, keep + char + ellipsis, font) > max_width:
            break
        keep += char
    return (keep + ellipsis) if keep else ellipsis


def _tokens(text: str) -> list[str]:
    """把文字切成「英文单词 / 单个汉字 / 标点」的小块，方便换行。"""
    out: list[str] = []
    word = ""
    for char in text:
        if char.isascii() and (char.isalnum() or char in "@._:/%+-"):
            word += char
            continue
        if word:
            out.append(word)
            word = ""
        out.append(char)
    if word:
        out.append(word)
    return out


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_width: float,
    max_lines: int = 2,
) -> list[str]:
    """按宽度换行，最多 max_lines 行，最后一行放不下加省略号。"""
    text = " ".join(str(text or "").split())
    if not text:
        return []

    lines: list[str] = []
    current = ""
    truncated = False
    for token in _tokens(text):
        if token == " ":
            if current:
                current += " "
            continue
        trial = current + token
        if _text_width(draw, trial, font) <= max_width:
            current = trial
            continue

        if current:
            lines.append(current.rstrip())
            current = ""
            if len(lines) >= max_lines:
                truncated = True
                break

        # 单个 token 自己就超宽：逐字硬切
        for char in token:
            if _text_width(draw, current + char, font) <= max_width:
                current += char
                continue
            lines.append(current.rstrip())
            current = char
            if len(lines) >= max_lines:
                truncated = True
                break
        if truncated:
            break

    if len(lines) < max_lines and current:
        lines.append(current.rstrip())
        current = ""
    elif current.strip() and len(lines) >= max_lines:
        truncated = True

    if truncated and lines:
        last = lines[-1]
        while last and _text_width(draw, last + "…", font) > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return [line for line in lines if line]


# ---------------- 数据 ----------------

def _entries(hours: int) -> list[dict]:
    """最近 N 小时新上线、还没开始的活动（给画图用）。"""
    from . import activity_meta

    out: list[dict] = []
    for row in activity_meta.new_activities(hours):
        item = activity_meta.display_row(row)
        if item["title"]:
            out.append(item)
    return out


def _meta_text(item: dict) -> str:
    when = item["start_text"]
    if item.get("end_text"):
        when = f"{when}–{item['end_text']}"
    place = item.get("place") or ""
    return f"{when} · {place}" if place else when


def _tags(item: dict) -> list[tuple[str, tuple[int, int, int], tuple[int, int, int]]]:
    tags = []
    if item.get("labor_counted"):
        hours = float(item.get("labor_hours") or 0)
        label = f"劳动学时 {hours:g}h" if hours else "劳动学时"
        tags.append((label, LABOR_BG, LABOR_FG))
    if item.get("new_era"):
        tags.append(("新时代讲座", NEWERA_BG, NEWERA_FG))
    return tags


# ---------------- 画图 ----------------

def _chip(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    font,
    bg: tuple[int, int, int],
    fg: tuple[int, int, int],
) -> float:
    """画一个小标签，返回右边界 x。"""
    height = 28
    width = int(_text_width(draw, text, font)) + 22
    draw.rounded_rectangle((x, y, x + width, y + height), radius=height // 2, fill=bg)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_y = y + (height - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((x + 11, text_y), text, font=font, fill=fg)
    return x + width


def _centered_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    text: str,
    font,
    fill,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = box[0] + (box[2] - box[0] - width) / 2 - bbox[0]
    y = box[1] + (box[3] - box[1] - height) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=fill)


def _card_height(title_lines: int, has_tags: bool) -> int:
    height = 20 + 30 * max(1, title_lines) + 10 + 24 + 16
    if has_tags:
        height += 10 + 28
    return height


def _draw(items: list[dict], hours: int) -> bytes:
    now = datetime.now()
    title_font = _font(32, True)
    sub_font = _font(16)
    card_title_font = _font(22, True)
    meta_font = _font(16)
    chip_font = _font(15, True)
    badge_font = _font(17, True)
    footer_font = _font(14)

    card_width = WIDTH - PAD * 2
    title_max = card_width - 90            # 序号占 40 + 间距，再留点右边距

    # 先用一张临时图量文字，算好高度再正式画
    probe = ImageDraw.Draw(Image.new("RGB", (WIDTH, 64)))
    prepared = []
    for index, item in enumerate(items[:MAX_ITEMS], 1):
        lines = _wrap(probe, item["title"], card_title_font, title_max, 2)
        if not lines:
            lines = [item["title"][:40]]
        prepared.append(
            {
                "index": index,
                "lines": lines,
                "meta": _truncate(probe, _meta_text(item), meta_font, title_max),
                "tags": _tags(item),
            }
        )

    header_height = 58 + 26
    cards_height = sum(
        _card_height(len(card["lines"]), bool(card["tags"])) + 12 for card in prepared
    )
    more = max(0, len(items) - len(prepared))
    more_height = 24 if more else 0
    footer_height = 46
    total_height = PAD + header_height + cards_height + more_height + footer_height + PAD
    total_height = max(total_height, 300)

    image = Image.new("RGB", (WIDTH, total_height), BG)
    draw = ImageDraw.Draw(image)

    # 标题：新活动 N 个 · 日期
    title = f"新活动 {len(items)} 个 · {now.month}月{now.day}日"
    draw.text((PAD, PAD - 4), title, font=title_font, fill=TEXT)
    subtitle = f"最近 {hours} 小时新上线 · 数据来自第二课堂"
    draw.text((PAD, PAD + 44), subtitle, font=sub_font, fill=SUB)

    y = PAD + header_height
    if not prepared:
        box_height = 150
        draw.rounded_rectangle(
            (PAD, y, PAD + card_width, y + box_height), radius=RADIUS, fill=CARD
        )
        _centered_text(
            draw,
            (PAD, y, PAD + card_width, y + box_height - 24),
            f"最近 {hours} 小时没有新活动",
            _font(20, True),
            SUB,
        )
        _centered_text(
            draw,
            (PAD, y + 36, PAD + card_width, y + box_height),
            "有新的会第一时间推给你",
            _font(15),
            MUTED,
        )
        y += box_height
    else:
        for card in prepared:
            tags = card["tags"]
            height = _card_height(len(card["lines"]), bool(tags))
            card_box = (PAD, y, PAD + card_width, y + height)
            draw.rounded_rectangle(card_box, radius=RADIUS, fill=CARD)

            # 序号徽章
            badge_x, badge_y = PAD + 18, y + 20
            draw.rounded_rectangle(
                (badge_x, badge_y, badge_x + 40, badge_y + 40),
                radius=12,
                fill=ACCENT_SOFT,
            )
            _centered_text(
                draw,
                (badge_x, badge_y, badge_x + 40, badge_y + 40),
                str(card["index"]),
                badge_font,
                ACCENT,
            )

            text_x = PAD + 72
            line_y = y + 18
            for line in card["lines"]:
                draw.text((text_x, line_y), line, font=card_title_font, fill=TEXT)
                line_y += 30

            meta_y = y + 18 + 30 * len(card["lines"]) + 6
            draw.text((text_x, meta_y), card["meta"], font=meta_font, fill=SUB)

            if tags:
                chip_x = text_x
                chip_y = meta_y + 24 + 10
                for label, bg, fg in tags:
                    chip_x = _chip(draw, chip_x, chip_y, label, chip_font, bg, fg) + 8
            y += height + 12

        if more:
            draw.text(
                (PAD + 4, y + 2),
                f"还有 {more} 个新活动没画下，回「活动」看全部",
                font=footer_font,
                fill=MUTED,
            )
            y += more_height

    footer = f"信鸽 · 第二课堂 · {now:%Y-%m-%d %H:%M} 生成"
    footer_width = _text_width(draw, footer, footer_font)
    draw.text(((WIDTH - footer_width) / 2, total_height - PAD - 18), footer, font=footer_font, fill=MUTED)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# ---------------- 缓存 ----------------

def _cache_path(hours: int) -> str:
    stamp = datetime.now().strftime("%Y%m%d%H")
    return os.path.join(CACHE_DIR, f"new-{hours}h-{stamp}.png")


def _cached(hours: int) -> bytes | None:
    path = _cache_path(hours)
    try:
        if time.time() - os.path.getmtime(path) < TTL_SECONDS:
            with open(path, "rb") as handle:
                return handle.read()
    except OSError:
        pass
    return None


def _store(hours: int, data: bytes) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(hours), "wb") as handle:
            handle.write(data)
        # 顺手清掉过期的旧图（只留最近一小时的）
        keep = os.path.basename(_cache_path(hours))
        for name in os.listdir(CACHE_DIR):
            if name != keep and name.startswith("new-"):
                try:
                    os.remove(os.path.join(CACHE_DIR, name))
                except OSError:
                    pass
    except OSError:
        pass


def render(hours: int = 24, use_cache: bool = True) -> bytes:
    """渲染「新活动」图。

    Args:
        hours: 看最近多少小时入库的活动（1-336）。
        use_cache: 10 分钟内复用缓存；测试时传 False 强制重画。

    Returns:
        PNG 字节。
    """
    hours = max(1, min(int(hours or 24), 24 * 14))
    if use_cache:
        hit = _cached(hours)
        if hit:
            return hit
    data = _draw(_entries(hours), hours)
    if use_cache:
        _store(hours, data)
    return data
