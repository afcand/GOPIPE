"""冷媒配管サイズ表（記号→口径の読み替え表）を図枠から読むテスト。

🔴 この表の呼び径を配管の数量として数えてはいけない。実測(2026-09-08)では
8ページ×76件=608件の架空計上になりかけた。数量に入らないこと自体は
frame_filter 側で止めており、ここでは「参照表として読めること」を固定する。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff import refrigerant, vector_takeoff  # noqa: E402
from gopipe_takeoff.models import Drawing, DrawingPage, TextLine  # noqa: E402

# 左=2列(A〜K) と 右=3列(①〜⑱) が同じ帯に並ぶ、実物と同じ形
_ROWS = [
    (0.500, ["6.4φ", "12.7φ", "9.5φ", "15.9φ", "12.7φ"]),
    (0.508, ["9.5φ", "15.9φ", "9.5φ", "19.1φ", "15.9φ"]),
    (0.516, ["9.5φ", "19.1φ", "9.5φ", "22.2φ", "19.1φ"]),
]


def _pages(n: int = 4) -> list[DrawingPage]:
    out = []
    for p in range(1, n + 1):
        lines = []
        for y, vals in _ROWS:
            for i, v in enumerate(vals):
                lines.append(TextLine(text=v, x=0.86 + i * 0.02, y=y, x1=0.87 + i * 0.02, y1=y + 0.005))
        lines.append(TextLine(text="冷媒配管用被覆銅管", x=0.86, y=0.44, x1=0.92, y1=0.45))
        lines.append(TextLine(text=f"GD {100 + p}VP", x=0.2 + p * 0.05, y=0.3, x1=0.3, y1=0.31))
        out.append(DrawingPage(page=p, width=1000, height=700,
                               text=f"MP-001-0{p} 冷媒配管 図面", text_lines=lines))
    return out


def test_2つ並んだ表を列数で振り分けて読む():
    rows = refrigerant.parse_size_table(Drawing(source_path="t.pdf", pages=_pages()))
    syms = [r.symbol for r in rows]
    assert syms == ["(1)", "(2)", "(3)", "A", "B", "C"]
    assert rows[0].liquid == "9.5φ" and rows[0].gas == "15.9φ" and rows[0].high_low_gas == "12.7φ"
    assert rows[3].liquid == "6.4φ" and rows[3].gas == "12.7φ"
    assert rows[3].high_low_gas == ""   # 2列の表に高低圧ガス管は無い


def test_サイズ表の呼び径は拾い出しの数量に入らない():
    """🔴 これが本丸。表を数えると608件の架空計上になる。"""
    dw = Drawing(source_path="t.pdf", pages=_pages())
    items, _ = vector_takeoff.extract(dw)
    assert not any(any(s in (i.spec or "") for s in ("6.4", "9.5", "12.7", "15.9", "19.1", "22.2"))
                   for i in items)
    assert sum(i.quantity for i in items) == 4   # 各ページの配管1件だけ


def test_冷媒配管の記載が無い図面では何も返さない():
    pages = [
        DrawingPage(page=p, width=1000, height=700, text=f"M-001-0{p} ダクト図",
                    text_lines=[TextLine(text="SA 400×300", x=0.2, y=0.3, x1=0.3, y1=0.31)])
        for p in range(1, 4)
    ]
    assert refrigerant.parse_size_table(Drawing(source_path="t.pdf", pages=pages)) == []
