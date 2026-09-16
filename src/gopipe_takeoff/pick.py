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


def pick_color(page, x: float, y: float, *, scale: float = 1.0, page_no: int = 1) -> PickResult:
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
    length_m = length_pt * mm_per_pt / 1000.0
    area_m2 = area_pt2 * (mm_per_pt / 1000.0) ** 2
    hx = _hex(rgb)
    # 🔴 数だけでは見積に使えない。長さ（と塗りなら面積）も行にして出す。
    items: list[TakeoffItem] = []
    if length_m >= 0.1:
        items.append(TakeoffItem(
            page=page_no, name=f"この色の線の延長 {hx}", spec=f"色 {hx}",
            quantity=round(length_m, 1), unit="m", category=None,
            confidence=0.8, qty_basis="measure", source="pick",
            location=f"同じ色の図形 {n} 個ぶん",
        ))
    if area_m2 >= 0.05:
        items.append(TakeoffItem(
            page=page_no, name=f"この色の塗りの面積 {hx}", spec=f"色 {hx}",
            quantity=round(area_m2, 1), unit="m2", category=None,
            confidence=0.7, qty_basis="measure", source="pick",
            location="外周の四角で測った概算",
        ))
    return PickResult(
        kind="color",
        label=(f"{hx}／{n}個"
               + (f"／{length_m:.1f} m" if length_m >= 0.1 else "")
               + (f"／{area_m2:.1f} m2" if area_m2 >= 0.05 else "")),
        detail={
            "hex": hx, "shapes": n,
            "length_m": round(length_m, 1), "area_m2": round(area_m2, 1),
            "is_fill": d.get("fill") is not None,
        },
        items=items,
        note=("長さは図面の縮尺で換算した値です。面積は塗りの外周の四角で測った概算なので、"
              "斜めの形では多めに出ます"),
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

# --- 配管を指す -------------------------------------------------------------


def _segments(d) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """その図形を、線分の並びに開く（曲線は両端で近似する）。"""
    out = []
    for it in d.get("items", []):
        if it[0] == "l":
            p, q = it[1], it[2]
            out.append(((p.x, p.y), (q.x, q.y)))
        elif it[0] == "c":
            p, q = it[1], it[4]
            out.append(((p.x, p.y), (q.x, q.y)))
        elif it[0] == "re":
            r = it[1]
            cs = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
            out += [(cs[i], cs[(i + 1) % 4]) for i in range(4)]
    return out


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _nearest_label(page, pts, *, limit_pt: float = 26.0) -> str:
    """たどった線のそばに刷られている呼び径などの文字を1つ拾う。

    近いというだけで結び付けない（幅の合わないラベルを採ると数量が化ける）ので、
    **線のすぐ脇にあるものだけ**を採り、無ければ空で返す。人が入れるほうが安全。
    """
    best, best_d = "", limit_pt
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:  # noqa: BLE001
        return ""
    for b in blocks:
        for line in b.get("lines", []):
            t = "".join(sp.get("text", "") for sp in line.get("spans", [])).strip()
            if not t or len(t) > 24:
                continue
            x0, y0, x1, y1 = line.get("bbox") or (0, 0, 0, 0)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            d = min(_dist((cx, cy), q) for q in pts)
            if d < best_d:
                best, best_d = t, d
    return best


def _dedupe_segments(segs, *, grid: float = 0.5):
    """同じ線分が重ねて描かれている分をまとめる。

    CADの図面では、同じ線が複数の図形として重なって入っていることがある。
    そのまま数えると、端点に線が何本も集まっているように見えて、分岐が実際の
    何倍にもなる（実測: 分岐98箇所）。向きを問わず同じ端点の組は1本にする。
    """
    seen = set()
    out = []
    for a, b in segs:
        ka = (round(a[0] / grid), round(a[1] / grid))
        kb = (round(b[0] / grid), round(b[1] / grid))
        if ka == kb:
            continue                      # 長さ0の線は数えない
        key = (ka, kb) if ka <= kb else (kb, ka)
        if key in seen:
            continue
        seen.add(key)
        out.append((a, b))
    return out


def _bends(front, *, tol: float = 1.6) -> dict:
    """たどった線の「曲がり」と「分岐」を数える。

    配管の積算では、延長と同じくらいエルボ（曲がり）が効く。継手は部材として拾うもので、
    長さの中には入っていない。端点が集まっているところを見て、
      ・2本が集まる → その角度で 90度・45度・その他の曲がり（まっすぐは数えない）
      ・3本以上    → 分岐（チーズ）
    と分ける。角度は図面に描かれたとおりで、規格の当てはめはしない（人が直せる形で出す）。
    """
    import math
    from collections import defaultdict


    buckets: dict[tuple[int, int], list] = defaultdict(list)
    for sg in front:
        for i, end in enumerate(sg):
            other = sg[1 - i]
            key = (round(end[0] / tol), round(end[1] / tol))
            buckets[key].append((end, other))

    out = {"bend90": 0, "bend45": 0, "bend_other": 0, "branch": 0}
    for key, arms in buckets.items():
        if len(arms) < 2:
            continue
        # 同じ向きへ伸びる腕は1本として数える（重なった線・継ぎ足しの線を分岐にしない）
        dirs = []
        for p, q in arms:
            vx, vy = q[0] - p[0], q[1] - p[1]
            n = math.hypot(vx, vy)
            if n < 0.5:
                continue
            u = (vx / n, vy / n)
            if not any(u[0] * v[0] + u[1] * v[1] > 0.97 for v in dirs):   # 約14度以内は同じ向き
                dirs.append(u)
        if len(dirs) < 2:
            continue
        if len(dirs) >= 3:
            out["branch"] += 1
            continue
        (p0, q0), (p1, q1) = arms[:2]
        v0 = (q0[0] - p0[0], q0[1] - p0[1])
        v1 = (q1[0] - p1[0], q1[1] - p1[1])
        n0 = math.hypot(*v0)
        n1 = math.hypot(*v1)
        if n0 < 0.5 or n1 < 0.5:
            continue
        cos = max(-1.0, min(1.0, (v0[0] * v1[0] + v0[1] * v1[1]) / (n0 * n1)))
        turn = 180.0 - math.degrees(math.acos(cos))   # まっすぐなら0度
        if turn < 15:
            continue
        if 70 <= turn <= 110:
            out["bend90"] += 1
        elif 30 <= turn < 70:
            out["bend45"] += 1
        else:
            out["bend_other"] += 1
    return out

def pick_pipe(page, x: float, y: float, *, scale: float, page_no: int = 1) -> PickResult:
    """指した配管を、線をたどって延長を出す。

    配管は単線で描かれるのでダクトのように面積と周長では解けない。指した線から
    端点が繋がっている線分を同じ色でたどり、その長さを合計する。
    たどるのを同じ色に限るのは、交差する別系統へ乗り移らないため
    （乗り移ると、1本のつもりが建物中の線を足した数字になる）。
    """
    from .duct_geometry import PT2MM

    d = _under(page, x, y, want=lambda o: o.get("fill") is None and o.get("items"))
    if d is None:
        return PickResult(kind="pipe", label="",
                          note="その場所に線がありません。配管の線の上を指してください")
    rgb = d.get("color")
    # 🔴 色だけでたどると、同じ黒で描かれた図枠の罫線や通り芯へ乗り移る
    # （実測: 指した先が78.6mの直線＝図枠だった）。**線幅でも絞る**。
    # BK平塚で「壁は線幅0.5ptだけ」で見分けたのと同じ手。
    w0 = float(d.get("width") or 0.0)
    segs: list = []
    for o in visible_drawings(page):
        if o.get("fill") is not None:
            continue
        if rgb is not None and not _same(o.get("color"), rgb, tol=0.06):
            continue
        w = float(o.get("width") or 0.0)
        if w0 > 0 and abs(w - w0) > max(0.12, w0 * 0.35):
            continue
        segs += _segments(o)
    if not segs:
        return PickResult(kind="pipe", label="", note="たどれる線がありません")

    # 指した点にいちばん近い線分から、端点が繋がっているものを広げていく
    def mid(s):
        return ((s[0][0] + s[1][0]) / 2, (s[0][1] + s[1][1]) / 2)

    start = min(range(len(segs)), key=lambda i: _dist(mid(segs[i]), (x, y)))
    TOL = 1.6                      # 端点が離れていても、この範囲なら繋がっているとみなす
    used = {start}
    front = [segs[start]]
    ends = [segs[start][0], segs[start][1]]
    changed = True
    while changed and len(used) < 4000:
        changed = False
        for i, sg in enumerate(segs):
            if i in used:
                continue
            if any(_dist(e, sg[0]) <= TOL or _dist(e, sg[1]) <= TOL for e in ends):
                used.add(i)
                front.append(sg)
                ends += [sg[0], sg[1]]
                changed = True

    front = _dedupe_segments(front)
    mm_per_pt = PT2MM * scale
    length_m = sum(_dist(a, b) for a, b in front) * mm_per_pt / 1000.0
    if length_m <= 0:
        return PickResult(kind="pipe", label="", note="長さが出せませんでした")
    label_txt = _nearest_label(page, [p for sg in front for p in sg])
    bend = _bends(front)
    items = [TakeoffItem(
        page=page_no, name="配管（指して実測）", spec=label_txt or None,
        quantity=round(length_m, 1), unit="m", confidence=0.85,
        category="配管", qty_basis="measure", source="pick",
        location=f"{len(front)}区間をたどりました",
    )]
    # 🔴 曲がりは延長の中に入っていない。継手は部材として別に拾うもので、
    # ここを落とすと「長さは合っているのに材料が足りない」見積になる。
    for key, name in (("bend90", "曲がり 90度"), ("bend45", "曲がり 45度"),
                      ("bend_other", "曲がり その他の角度"), ("branch", "分岐")):
        if bend[key]:
            items.append(TakeoffItem(
                page=page_no, name=f"{name}（指して計数）",
                spec=label_txt or None, quantity=float(bend[key]), unit="箇所",
                category="継手・付属", confidence=0.6, qty_basis="count", source="pick",
                location="指した配管の上（弁・器具の記号を含む場合があります）",
            ))
    parts = [f"{length_m:.1f} m"]
    if label_txt:
        parts.append(f"（{label_txt}）")
    turns = bend["bend90"] + bend["bend45"] + bend["bend_other"]
    if turns:
        parts.append(f"／曲がり {turns}箇所")
    if bend["branch"]:
        parts.append(f"／分岐 {bend['branch']}箇所")
    return PickResult(
        kind="pipe",
        label="".join(parts),
        detail={"length_m": round(length_m, 2), "segments": len(front), "label": label_txt,
                "hex": _hex(rgb), **bend},
        items=items,
        note=(("そばの印字から呼び径を採りました。図面と違う場合は直してください。"
               if label_txt else "呼び径は図面から取れませんでした。人が入れてください。")
              + "曲がりと分岐は図面の線から数えた値です。弁や器具の記号が線として"
                "つながっている分を含むことがあるので、多めに出ます（要確認）"),
    )

# --- 範囲の寸法 -------------------------------------------------------------


def measure_area(page, *, scale: float, top: int = 6) -> dict:
    """その紙（＝囲んだ範囲）にある線の長さを、色ごとに合計する。

    囲んだだけでは「何がどれだけあるか」が分からない。印字を拾えない図面でも、
    線の長さは測れる。ただし何の線かは機械には分からないので、色ごとに出して
    人が意味を当てる（色の意味は会社ごとにしか決まらない）。
    """
    from .duct_geometry import PT2MM

    mm_per_pt = PT2MM * scale
    by_color: dict[str, list[float]] = {}
    for d in visible_drawings(page):
        if d.get("fill") is not None and d.get("color") is None:
            continue
        hx = _hex(d.get("color")) or _hex(d.get("fill"))
        if not hx:
            continue
        length = _seg_len_pt(d)
        if length <= 0:
            continue
        cur = by_color.setdefault(hx, [0.0, 0.0])
        cur[0] += length
        cur[1] += 1
    rows = sorted(
        ({"hex": k, "length_m": round(v[0] * mm_per_pt / 1000.0, 1), "shapes": int(v[1])}
         for k, v in by_color.items()),
        key=lambda r: -r["length_m"],
    )
    rows = [r for r in rows if r["length_m"] >= 0.5][:top]
    return {"by_color": rows, "total_m": round(sum(r["length_m"] for r in rows), 1)}
