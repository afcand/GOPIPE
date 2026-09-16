#!/usr/bin/env python3
"""拾った場所を色分けして、元の図面の上に描く。

数字の表だけ返しても「どこを拾ったのか」は分からない。逆に、全部同じ色で囲むと
何を何として数えたのかが分からない。**種類ごとに色を分けて、凡例を付ける**のが
人が検算できる最小の形（BK平塚で実測値書き込みPDFを出して往復した型と同じ）。

    .venv/bin/python scripts/mark_colored.py --input 図面.pdf --out 色分け.pdf
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "shared"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import fitz  # noqa: E402

from gopipe_takeoff.classifier import classify  # noqa: E402
from gopipe_takeoff.dictionary import TakeoffDictionary  # noqa: E402
from gopipe_takeoff.duct_geometry import (  # noqa: E402
    _area_perimeter, _light_of, _rings, calibrate_scale, solve_rectangle, system_colors, PT2MM,
)
from gopipe_takeoff.frame_filter import detect as detect_frame  # noqa: E402
from gopipe_takeoff.legend_symbols import count_glyphs  # noqa: E402
from gopipe_takeoff.locale import resolve as kpath  # noqa: E402
from gopipe_takeoff.pdf_loader import load_pdf  # noqa: E402
from gopipe_takeoff.pdf_shapes import visible_drawings  # noqa: E402
from gopipe_takeoff.vector_takeoff import extract as extract_vector  # noqa: E402

# 種類ごとの色。図面の上に描くので、元の線と喧嘩しない濃さにする。
PALETTE: dict[str, tuple[float, float, float]] = {
    "ダクト": (0.00, 0.62, 0.30),
    "ダクト（丸）": (0.00, 0.48, 0.65),
    "ダクト（実測延長）": (0.85, 0.35, 0.00),
    "配管": (0.15, 0.35, 0.85),
    "給水": (0.00, 0.55, 0.75),
    "吹出口・吸込口": (0.90, 0.55, 0.00),
    "弁類": (0.55, 0.20, 0.75),
    "記号カウント": (0.80, 0.10, 0.45),
    "継手・付属": (0.45, 0.45, 0.20),
    "その他": (0.45, 0.50, 0.55),
}
DUCT_COLOR = (0.85, 0.35, 0.00)      # 実測したダクトの帯
GLYPH_COLOR = (0.80, 0.10, 0.45)     # 繰り返し記号


# 辞書に無い種類が出たとき用。名前から決めるので、同じ種類は毎回同じ色になる
# （実行のたびに色が入れ替わると、前回の図面と見比べられない）。
_EXTRA = [
    (0.75, 0.15, 0.15), (0.20, 0.30, 0.70), (0.10, 0.55, 0.45), (0.60, 0.40, 0.05),
    (0.45, 0.15, 0.55), (0.05, 0.45, 0.25), (0.70, 0.25, 0.45), (0.25, 0.50, 0.15),
    (0.55, 0.30, 0.10), (0.15, 0.45, 0.60),
]


def color_of(cat: str | None) -> tuple[float, float, float]:
    key = cat or "その他"
    if key in PALETTE:
        return PALETTE[key]
    h = sum(ord(c) * (i + 1) for i, c in enumerate(key))
    return _EXTRA[h % len(_EXTRA)]


def mark(src: Path, out: Path, *, pages: list[int] | None = None) -> dict:
    drawing = load_pdf(src, render=False)
    frame = detect_frame(drawing)
    vec, _unread = extract_vector(drawing, report=frame)
    items = classify(vec, TakeoffDictionary.from_yaml(kpath("dictionary.yaml")))

    by_page: dict[int, list] = defaultdict(list)
    for it in items:
        if it.bbox is not None:
            by_page[it.page].append(it)

    doc = fitz.open(src)
    tally: dict[str, int] = defaultdict(int)
    duct_total = 0.0

    for n, page in enumerate(doc, start=1):
        if pages and n not in pages:
            continue
        # 印字から拾った行（何として数えたかで色を変える）
        for it in by_page.get(n, []):
            b = it.bbox
            r = fitz.Rect(b.x0 - 1, b.y0 - 1, b.x1 + 1, b.y1 + 1)
            c = color_of(it.category)
            page.draw_rect(r, color=c, width=0.7)
            tally[it.category or "その他"] += 1

        # 実測したダクトの帯（塗り多角形をそのまま囲む）
        scale, _how = calibrate_scale(page)
        mm_per_pt = PT2MM * scale
        systems = system_colors(page)
        if systems:
            for d in visible_drawings(page):
                fill = d.get("fill")
                if fill is None or not any(_light_of(fill, s.rgb) for s in systems):
                    continue
                for ring in _rings(d):
                    a, per = _area_perimeter(ring)
                    solved = solve_rectangle(a, per)
                    if solved is None:
                        continue
                    long_pt, short_pt = solved
                    length_m = long_pt * mm_per_pt / 1000.0
                    width_mm = short_pt * mm_per_pt
                    if width_mm < 100 or length_m < 0.3:
                        continue
                    page.draw_rect(d["rect"], color=DUCT_COLOR, width=1.1)
                    page.insert_text(
                        fitz.Point(d["rect"].x0, d["rect"].y0 - 1.5),
                        f"{length_m:.1f}m", fontsize=4.2, color=DUCT_COLOR,
                    )
                    duct_total += length_m
                    tally["ダクト（実測延長）"] += 1
                    break

        # 繰り返し出てくる記号（同じ形ごとに数えた分）
        for g in count_glyphs(visible_drawings(page)):
            for (gx, gy) in g.positions:
                page.draw_circle(fitz.Point(gx + g.width / 2, gy + g.height / 2),
                                 max(g.width, g.height) / 2 + 1.5,
                                 color=GLYPH_COLOR, width=0.8)
            tally["記号カウント"] += g.count

        _legend(page, tally, duct_total, n)

    if pages:
        doc.select([p - 1 for p in pages])
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    doc.close()
    return {"tally": dict(tally), "duct_m": round(duct_total, 1)}


def _legend(page, tally: dict[str, int], duct_m: float, page_no: int) -> None:
    """凡例。色が何を意味するかを紙の上に置く（別紙にすると必ず離れる）。"""
    x, y = page.rect.x0 + 14, page.rect.y0 + 14
    rows = [(k, v) for k, v in tally.items() if v]
    h = 16 + 11 * (len(rows) + 1)
    page.draw_rect(fitz.Rect(x - 6, y - 10, x + 250, y + h), color=(0.4, 0.45, 0.5),
                   fill=(1, 1, 1), width=0.6, fill_opacity=0.88)
    # 🔴 PDFの標準フォントには日本語が無く、指定しないと凡例が「······」になる
    page.insert_text(fitz.Point(x, y), f"GoPipe が拾った場所（{page_no}ページ）",
                     fontsize=7.5, color=(0.1, 0.2, 0.3), fontname="japan")
    yy = y + 13
    for k, v in rows:
        c = color_of(k)
        page.draw_rect(fitz.Rect(x, yy - 5, x + 8, yy + 1), color=c, fill=c, width=0.4)
        note = f"{k}  {v}" + ("箇所" if k != "ダクト（実測延長）" else f"区間 / 計 {duct_m:.1f}m")
        page.insert_text(fitz.Point(x + 12, yy), note, fontsize=6.6,
                         color=(0.15, 0.2, 0.25), fontname="japan")
        yy += 11
    page.insert_text(fitz.Point(x, yy + 2),
                     "囲み＝印字から拾った行／太枠＝実測したダクト／丸＝繰り返し記号",
                     fontsize=6, color=(0.4, 0.45, 0.5), fontname="japan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages", default="", help="例: 1,2,5（省略で全ページ）")
    a = ap.parse_args()
    pages = [int(v) for v in a.pages.split(",") if v.strip().isdigit()] or None
    res = mark(Path(a.input), Path(a.out), pages=pages)
    print("色分けした図面:", a.out)
    for k, v in sorted(res["tally"].items(), key=lambda t: -t[1]):
        print(f"  {k:<18} {v}")
    print(f"  ダクト実測 合計 {res['duct_m']} m")


if __name__ == "__main__":
    main()
