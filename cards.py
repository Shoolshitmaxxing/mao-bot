"""Ritar kortbilder med Pillow. Inga externa bildfiler behövs, så ingen licensfråga."""
from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from game import SYMBOLS, Card

W, H = 150, 210
STEP = 55  # hur mycket korten överlappar i handen
PER_ROW = 16  # max kort per rad i handbilden

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Raspberry Pi OS / Debian (fonts-dejavu-core)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/seguisym.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]
TABLE_GREEN = (30, 90, 50, 255)


@lru_cache(maxsize=None)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


@lru_cache(maxsize=None)
def _card_image(suit: str, rank: str) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((1, 1, W - 2, H - 2), radius=14, fill="white", outline=(60, 60, 60), width=2)
    color = (200, 30, 30) if suit in "HD" else (20, 20, 20)
    sym = SYMBOLS[suit]

    corner = Image.new("RGBA", (52, 76), (0, 0, 0, 0))
    cd = ImageDraw.Draw(corner)
    cd.text((4, 2), rank, font=_font(30), fill=color)
    cd.text((4, 40), sym, font=_font(30), fill=color)
    img.alpha_composite(corner, (8, 6))
    img.alpha_composite(corner.rotate(180), (W - 8 - 52, H - 6 - 76))

    d.text((W / 2, H / 2 + 6), sym, font=_font(78), fill=color, anchor="mm")
    return img


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_top(card: Card) -> bytes:
    """Bild på översta kortet i högen, på grön bakgrund."""
    big = _card_image(card.suit, card.rank)
    pad = 12
    img = Image.new("RGBA", (W + 2 * pad, H + 2 * pad), TABLE_GREEN)
    img.alpha_composite(big, (pad, pad))
    return _png(img)


def render_hand(hand: list[Card]) -> bytes:
    """Hela handen som en bild, överlappande kort i rader om högst PER_ROW."""
    rows = [hand[i:i + PER_ROW] for i in range(0, len(hand), PER_ROW)] or [[]]
    pad = 12
    width = W + (min(len(hand), PER_ROW) - 1) * STEP + 2 * pad if hand else W
    height = len(rows) * (H + pad) + pad
    img = Image.new("RGBA", (max(width, W + 2 * pad), height), TABLE_GREEN)
    for r, row in enumerate(rows):
        for i, card in enumerate(row):
            img.alpha_composite(_card_image(card.suit, card.rank), (pad + i * STEP, pad + r * (H + pad)))
    return _png(img)
