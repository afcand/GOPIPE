"""図面の1点を指して、そこにあるものだけを拾う。

「1枚を丸ごと」でも「範囲を囲む」でもなく、**人が物を指す**経路。
指した時点で「どれを数えるか」が決まっているので、
拾い過ぎ（通り芯や躯体の壁をダクトと数える）も、拾い漏れも、原理的に起きない。

3つの指し方:
  color  … 線を指す → その色のものを図面全体から集める（色の意味は会社ごとに覚える）
  duct   … ダクトを指す → その1本の延長と幅を測る
  symbol … 記号を指す → 同じ形が図面に何個あるかを数える
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import TakeoffItem
from .pdf_shapes import visible_drawings


@dataclass
class PickResult:
    """指した結果。数字と、その出どころを一緒に返す。"""

    kind: str
    label: str
    detail: dict = field(default_factory=dict)
    items: list[TakeoffItem] = field(default_factory=list)
    note: str = ""


def _hex(rgb) -> str:
    if not rgb:
        return ""
    r, g, b = (int(round(max(0.0, min(1.0, float(v))) * 255)) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _same(a, b, tol: float = 0.08) -> bool:
    if a is None or b is None:
        return False
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a[:3], b[:3]))


def _under(page, x: float, y: float, *, pad: float = 3.0, want=None):
    """指した点にある図形。重なっているときは小さいほうを採る。

    🔴 用途ごとに「欲しい種類」を渡すこと。何でも一番小さいものを返すと、
    ダクトを指しても上に乗っている細い線（ハッチや寸法線）を掴んでしまい、
    「その場所に塗られた図形がありません」になる（実測で踏んだ）。
    """
    hits = []
    for d in visible_drawings(page):
        r = d.get("rect")
        if r is None:
            continue
        if want is not None and not want(d):
            continue
        if r.x0 - pad <= x <= r.x1 + pad and r.y0 - pad <= y <= r.y1 + pad:
            hits.append((max(r.width, 0.1) * max(r.height, 0.1), d))
    if not hits:
        return None
    # 小ささだけで選ぶと、重なっている別の記号を掴むことがある（実測で外れた）。
    # 人は「見えている物の真ん中」を押すので、中心が近いほうを先に採る。
    def near(t):
        r = t[1]["rect"]
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        return ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
    hits.sort(key=lambda t: (round(near(t), 1), t[0]))
    return hits[0][1]


def _seg_len_pt(d) -> float:
    """その図形の線の長さ（pt）。塗りだけの図形は0。"""
    total = 0.0
    for it in d.get("items", []):
        if it[0] == "l":
            p, q = it[1], it[2]
            total += ((p.x - q.x) ** 2 + (p.y - q.y) ** 2) ** 0.5
        elif it[0] == "re":
            r = it[1]
            total += 2 * (r.width + r.height)
    return total


# --- 色を指す ---------------------------------------------------------------


def pick_color(page, x: float, y: float, *, scale: float = 1.0) -> PickResult:
    """指した線の色を採り、同じ色のものを図面全体から集める。

    色の意味（給気/還気/既存/新設…）は会社ごと図面ごとにしか決まらないので、
    ここでは意味を当てない。数と量だけ出して、名前は人に聞く。
    """
    from .duct_geometry import PT2MM

    d = _under(page, x, y)
    if d is None:
        return PickResult(kind="color", label="", note="その場所には線も塗りもありません")
    rgb = d.get("color") or d.get("fill")
    if rgb is None:
        return PickResult(kind="color", label="", note="その図形には色が付いていません")

    mm_per_pt = PT2MM * scale
    n = 0
    length_pt = 0.0
    area_pt2 = 0.0
    for o in visible_drawings(page):
        if _same(o.get("color"), rgb) or _same(o.get("fill"), rgb):
            n += 1
            length_pt += _seg_len_pt(o)
            if o.get("fill") is not None:
                r = o.get("rect")
                if r is not None:
                    area_pt2 += r.width * r.height
    return PickResult(
        kind="color",
        label=_hex(rgb),
        detail={
            "hex": _hex(rgb),
            "shapes": n,
            "length_m": round(length_pt * mm_per_pt / 1000.0, 1),
            "area_m2": round(area_pt2 * (mm_per_pt / 1000.0) ** 2, 1),
            "is_fill": d.get("fill") is not None,
        },
        note=(f"同じ色の図形が {n} 個。線の長さの合計 "
              f"{length_pt * mm_per_pt / 1000.0:.1f} m（図面の縮尺で換算）"),
    )


# --- ダクトを指す -----------------------------------------------------------


def pick_duct(page, x: float, y: float, *, scale: float, page_no: int = 1) -> PickResult:
    """指したダクト1本の、延長と幅を測る。

    ベクター図ではダクトが塗られた多角形なので、面積と周長から長方形として解く
    （斜めでも効く。エルボや分岐は長方形にならないので解かない＝長さを出さない）。
    """
    from .duct_geometry import PT2MM, _area_perimeter, _rings, solve_rectangle

    # 塗られた図形だけを見る（上に乗っているハッチや寸法線を掴まないため）
    d = _under(page, x, y, want=lambda o: o.get("fill") is not None)
    if d is None:
        return PickResult(kind="duct", label="",
                          note="その場所に塗られた図形がありません。ダクトの帯の内側を指してください")
    mm_per_pt = PT2MM * scale
    best = None
    for ring in _rings(d):
        a, per = _area_perimeter(ring)
        solved = solve_rectangle(a, per)
        if solved is None:
            continue
        long_pt, short_pt = solved
        if best is None or long_pt > best[0]:
            best = (long_pt, short_pt, a)
    if best is None:
        return PickResult(kind="duct", label="",
                          note="この形は長方形として解けません（エルボ・分岐は長さを出しません）")
    long_pt, short_pt, a = best
    length_m = long_pt * mm_per_pt / 1000.0
    width_mm = short_pt * mm_per_pt
    girth_m = 2 * (width_mm) / 1000.0     # 幅だけ分かる。高さは図面の呼び寸法から
    item = TakeoffItem(
        page=page_no, name="ダクト（指して実測）", spec=f"幅{width_mm:.0f}mm",
        quantity=round(length_m, 1), unit="m", confidence=0.9,
        qty_basis="dimension", source="pick",
    )
    return PickResult(
        kind="duct", label=f"{length_m:.1f} m × 幅 {width_mm:.0f} mm",
        detail={"length_m": round(length_m, 2), "width_mm": round(width_mm),
                "plan_area_m2": round(a * (mm_per_pt / 1000.0) ** 2, 2),
                "girth_hint_m": round(girth_m, 2)},
        items=[item],
        note="展開面積は高さが要ります。図面の呼び寸法（600×400 など）と突き合わせてください",
    )


# --- 記号を指す -------------------------------------------------------------


def pick_symbol(page, x: float, y: float, *, page_no: int = 1) -> PickResult:
    """指した記号と同じ形が、この図面に何個あるかを数える。

    「凡例と一致するか」ではなく「同じ形が何度も出るか」で数える。
    凡例の見本は模式図で、図の中の実物とは別物のことがある（VDで実証済み）。
    """
    from .legend_symbols import _part_key, _points

    def key_of(dr) -> str:
        ops = []
        for it in dr.get("items", []):
            pts = _points(it)
            if pts:
                ops.append((it[0], pts))
        return _part_key(ops) if ops else ""

    from .legend_symbols import _is_glyph

    # 記号らしい図形（いくつかの線で出来た小さな塊）だけを見る。
    # これを付けないと、記号の上を通る通り芯の線を掴んで「1個」と答える。
    d = _under(page, x, y, want=_is_glyph, pad=6.0) or _under(page, x, y, pad=6.0)
    if d is None:
        return PickResult(kind="symbol", label="", note="その場所には図形がありません")
    k = key_of(d)
    if not k:
        return PickResult(kind="symbol", label="", note="この図形は形を取り出せません")

    R = page.rect
    hits = []
    for o in visible_drawings(page):
        if key_of(o) == k:
            r = o.get("rect")
            if r is not None:
                hits.append((r.x0 / R.width, r.y0 / R.height))
    r0 = d.get("rect")
    size = f"{r0.width:.0f}×{r0.height:.0f}pt" if r0 is not None else ""
    item = TakeoffItem(
        page=page_no, name="指した記号", spec=size, quantity=float(len(hits)),
        unit="個", confidence=0.9, qty_basis="count", source="pick",
    )
    return PickResult(
        kind="symbol", label=f"{len(hits)} 個（{size}）",
        detail={"count": len(hits), "size": size, "positions": hits[:200]},
        items=[item],
        note="名前は図面から引いていません。何の記号かを入れてください",
    )
