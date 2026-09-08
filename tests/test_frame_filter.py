"""図枠（凡例・参照表）を図の中の部材と見分けるテスト。

固定したいのは 2026-09-08 の実害:
  A1ベクターの配管図8枚から冷媒配管の呼び径らしき文字が608件取れたが、
  8ページに76件ずつ・同じ座標＝図枠に刷られたサイズ表だった。そのまま数えれば
  608本の架空計上になる。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff import frame_filter  # noqa: E402
from gopipe_takeoff.models import Drawing, DrawingPage, TextLine  # noqa: E402


def _page(no: int, sheet: str, lines: list[tuple[str, float, float]]) -> DrawingPage:
    return DrawingPage(
        page=no, width=1000, height=700, text=f"図面 {sheet}",
        text_lines=[TextLine(text=t, x=x, y=y, x1=x + 0.02, y1=y + 0.01) for t, x, y in lines],
    )


def _drawing(pages: list[DrawingPage]) -> Drawing:
    return Drawing(source_path="t.pdf", pages=pages)


def test_同じ座標に毎ページ出る文字は図枠と判定する():
    # 図枠の参照表（毎ページ同じ位置）＋ページごとに違う部材ラベル
    pages = [
        _page(i, f"MP-001-0{i}", [
            ("19.1φ", 0.90, 0.50),          # 図枠の冷媒サイズ表
            ("12.7φ", 0.90, 0.52),
            (f"SA {300 + i * 25}×300", 0.2 + i * 0.05, 0.3),  # 図の中の部材
        ])
        for i in range(1, 6)
    ]
    rep = frame_filter.detect(_drawing(pages))
    assert rep.pages_by_kind == {"MP": 5}
    inner, frame = frame_filter.split(_drawing(pages), rep)
    assert [ln.text for ln in inner[1]] == ["SA 325×300"]
    assert sorted(ln.text for ln, _ in frame[1]) == ["12.7φ", "19.1φ"]
    assert "5枚中5枚" in frame[1][0][1]


def test_同じ文字でも座標が違えば部材として残す():
    pages = [
        _page(i, f"M-001-0{i}", [("VD 250×550", 0.1 + i * 0.07, 0.4)])
        for i in range(1, 6)
    ]
    inner, frame = frame_filter.split(_drawing(pages))
    assert all(len(inner[i]) == 1 for i in range(1, 6))
    assert all(not frame[i] for i in range(1, 6))


def test_ページが少ないときは判定しない():
    """1〜2枚では『毎ページ出る』に意味が無い。本物を黙って捨てる方が危ない。"""
    pages = [_page(i, f"M-001-0{i}", [("19.1φ", 0.9, 0.5)]) for i in range(1, 3)]
    rep = frame_filter.detect(_drawing(pages))
    assert len(rep) == 0
    inner, frame = frame_filter.split(_drawing(pages), rep)
    assert len(inner[1]) == 1 and not frame[1]


def test_図面種別が違えば別々に数える():
    """同じPDFでもダクト図と配管図で図枠が違う。混ぜると片方を見逃す。"""
    duct = [_page(i, f"M-001-0{i}", [("凡例OA", 0.8, 0.6)]) for i in range(1, 5)]
    pipe = [_page(4 + i, f"MP-001-0{i}", [("凡例GD", 0.8, 0.6)]) for i in range(1, 5)]
    rep = frame_filter.detect(_drawing(duct + pipe))
    assert rep.pages_by_kind == {"M": 4, "MP": 4}
    assert ("M", "凡例OA", 0.8, 0.6) in rep.keys
    assert ("MP", "凡例GD", 0.8, 0.6) in rep.keys
    # 種別をまたいだ誤判定が起きていないこと
    assert ("M", "凡例GD", 0.8, 0.6) not in rep.keys


def test_除外したものは理由つきで返る():
    """捨てた事実を運べること。0件と『読めていない』を混同させないため。"""
    pages = [_page(i, f"M-001-0{i}", [("参照表", 0.9, 0.5)]) for i in range(1, 5)]
    _, frame = frame_filter.split(_drawing(pages))
    ln, why = frame[1][0]
    assert ln.text == "参照表"
    assert "図枠の共通表と判定" in why and "4枚中4枚" in why
