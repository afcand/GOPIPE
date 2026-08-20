"""色の実測（機械で測って行に貼る）の回帰テスト。

AI に色を聞く方式は実測で正解率30%・再現率41%だった（2026-08-20）。
画素から測る方式に切り替えたので、ここでは
「拾い過ぎない（黒を色にしない）」「座標が正しくページへ戻る」を固定する。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

PIL = pytest.importorskip("PIL")
np = pytest.importorskip("numpy")
from PIL import Image  # noqa: E402

from gopipe_takeoff.color_sample import (  # noqa: E402
    hue_name,
    sample_region,
    tile_box_to_page,
)
from gopipe_takeoff.models import BBox, Tile  # noqa: E402


def _png(pixels, size=(60, 60), bg=(255, 255, 255)) -> bytes:
    im = Image.new("RGB", size, bg)
    for (x, y, c) in pixels:
        im.putpixel((x, y), c)
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def _line(color, length=44, width=8, at=(5, 5)):
    """管の描線に近い形（長く続く帯）。色は「線が長く続くか」で文字と見分ける。"""
    return [(at[0] + i, at[1] + j, color) for i in range(length) for j in range(width)]


def _text_like(color, at=(5, 5)):
    """寸法文字に近い形（短い画が散る）。実測でAIのbboxがこれを囲むことがある。"""
    px = []
    for k in range(5):  # 5画ぶん
        x = at[0] + k * 9
        px += [(x, at[1] + j, color) for j in range(11)]      # 縦画 11px
        px += [(x + i, at[1] + 5, color) for i in range(6)]   # 横画 6px
    return px


# --------------------------------------------------------------------------
# 色相 → 名前
# --------------------------------------------------------------------------
def test_hue_names_match_measured_inks():
    """実測した凡例の色（資料③）が正しい名前になること。"""
    assert hue_name(209.3) == "青"    # (既存再利用)
    assert hue_name(99.8) == "緑"     # (移設)
    assert hue_name(27.7) == "橙茶"
    assert hue_name(240.1) == "藤紫"  # 資料④ FS-IP の塗り


def test_red_wraps_around_zero():
    assert hue_name(355.0) == "赤"
    assert hue_name(5.0) == "赤"


# --------------------------------------------------------------------------
# 黒を色にしない
# --------------------------------------------------------------------------
def test_black_ink_is_not_a_color():
    """🔴黒だけの部材に色を貼らない。「その他＝新設」に混ぜると見積が狂う。"""
    png = _png(_line((20, 20, 20)))
    assert sample_region(png, (0, 0, 60, 60)) is None


def test_white_paper_is_not_a_color():
    assert sample_region(_png([]), (0, 0, 60, 60)) is None


def test_strong_blue_is_detected():
    png = _png(_line((31, 84, 135)))  # 資料③の凡例「既存再利用」の実測RGB
    got = sample_region(png, (0, 0, 60, 60))
    assert got and got["color"] == "青"
    assert 195 <= got["hue"] <= 230


def test_pale_fill_is_detected():
    """淡い塗り（資料④の藤色 RGB 174,173,209）も拾えること。"""
    png = _png(_line((174, 173, 209)))
    got = sample_region(png, (0, 0, 60, 60))
    assert got and got["color"] == "藤紫"


def test_few_stray_colored_pixels_do_not_paint_the_row():
    """JPEGのにじみ数点で色を貼らない（人が信じてしまう）。"""
    png = _png(_line((20, 20, 20)) + [(50, 50, (31, 84, 135)), (51, 50, (31, 84, 135))])
    assert sample_region(png, (0, 0, 60, 60)) is None


def test_text_like_strokes_are_rejected():
    """🔴寸法文字を囲んだ箱で色を貼らない。

    実測(資料③): 「200φ」等の寸法文字は青、同じ径の管の本体は橙。
    文字を測ると系統を取り違える。線は色が長く続き、文字は短い画が散る。
    """
    png = _png(_text_like((31, 84, 135)), size=(80, 40))
    assert sample_region(png, (0, 0, 80, 40)) is None


# --------------------------------------------------------------------------
# タイル座標 → ページ座標
# --------------------------------------------------------------------------
def _tile(page_rect, w=100, h=100):
    return Tile(image_png=b"x", row=0, col=0, rows=1, cols=1, width=w, height=h,
                page_rect=page_rect)


def test_tile_box_maps_to_page():
    t = _tile([200.0, 100.0, 300.0, 200.0])  # ページ上の 100x100 の領域
    got = tile_box_to_page(t, BBox(x0=0, y0=0, x1=50, y1=50))
    assert got == (200.0, 100.0, 250.0, 150.0)


def test_tile_box_scales_when_tile_pixels_differ():
    t = _tile([0.0, 0.0, 400.0, 400.0], w=100, h=100)  # 1px = ページ4px
    got = tile_box_to_page(t, BBox(x0=10, y0=10, x1=20, y1=20))
    assert got == (40.0, 40.0, 80.0, 80.0)


def test_tile_without_page_rect_returns_none():
    """位置が分からないタイルでは色を測らない（嘘の位置で測るより出さない）。"""
    t = Tile(image_png=b"x", row=0, col=0, rows=1, cols=1, width=10, height=10)
    assert tile_box_to_page(t, BBox(x0=0, y0=0, x1=5, y1=5)) is None
