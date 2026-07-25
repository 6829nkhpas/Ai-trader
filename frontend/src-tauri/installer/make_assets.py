"""Generate NSIS installer graphics for Strat Ai from the brand icon.

Outputs (BMP3, the format the NSIS MUI expects):
  header.bmp   150x57   — top banner shown on interior wizard pages
  sidebar.bmp  164x314  — welcome/finish page left panel

Run:  python make_assets.py
"""
from PIL import Image, ImageDraw, ImageFont
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "..", "icons", "icon.png")

BG_TOP = (10, 14, 20)      # #0a0e14 terminal background
BG_BOT = (17, 24, 33)      # #111821 slightly lighter for gradient
RED = (255, 49, 49)        # #ff3131 brand red
GREEN = (120, 215, 95)     # brand green
TEXT = (235, 238, 242)
MUTED = (140, 150, 162)


def _font(size, bold=False):
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def _vgrad(w, h):
    img = Image.new("RGB", (w, h), BG_TOP)
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def _logo(size):
    logo = Image.open(ICON).convert("RGBA")
    logo.thumbnail((size, size), Image.LANCZOS)
    return logo


def make_header():
    w, h = 150, 57
    img = _vgrad(w, h)
    logo = _logo(44)
    ly = (h - logo.height) // 2
    img.paste(logo, (8, ly), logo)
    d = ImageDraw.Draw(img)
    d.text((58, 14), "STRAT AI", font=_font(18, bold=True), fill=TEXT)
    d.text((59, 35), "TERMINAL", font=_font(9), fill=RED)
    # thin accent underline
    d.rectangle([0, h - 2, w, h - 1], fill=RED)
    img.save(os.path.join(HERE, "header.bmp"), "BMP")


def make_sidebar():
    w, h = 164, 314
    img = _vgrad(w, h)
    d = ImageDraw.Draw(img)
    # logo centered near top
    logo = _logo(96)
    lx = (w - logo.width) // 2
    img.paste(logo, (lx, 44), logo)
    # wordmark
    f_title = _font(24, bold=True)
    tw = d.textlength("STRAT AI", font=f_title)
    d.text(((w - tw) / 2, 160), "STRAT AI", font=f_title, fill=TEXT)
    # tagline
    f_sub = _font(11)
    sub = "AI Trading Terminal"
    sw = d.textlength(sub, font=f_sub)
    d.text(((w - sw) / 2, 192), sub, font=f_sub, fill=MUTED)
    # accent bar
    d.rectangle([(w - 60) / 2, 214, (w + 60) / 2, 216], fill=GREEN)
    # footer
    f_foot = _font(9)
    foot = "stratai.live"
    fw = d.textlength(foot, font=f_foot)
    d.text(((w - fw) / 2, h - 26), foot, font=f_foot, fill=MUTED)
    img.save(os.path.join(HERE, "sidebar.bmp"), "BMP")


if __name__ == "__main__":
    make_header()
    make_sidebar()
    print("wrote header.bmp (150x57) and sidebar.bmp (164x314)")
