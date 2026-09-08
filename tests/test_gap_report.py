"""「拾えていないもの」を図面自身から申告するテスト。

固定したい実害(2026-09-08 NEC府中 A1ベクター23枚):
  防火ダンパー・防煙ダンパーは23枚で文字ラベルが1件しかなく、実体は記号だった。
  拾い出し表に出てこないと、現場からは「0個」に見える。無いのではなく
  「この経路では数えられない」だけ、を必ず言葉にする。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff import gap_report, vector_takeoff  # noqa: E402
from gopipe_takeoff.models import Drawing, DrawingPage, TextLine  # noqa: E402


def _page(no: int, text: str, lines: list[tuple[str, float, float]]) -> DrawingPage:
    return DrawingPage(
        page=no, width=1000, height=700, text=text,
        text_lines=[TextLine(text=t, x=x, y=y, x1=x + 0.03, y1=y + 0.008) for t, x, y in lines],
    )


def _items(dw: Drawing):
    return vector_takeoff.extract(dw)[0]


def test_凡例にあるのに図の中に文字が無い記号を申告する():
    pages = [
        _page(i, f"M-001-0{i} ダクト図", [
            ("FD/SD", 0.80, 0.70),                          # 図枠の凡例（毎ページ同じ位置）
            (f"SA {300 + i * 25}×300", 0.2 + i * 0.03, 0.30),  # 図の中の部材
        ])
        for i in range(1, 6)
    ]
    dw = Drawing(source_path="t.pdf", pages=pages)
    gaps = gap_report.find(dw, _items(dw))
    fd = next(g for g in gaps if "FD" in g.item)
    assert "記号で描かれています" in fd.reason
    assert "この表の数量には入っていません" in fd.action
    assert fd.pages == [1, 2, 3, 4, 5]
    # 連記の凡例なら1行にまとめる（同じ宿題を3行に増やさない）
    assert sum(1 for g in gaps if "ダンパー" in g.item) == 1


def test_図の中に文字で在る記号は申告しない():
    pages = [
        # 図の中の部材はページごとに位置が違う（同じ座標だと図枠と判定される）
        _page(i, f"M-001-0{i} ダクト図", [
            ("VD", 0.80, 0.70),
            ("VD 250×550", 0.2 + i * 0.03, 0.30),
            ("VD 150φ", 0.4 + i * 0.03, 0.30),
            ("VD 350φ", 0.5 + i * 0.03, 0.30),
        ])
        for i in range(1, 6)
    ]
    dw = Drawing(source_path="t.pdf", pages=pages)
    assert not any("VD" in g.item for g in gap_report.find(dw, _items(dw)))


def test_延長mは推定せず宿題として出す():
    pages = [_page(i, f"M-001-0{i}", [("SA 400×300", 0.2 + i * 0.05, 0.3)]) for i in range(1, 4)]
    dw = Drawing(source_path="t.pdf", pages=pages)
    g = next(g for g in gap_report.find(dw, _items(dw)) if "延長" in g.item)
    assert "推定値は入れていません" in g.action


def test_複数フロアの共用図は掛ける数を出す():
    """PWC-002-01「衛生設備 2.3.4階 東側トイレ詳細図」＝1枚で3フロア分。"""
    pages = [
        _page(1, "PWC-001-01 衛生設備 1階 東側トイレ詳細図", [("20A", 0.2, 0.3)]),
        _page(2, "PWC-002-01 衛生設備 2.3.4階 東側トイレ詳細図", [("20A", 0.2, 0.3)]),
        _page(3, "PWC-005-01 衛生設備 5階 東側トイレ詳細図", [("20A", 0.2, 0.3)]),
    ]
    dw = Drawing(source_path="t.pdf", pages=pages)
    g = next(g for g in gap_report.find(dw, _items(dw)) if "フロア分" in g.item)
    assert g.pages == [2] and "×3" in g.action


def test_断面図が同居する紙は二重計上を警告し割合を測る():
    pages = [
        _page(i, f"PWC-00{i}-01 A-A'断面図 B-B'断面図", [
            # 平面と断面に同じ表記（位置はページごとに違う＝図枠ではない）
            ("GD 100VP", 0.2 + i * 0.04, 0.3), ("GD 100VP", 0.2 + i * 0.04, 0.6),
        ])
        for i in range(1, 4)
    ]
    dw = Drawing(source_path="t.pdf", pages=pages)
    g = next(g for g in gap_report.find(dw, _items(dw)) if "二重計上" in g.item)
    assert "50%" in g.reason  # 2件中1件が重複
    assert "x・y" in g.action


def test_申告は1行の日本語にできる():
    pages = [_page(i, f"M-001-0{i}", [("SA 400×300", 0.2 + i * 0.05, 0.3)]) for i in range(1, 4)]
    dw = Drawing(source_path="t.pdf", pages=pages)
    line = gap_report.find(dw, _items(dw))[0].line()
    assert "｜" in line and "ページ" in line


def test_分類でカテゴリが塗り替えられても延長の宿題は消えない():
    """🔴 分類(classify)後の項目を渡されても効くこと。

    一度これで「配管の延長が要る」が黙って消えた。拾えていないものの申告が
    呼ぶ順番で変わるのは、いちばん出てはいけない種類の不具合。
    """
    pages = [_page(i, f"MP-001-0{i}", [("GD 100VP", 0.2 + i * 0.05, 0.3)]) for i in range(1, 4)]
    dw = Drawing(source_path="t.pdf", pages=pages)
    items = _items(dw)
    assert items and any("配管" in (i.category or "") for i in items)

    for it in items:            # 辞書による塗り替えを模す
        it.category = "衛生設備"
    gaps = gap_report.find(dw, items)
    assert any("配管の延長" in g.item for g in gaps)
