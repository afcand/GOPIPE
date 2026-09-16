"""「指した範囲だけ拾う」の土台。範囲の外が混ざらないことを固定する。

1枚を丸ごと読ませると割り方で数量が変わる（実測: 同じA3スキャンで冷水管が
17m/12m/4.5m/13m）。人が範囲を指す経路は、その食い違いを消すためのもの。
範囲の外が1行でも混ざると、この前提そのものが崩れる。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "shared", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from gopipe_takeoff.region import Region, crop  # noqa: E402

fitz = pytest.importorskip("fitz")


def _sample(tmp_path: Path) -> Path:
    """左上と右下に、それぞれ別の文字を置いた2ページのPDF。

    文字を英数字にするのは、PDFの組み込みフォントに日本語が無く、日本語で書くと
    中身が「··」になって検査にならないため（見本を作る都合であって、実物の図面の
    日本語が読めないという話ではない）。
    """
    doc = fitz.open()
    for tag in ("A", "B"):
        pg = doc.new_page(width=800, height=600)
        pg.insert_text((40, 60), f"TOPLEFT{tag} FD-1")
        pg.insert_text((600, 520), f"BOTRIGHT{tag} VD-2")
    out = tmp_path / "sample.pdf"
    doc.save(out)
    doc.close()
    return out


def test_crop_keeps_only_text_inside(tmp_path):
    src = _sample(tmp_path)
    out = crop(src, Region(page=1, x0=0.0, y0=0.0, x1=0.45, y1=0.45))
    doc = fitz.open(out)
    assert doc.page_count == 1, "範囲を指したら、その1ページだけになる"
    text = doc[0].get_text("text")
    assert "TOPLEFTA" in text
    assert "BOTRIGHTA" not in text, "範囲の外の文字が混ざっている"
    assert "TOPLEFTB" not in text, "別ページが混ざっている"


def test_crop_picks_the_right_page(tmp_path):
    src = _sample(tmp_path)
    out = crop(src, Region(page=2, x0=0.5, y0=0.5, x1=1.0, y1=1.0))
    text = fitz.open(out)[0].get_text("text")
    assert "BOTRIGHTB" in text and "TOPLEFTB" not in text


def test_region_accepts_any_drag_direction():
    r = Region.from_dict({"page": 1, "x0": 0.8, "y0": 0.9, "x1": 0.2, "y1": 0.3})
    assert (r.x0, r.y0, r.x1, r.y1) == (0.2, 0.3, 0.8, 0.9), "右下から左上へ囲んでも同じ範囲"


def test_region_clamps_outside_the_paper():
    r = Region.from_dict({"page": 1, "x0": -0.5, "y0": 0.2, "x1": 1.8, "y1": 0.9})
    assert (r.x0, r.x1) == (0.0, 1.0)


def test_tiny_region_is_refused(tmp_path):
    src = _sample(tmp_path)
    with pytest.raises(ValueError):
        crop(src, Region(page=1, x0=0.5, y0=0.5, x1=0.502, y1=0.9))


def test_whole_page_is_recognized():
    assert Region.from_dict({"page": 1}).is_whole_page
    assert not Region(page=1, x1=0.5).is_whole_page


# --- 範囲を切ったら「線」も切れているか ---------------------------------------


def test_cropped_page_hides_shapes_outside(tmp_path):
    """🔴 文字だけ絞れて線が絞れないと、指した外のダクトまで数える。

    実測(2026-09-16): 右下4分の1を指したのに、ダクトは1枚目まるごとと同じ
    107区間・104.6m を測っていた。page.get_drawings() が cropbox の外も返すため。
    """
    from gopipe_takeoff.pdf_shapes import visible_drawings

    doc = fitz.open()
    pg = doc.new_page(width=800, height=600)
    pg.draw_rect(fitz.Rect(40, 40, 200, 160), color=(1, 0, 0))      # 左上
    pg.draw_rect(fitz.Rect(560, 400, 760, 560), color=(0, 0, 1))    # 右下
    src = tmp_path / "shapes.pdf"
    doc.save(src)
    doc.close()

    whole = fitz.open(src)[0]
    assert len(visible_drawings(whole)) == 2

    out = crop(src, Region(page=1, x0=0.0, y0=0.0, x1=0.45, y1=0.45))
    got = visible_drawings(fitz.open(out)[0])
    assert len(got) == 1, "範囲の外に描かれた図形まで数えている"
