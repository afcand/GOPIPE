"""図面の線を追跡して「区間（polyline）」にする — 延長mを推定でなく幾何から出すための土台。

なぜ要るか:
  スキャン図には配管の長さを書いた寸法が無い（実測: 資料⑤は数えられる49件を100%拾い、
  測るしかない32件が全部0m）。長さは**経路をなぞって測る**しかない。
  積算担当が定規でやっている作業そのものを、機械にやらせる。

方針:
  1. 色で系統を分ける（color_sample と同じ窓。設備は色、建築は無彩色）
  2. 隙間を閉じる（文字の重なり・破線・かすれで線は途切れる）
  3. 連結成分に分ける（1本の系統ごと）
  4. 細線化して芯線を出す（ダクトは2本の輪郭線で描かれる。芯を取らないと長さが2倍になる）
  5. 芯線を折れ線に落とし、分岐で切る

依存は numpy + PIL のみ（opencv はサーバレスの容量制限で載せられない）。
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger("gopipe.trace")

# 閉じる隙間の大きさ(px)。200dpi で 9px ≒ 1.1mm。
# 文字が線に重なって空く穴と、破線の隙間を埋める。大きくすると別の線と繋がる。
CLOSE_PX = 9
# これ未満の連結成分は文字・記号のかけら。線として扱わない。
MIN_COMPONENT_PX = 400
# 折れ線を間引く許容誤差(px)。曲がりを残しつつ点数を落とす。
SIMPLIFY_PX = 3.0
# これ未満の区間は継手や記号の内部。長さに数えない。
MIN_RUN_PX = 40


def _binary_close(mask, r: int):
    """膨張→収縮で隙間を閉じる（純numpy）。文字の重なりで空いた穴を埋める。"""
    import numpy as np

    def _dilate(m, k):
        out = m.copy()
        for dy in range(-k, k + 1):
            for dx in range(-k, k + 1):
                if dy or dx:
                    out |= np.roll(np.roll(m, dy, 0), dx, 1)
        return out

    def _erode(m, k):
        out = m.copy()
        for dy in range(-k, k + 1):
            for dx in range(-k, k + 1):
                if dy or dx:
                    out &= np.roll(np.roll(m, dy, 0), dx, 1)
        return out

    k = max(1, r // 2)
    return _erode(_dilate(mask, k), k)


def _components(mask, min_px: int = MIN_COMPONENT_PX) -> list:
    """連結成分（8近傍）を大きい順に返す。ON画素だけを見るので疎な図面でも速い。"""
    import numpy as np

    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return []
    idx = {}
    for i, (y, x) in enumerate(zip(ys.tolist(), xs.tolist())):
        idx[(y, x)] = i
    parent = list(range(ys.size))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 走査済みの4方向だけ見れば8連結が取れる
    for (y, x), i in idx.items():
        for dy, dx in ((-1, -1), (-1, 0), (-1, 1), (0, -1)):
            j = idx.get((y + dy, x + dx))
            if j is not None:
                union(i, j)
    groups: dict[int, list] = {}
    for (y, x), i in idx.items():
        groups.setdefault(find(i), []).append((y, x))
    out = [v for v in groups.values() if len(v) >= min_px]
    out.sort(key=len, reverse=True)
    return out


def _thin(mask):
    """Zhang-Suen の細線化。芯線（1px幅）にする。

    🔴ダクトは2本の輪郭線で描かれる。輪郭のまま長さを測ると**2倍**になる。
    塗りつぶしの帯で描かれていても、幅のぶん余計に測る。芯を取るのが先。
    """
    import numpy as np

    img = mask.copy()
    while True:
        changed = False
        for step in (0, 1):
            p = [np.roll(np.roll(img, dy, 0), dx, 1) for dy, dx in
                 ((-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1))]
            n = sum(x.astype(np.uint8) for x in p)
            # 0→1 への変化回数
            seq = p + [p[0]]
            t = sum(((~seq[i]) & seq[i + 1]).astype(np.uint8) for i in range(8))
            if step == 0:
                cond = (p[0] & p[2] & p[4]) | (p[2] & p[4] & p[6])
            else:
                cond = (p[0] & p[2] & p[6]) | (p[0] & p[4] & p[6])
            rm = img & (n >= 2) & (n <= 6) & (t == 1) & ~cond
            if rm.any():
                img &= ~rm
                changed = True
        if not changed:
            break
    return img


def _neighbors(pts: set, y: int, x: int) -> list:
    out = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if (dy or dx) and (y + dy, x + dx) in pts:
                out.append((y + dy, x + dx))
    return out


def _skeleton_to_paths(points: list) -> list[list[tuple[int, int]]]:
    """芯線の画素集合を折れ線の並びにする。分岐と端点で切る。"""
    pts = set(points)
    deg = {p: len(_neighbors(pts, *p)) for p in pts}
    nodes = {p for p, d in deg.items() if d != 2}
    paths: list[list[tuple[int, int]]] = []
    used: set[tuple] = set()

    def walk(start, first):
        path = [start, first]
        used.add((start, first))
        used.add((first, start))
        cur, prev = first, start
        while deg.get(cur, 0) == 2:
            nxt = [q for q in _neighbors(pts, *cur) if q != prev]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            used.add((prev, cur))
            used.add((cur, prev))
            path.append(cur)
        return path

    for nd in nodes:
        for nb in _neighbors(pts, *nd):
            if (nd, nb) in used:
                continue
            # 分岐部は1画素で終わらず数画素の塊になる。塊の内側どうしを結ぶ
            # 1歩の断片を区間として数えると、T字が3本でなく8本に割れる。
            if nb in nodes:
                used.add((nd, nb))
                used.add((nb, nd))
                continue
            paths.append(walk(nd, nb))
    # 分岐も端点も無い閉じた輪（ダクトの周回など）
    for p in pts:
        if deg.get(p) == 2 and not any((p, q) in used for q in _neighbors(pts, *p)):
            nb = _neighbors(pts, *p)
            if nb:
                paths.append(walk(p, nb[0]))
    return [p for p in paths if len(p) >= 2]


def _simplify(path: list, eps: float = SIMPLIFY_PX) -> list:
    """Ramer–Douglas–Peucker。曲がりを残して点を減らす。"""
    if len(path) < 3:
        return path
    (y0, x0), (y1, x1) = path[0], path[-1]
    dy, dx = y1 - y0, x1 - x0
    den = (dy * dy + dx * dx) ** 0.5
    worst, wi = -1.0, 0
    for i in range(1, len(path) - 1):
        y, x = path[i]
        d = (abs(dx * (y0 - y) - dy * (x0 - x)) / den) if den else (
            ((y - y0) ** 2 + (x - x0) ** 2) ** 0.5)
        if d > worst:
            worst, wi = d, i
    if worst <= eps:
        return [path[0], path[-1]]
    return _simplify(path[:wi + 1], eps)[:-1] + _simplify(path[wi:], eps)


def _length_px(path: list) -> float:
    return sum((((path[i + 1][0] - path[i][0]) ** 2 +
                 (path[i + 1][1] - path[i][1]) ** 2) ** 0.5) for i in range(len(path) - 1))


def trace_color(image_bytes: bytes, hue_lo: float, hue_hi: float, *,
                mm_per_px: float | None = None, max_paths: int = 400) -> list[dict]:
    """1つの色の線を追跡して区間の一覧を返す。

    Returns: [{"points": [(x,y)...], "length_px": float, "length_m": float|None}]
    """
    import numpy as np
    from PIL import Image

    from .color_sample import S_MIN, S_MIN_BRIGHT, V_BRIGHT, V_MAX, V_MIN, _hsv

    im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    a = np.asarray(im)
    h, s, v = _hsv(a)
    ink = (v >= V_MIN) & (v <= V_MAX)
    colored = ink & ((s >= S_MIN) | ((s >= S_MIN_BRIGHT) & (v >= V_BRIGHT)))
    hh = h % 360
    band = (hh >= hue_lo) & (hh < hue_hi) if hue_hi <= 360 else ((hh >= hue_lo) | (hh < hue_hi - 360))
    mask = colored & band
    if mask.sum() < MIN_COMPONENT_PX:
        return []
    mask = _binary_close(mask, CLOSE_PX)
    out: list[dict] = []
    for comp in _components(mask):
        sub = np.zeros_like(mask)
        ys = np.array([p[0] for p in comp])
        xs = np.array([p[1] for p in comp])
        sub[ys, xs] = True
        skel = _thin(sub)
        sy, sx = np.nonzero(skel)
        for path in _skeleton_to_paths(list(zip(sy.tolist(), sx.tolist()))):
            sp = _simplify(path)
            lp = _length_px(sp)
            if lp < MIN_RUN_PX:
                continue
            out.append({
                "points": [(int(x), int(y)) for y, x in sp],
                "length_px": round(lp, 1),
                "length_m": round(lp * mm_per_px / 1000, 2) if mm_per_px else None,
            })
            if len(out) >= max_paths:
                return out
    return out
