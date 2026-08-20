"""2Dの経路と取付高さ(FL+)を突き合わせて3Dの区間にする — 立上り・立下りを長さにする。

第1段で「高さが2,850mmにわたって12段あるのに16要素すべて0m」＝垂直区間が
1本も数えられていないことが分かった。第2段で経路をなぞれるようになった。
ここは両者を結ぶ。平面図では点にしか見えない垂直区間が、高さの差として長さになる。

🔴 この層は**推測を含む**。高さの注記は線の近くにあるだけで、どの区間に効くかは
書かれていない。だから:
  - 近くに注記が無い区間には高さを与えない（unassigned として正直に残す）
  - 垂直区間は「端点が近い」かつ「高さが違う」ときだけ立てる
  - 出した数字は必ず内訳（水平/垂直/高さ不明）に割って返す
まとめて1つの数字にすると、推測が実測の顔をして見積へ入る。
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger("gopipe.space3d")

# 高さの注記が、その区間に効くとみなす距離(px)。200dpi・1/50 で 120px ≒ 0.7m。
# 広げると隣の系統の高さを拾う。狭めると高さ不明が増える（そちらは安全側）。
LEVEL_RADIUS_PX = 120.0
# 端点どうしがこの距離(px)以内なら繋がっているとみなす。
JOIN_PX = 28.0
# これ未満の高低差は施工誤差・表記ゆれ。垂直区間として立てない。
MIN_RISER_MM = 300.0
# 高さを繋がりに沿って何区間先まで広げるか。広げすぎると別の高さの系統まで塗り、
# 垂直区間が消える（＝立上り・立下りを見落とす）。
MAX_HOPS = 6


def _pts(run) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in run["points"]]


def assign_levels(runs: list[dict], anchors: list[dict], *,
                  radius_px: float = LEVEL_RADIUS_PX) -> list[dict]:
    """各区間に取付高さを割り当てる。近くに注記が無ければ None のまま残す。

    anchors: [{"x": px, "y": px, "level_mm": float}]
    """
    out = []
    for r in runs:
        best, best_d = None, radius_px
        for a in anchors:
            ax, ay = float(a["x"]), float(a["y"])
            for px, py in _pts(r):
                d = math.hypot(px - ax, py - ay)
                if d < best_d:
                    best, best_d = a, d
        out.append({
            **r,
            "level_mm": (float(best["level_mm"]) if best else None),
            "level_dist_px": (round(best_d, 1) if best else None),
        })
    return out


def _connections(runs: list[dict], join_px: float) -> dict[int, set[int]]:
    """端点が近い区間どうしを繋ぐ（経路のつながり）。"""
    ends = []
    for i, r in enumerate(runs):
        p = _pts(r)
        ends.append((i, p[0]))
        ends.append((i, p[-1]))
    g: dict[int, set[int]] = {i: set() for i in range(len(runs))}
    for a in range(len(ends)):
        ia, pa = ends[a]
        for b in range(a + 1, len(ends)):
            ib, pb = ends[b]
            if ia != ib and math.hypot(pa[0] - pb[0], pa[1] - pb[1]) <= join_px:
                g[ia].add(ib)
                g[ib].add(ia)
    return g


def propagate_levels(runs: list[dict], *, join_px: float = JOIN_PX,
                     max_hops: int = MAX_HOPS) -> list[dict]:
    """高さを、繋がった経路に沿って広げる。

    取付高さの注記は**要所にしか書かれない**（実測: 資料②は396区間に対し注記15点）。
    近さだけで割り当てると8割が高さ不明になる。ダクトは高さを変えるまで同じ高さを
    走るので、注記のある区間から繋がりをたどって広げるのが図面の読み方に合う。

    🔴 広げすぎない（max_hops）。図面の端まで伝わると、別の高さの系統まで
    塗ってしまい、垂直区間が消える。伝播で付けた高さは level_src="propagated" と
    記録し、注記から直接付いたもの（"annotated"）と区別する。
    """
    out = [dict(r) for r in runs]
    for r in out:
        r["level_src"] = "annotated" if r.get("level_mm") is not None else None
    g = _connections(out, join_px)
    frontier = [(i, 0) for i, r in enumerate(out) if r.get("level_mm") is not None]
    while frontier:
        nxt = []
        for i, hop in frontier:
            if hop >= max_hops:
                continue
            for j in g[i]:
                if out[j].get("level_mm") is None:
                    out[j]["level_mm"] = out[i]["level_mm"]
                    out[j]["level_src"] = "propagated"
                    nxt.append((j, hop + 1))
        frontier = nxt
    return out


def find_risers(runs: list[dict], *, join_px: float = JOIN_PX,
                min_mm: float = MIN_RISER_MM) -> list[dict]:
    """高さの違う区間どうしが端点で繋がっている箇所に、垂直区間を立てる。

    平面図では点にしか見えないが、実際には高さの差ぶんの配管・ダクトがある。
    """
    ends = []
    for i, r in enumerate(runs):
        if r.get("level_mm") is None:
            continue
        p = _pts(r)
        ends.append((i, p[0], r["level_mm"]))
        ends.append((i, p[-1], r["level_mm"]))
    risers, seen = [], set()
    for a in range(len(ends)):
        ia, pa, za = ends[a]
        for b in range(a + 1, len(ends)):
            ib, pb, zb = ends[b]
            if ia == ib:
                continue
            drop = abs(za - zb)
            if drop < min_mm:
                continue
            if math.hypot(pa[0] - pb[0], pa[1] - pb[1]) > join_px:
                continue
            key = (min(ia, ib), max(ia, ib), round(drop))
            if key in seen:
                continue
            seen.add(key)
            risers.append({
                "at": [(pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2],
                "from_mm": min(za, zb), "to_mm": max(za, zb),
                "length_m": round(drop / 1000, 2),
                "runs": [ia, ib],
            })
    return _merge_risers(risers, join_px)


def _merge_risers(risers: list[dict], join_px: float) -> list[dict]:
    """同じ場所の立上り・立下りを1本にまとめる。

    🔴 1本の立管には、上でも下でも複数の枝が取り付く。区間の組ごとに数えると
    **同じ1本を何本にも数える**（実測: 資料②で FL+2700→5300 の同じ立下りが47本に化けた）。
    物理的に1本なので、場所と高さの組が同じものは1本に寄せる。
    """
    merged: list[dict] = []
    r2 = (join_px * 3) ** 2
    for rs in sorted(risers, key=lambda x: -x["length_m"]):
        hit = None
        for m in merged:
            if (m["from_mm"], m["to_mm"]) != (rs["from_mm"], rs["to_mm"]):
                continue
            dx = m["at"][0] - rs["at"][0]
            dy = m["at"][1] - rs["at"][1]
            if dx * dx + dy * dy <= r2:
                hit = m
                break
        if hit:
            hit["branches"] = hit.get("branches", 1) + 1
        else:
            merged.append({**rs, "branches": 1})
    return merged


def assemble(runs: list[dict], anchors: list[dict], *, mm_per_px: float,
             radius_px: float = LEVEL_RADIUS_PX, join_px: float = JOIN_PX) -> dict:
    """2Dの経路＋高さ → 3Dの区間と、内訳に割った長さ。

    Returns:
      segments: [{"a":[x,y,z],"b":[x,y,z],"kind":"h"|"v"}]（座標は mm）
      horizontal_m / vertical_m / unassigned_m / total_m
    """
    leveled = assign_levels(runs, anchors, radius_px=radius_px)
    # 注記は要所にしかない。繋がりに沿って広げてから垂直区間を探す。
    leveled = propagate_levels(leveled, join_px=join_px)
    risers = find_risers(leveled, join_px=join_px)

    segments, h_m, v_m, u_m = [], 0.0, 0.0, 0.0
    for r in leveled:
        pts = _pts(r)
        z = r.get("level_mm")
        for i in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            length = math.hypot(x1 - x0, y1 - y0) * mm_per_px / 1000
            if z is None:
                u_m += length
                continue
            h_m += length
            segments.append({
                "a": [x0 * mm_per_px, y0 * mm_per_px, z],
                "b": [x1 * mm_per_px, y1 * mm_per_px, z],
                "kind": "h",
            })
    for rs in risers:
        x, y = rs["at"]
        v_m += rs["length_m"]
        segments.append({
            "a": [x * mm_per_px, y * mm_per_px, rs["from_mm"]],
            "b": [x * mm_per_px, y * mm_per_px, rs["to_mm"]],
            "kind": "v",
        })
    n_un = sum(1 for r in leveled if r.get("level_mm") is None)
    n_prop = sum(1 for r in leveled if r.get("level_src") == "propagated")
    logger.info(
        "3D組立: 区間%d本（高さ不明%d本）／水平%.1fm・垂直%.1fm・高さ不明%.1fm・立上り立下り%d本",
        len(leveled), n_un, h_m, v_m, u_m, len(risers),
    )
    return {
        "segments": segments,
        "risers": risers,
        "runs_total": len(leveled),
        "runs_unleveled": n_un,
        "runs_propagated": n_prop,
        "horizontal_m": round(h_m, 2),
        "vertical_m": round(v_m, 2),
        "unassigned_m": round(u_m, 2),
        "total_m": round(h_m + v_m, 2),
    }
