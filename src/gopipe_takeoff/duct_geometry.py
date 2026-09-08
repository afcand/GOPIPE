"""ベクター図のダクトを幾何から測る — 延長mと展開面積m2を推定でなく測定で出す。

なぜ要るか:
  拾い出しで金額を決めるのは延長と展開面積だが、そこは図面に文字として書かれて
  いない（実測2026-09-08: NEC府中 A1×23枚で、口径や取付高さは99.8%読めたのに
  延長は1件も出せなかった）。長さは経路をなぞって測るしかない。

  スキャン図では画像から線を追う（trace.py）。だがベクター図には、もっと確かな
  手がある。**ダクトは系統ごとの色で塗られた多角形として描かれている**。
  塗りの面積と周長が正確に分かるので、長さは計算で出る。

どう測るか:
  1. 図枠の「用途／記号／使用材料」表から、系統ごとの色を読む
     （🔴 色はAIに聞かない。実測(2026-09-08)では外気=緑・排気=橙で、
       見た目の思い込みと逆だった）
  2. 塗り色が系統色の「白寄せ」であるものを、その系統のダクトとみなす
  3. 多角形の面積 A と周長 P から、長方形として 長辺と短辺を解く
        L, W = P/4 ± sqrt((P/4)^2 - A)
     短辺 W はダクト幅、長辺 L が延長。斜めに置かれていても効く（外接矩形と違い、
     傾きに影響されない）。
  4. 縮尺は図枠の表記を鵜呑みにせず、通り芯の間隔と印字寸法で確かめる
     （🔴 1/50表記が実効1/72.6だった実物がある）

出さないもの:
  ・展開面積は、口径 W×H が印字から取れた区間だけ出す。片方しか分からないときは
    空欄のまま返す。周長を推測で埋めると、そのまま金額になる。
  ・長方形として解けない多角形（エルボ・分岐・変形）は length を返さない。
    数えられないものを数えたことにしない。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .models import TakeoffItem

PT2MM = 25.4 / 72.0
# 塗りが系統色の「白寄せ」かを判定する許容差（0..1 の色の距離）
COLOR_TOL = 0.06
# 白寄せの度合いがこの範囲なら「薄い同色」とみなす（0=白、1=原色）
LIGHT_MIN, LIGHT_MAX = 0.05, 0.95
# これ未満の多角形は継手のかけら・ハッチング。区間として扱わない（紙の上のpt^2）
MIN_AREA_PT2 = 20.0
# 🔴 これ未満の幅はダクトではない。実測(2026-09-08 p1)では、同じ場所に並ぶ
# 幅28mm・長さ1.44mの帯が128本あり、それだけで還気ダクト183.8mになっていた。
# 正体は塗りつぶしのハッチングで、ダクトではない。設備図の最小のダクトでも
# 100mmはあるので、ここで切る。
MIN_WIDTH_MM = 100.0
# これ未満の区間は継手の内部。長さに数えない（m）。
MIN_LENGTH_M = 0.3
# 長方形として解けたとみなす細長さの下限（L/W）。1に近いものは継手や機器の箱。
MIN_ASPECT = 1.2

_DUCT_ROW = re.compile(r"^(外気|給気|外調機給気|還気|排気|パス|排煙)ダクト$")
_AXIS = re.compile(r"^([XY])(\d{1,2})$")
_SCALE_NOTE = re.compile(r"A1[：:]\s*S?[＝=]?\s*1/(\d+)")


@dataclass
class SystemColor:
    """系統（用途）と、図面でその系統に使われている色。"""

    name: str          # 例: 外気ダクト
    rgb: tuple[float, float, float]


# 系統記号 → 用途名（図枠の用途表と、図の中のラベルを突き合わせるため）
SYS_CODE = {
    "OA": "外気ダクト", "SA": "給気ダクト", "SOA": "外調機給気ダクト",
    "RA": "還気ダクト", "EA": "排気ダクト", "PASS": "パスダクト", "SM": "排煙ダクト",
}
_LABEL = re.compile(r"^(SA|RA|OA|EA|SOA|PASS|SM)\s+([\d,]+)\s*(?:([×xX])\s*([\d,]+)|[φΦ])")
# ラベルと区間を結びつけるときに許す幅のずれ
WIDTH_TOL = 0.12


@dataclass
class DuctRun:
    """ダクトの1区間。面積と周長から解いた実寸。"""

    system: str
    length_m: float
    width_mm: float
    plan_area_m2: float
    x: float           # 紙に対する比率
    y: float
    # 図面の印字から結びついた呼び寸法。取れなければ None のまま。
    size_w: int | None = None
    size_h: int | None = None
    round_duct: bool = False

    @property
    def girth_m(self) -> float | None:
        """周長。角ダクトは 2(W+H)、丸ダクトは πD。展開面積の計算に使う。"""
        if self.size_w is None:
            return None
        if self.round_duct:
            return math.pi * self.size_w / 1000.0
        if self.size_h is None:
            return None
        return 2 * (self.size_w + self.size_h) / 1000.0

    @property
    def sheet_area_m2(self) -> float | None:
        """展開面積（板金の面積）。積算担当が拾っているのはこの数字。

        実測(2026-09-08 ①の手書き): 「SA(角) 1100×450 8.2m × 3.1 = 26m²」
        ＝ 周長 2×(1.1+0.45)=3.10m に延長を掛けたもの。
        呼び寸法が図面から取れなかった区間は None を返す（推測で埋めない）。
        """
        g = self.girth_m
        return None if g is None else g * self.length_m


def system_colors(page) -> list[SystemColor]:
    """図枠の用途表から「用途 → 色」を読む。表の版組には依存しない。

    用途名のすぐ右にある色付きの線を、その用途の色として採る。
    """
    drawings = [d for d in page.get_drawings() if d.get("color")]
    out: list[SystemColor] = []
    seen: set[str] = set()
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(sp.get("text", "") for sp in line.get("spans", [])).strip()
            if not _DUCT_ROW.match(text) or text in seen:
                continue
            x0, y0, x1, y1 = line["bbox"]
            cy = (y0 + y1) / 2
            for d in drawings:
                r = d["rect"]
                if x1 < r.x0 < x1 + 90 and abs((r.y0 + r.y1) / 2 - cy) < 5:
                    out.append(SystemColor(text, tuple(round(c, 3) for c in d["color"])))
                    seen.add(text)
                    break
    return out


def _light_of(fill, rgb) -> bool:
    """塗り色が、その系統色を白へ寄せたものかどうか。

    設備図は線を原色、塗りをその薄い色で描く。塗りは原色と白を結ぶ線の上に乗るので、
    「どれくらい白いか」を1つ求めて、他の成分が合うかを見る。
    """
    if fill is None:
        return False
    # 原色と白の差がいちばん大きい成分で、白寄せの度合い t を求める
    diffs = [(abs(1.0 - c), i) for i, c in enumerate(rgb)]
    span, i = max(diffs)
    if span < 0.15:
        return False
    t = (1.0 - fill[i]) / span
    if not (LIGHT_MIN <= t <= LIGHT_MAX):
        return False
    return all(abs(fill[j] - (rgb[j] * t + (1.0 - t))) <= COLOR_TOL for j in range(3))


def _rings(drawing) -> list[list[tuple[float, float]]]:
    """描画物を、閉じた点列（多角形）の集まりにする。"""
    rings: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    for it in drawing.get("items", []):
        op = it[0]
        if op == "re":
            r = it[1]
            rings.append([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)])
            continue
        pts = [(v.x, v.y) for v in it[1:] if hasattr(v, "x")]
        if not pts:
            continue
        if cur and cur[-1] != pts[0]:
            rings.append(cur)
            cur = []
        cur += pts if not cur else pts[1:]
    if cur:
        rings.append(cur)
    return [r for r in rings if len(r) >= 3]


def _area_perimeter(ring) -> tuple[float, float]:
    """多角形の面積（絶対値）と周長。"""
    a = 0.0
    p = 0.0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        a += x0 * y1 - x1 * y0
        p += math.hypot(x1 - x0, y1 - y0)
    return abs(a) / 2.0, p


def solve_rectangle(area: float, perimeter: float) -> tuple[float, float] | None:
    """面積と周長から長方形の長辺・短辺を解く。斜めに置かれていても効く。

    L と W は x^2 - (P/2)x + A = 0 の解。実数解が無い形（エルボ・分岐など）は
    長方形ではないので None を返す＝長さを出さない。
    """
    half = perimeter / 4.0
    disc = half * half - area
    if disc < 0:
        return None
    root = math.sqrt(disc)
    return half + root, half - root


def calibrate_scale(page) -> tuple[float, str]:
    """縮尺を決める。図枠の表記を、通り芯の間隔と印字寸法で確かめる。

    Returns: (縮尺の分母, 根拠の文)
    🔴 図枠の「1/50」を鵜呑みにしない。1/50表記で実効1/72.6という実物がある。
    """
    text = page.get_text()
    m = _SCALE_NOTE.search(text)
    printed = float(m.group(1)) if m else 0.0

    # 通り芯の位置（キープランのものは除く）
    axes: dict[str, dict[int, float]] = {"X": {}, "Y": {}}
    R = page.rect
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            t = "".join(sp.get("text", "") for sp in line.get("spans", [])).strip()
            am = _AXIS.match(t)
            if not am:
                continue
            b = line["bbox"]
            if b[0] / R.width > 0.80 and b[1] / R.height > 0.78:
                continue          # キープラン
            v = (b[0] + b[2]) / 2 if am.group(1) == "X" else (b[1] + b[3]) / 2
            axes[am.group(1)].setdefault(int(am.group(2)), v)

    dims = {
        float(t.replace(",", ""))
        for t in re.findall(r"\b(\d{1,2},\d{3})\b", text)
    }
    if printed and dims:
        for keys in axes.values():
            ks = sorted(keys)
            for a, b in zip(ks, ks[1:]):
                gap_mm = abs(keys[b] - keys[a]) * PT2MM * printed
                if gap_mm < 100:
                    continue
                near = min(dims, key=lambda d: abs(d - gap_mm))
                if abs(near - gap_mm) / near <= 0.01:
                    return printed, (
                        f"図枠の 1/{printed:.0f} を通り芯で確認"
                        f"（{gap_mm:.0f}mm と印字 {near:.0f}mm が一致）"
                    )
    if printed:
        return printed, f"図枠の表記 1/{printed:.0f}（通り芯での確認はできていません）"
    return 50.0, "縮尺の表記が読めず 1/50 と仮定しました（要確認）"


def duct_runs(page, *, scale: float, inside=None) -> list[DuctRun]:
    """塗り色から系統を判定し、多角形ごとに延長と幅を測る。"""
    systems = system_colors(page)
    if not systems:
        return []
    mm_per_pt = PT2MM * scale
    out: list[DuctRun] = []
    seen: set[tuple] = set()
    R = page.rect
    for d in page.get_drawings():
        fill = d.get("fill")
        if fill is None:
            continue
        r = d["rect"]
        if inside is not None and not inside(r.x0, r.y0):
            continue
        sysname = next((s.name for s in systems if _light_of(fill, s.rgb)), None)
        if sysname is None:
            continue
        for ring in _rings(d):
            area_pt2, per_pt = _area_perimeter(ring)
            if area_pt2 < MIN_AREA_PT2:
                continue
            solved = solve_rectangle(area_pt2, per_pt)
            if solved is None:
                continue
            long_pt, short_pt = solved
            if short_pt <= 0 or long_pt / short_pt < MIN_ASPECT:
                continue
            width_mm = short_pt * mm_per_pt
            length_m = long_pt * mm_per_pt / 1000.0
            if width_mm < MIN_WIDTH_MM or length_m < MIN_LENGTH_M:
                continue
            # 同じ多角形が重ねて描かれていることがある。位置と面積で1本に畳む。
            key = (round(ring[0][0], 1), round(ring[0][1], 1), round(area_pt2, 1))
            if key in seen:
                continue
            seen.add(key)
            out.append(DuctRun(
                system=sysname,
                length_m=length_m,
                width_mm=width_mm,
                plan_area_m2=area_pt2 * (mm_per_pt / 1000.0) ** 2,
                x=r.x0 / R.width, y=r.y0 / R.height,
            ))
    return out


def attach_sizes(page, runs: list[DuctRun]) -> None:
    """図面に印字された呼び寸法を、測った区間に結びつける（その場で書き換える）。

    同じ系統で、幅が測った短辺とほぼ一致するラベルのうち、いちばん近いものを採る。
    幅が合わないラベルは採らない。近いというだけで結びつけると、周長が化けて
    展開面積がそのまま金額の誤りになる。
    """
    R = page.rect
    labels: list[tuple[str, int, int | None, bool, float, float]] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            t = "".join(sp.get("text", "") for sp in line.get("spans", [])).strip()
            m = _LABEL.match(t)
            if not m:
                continue
            b = line["bbox"]
            w = int(m.group(2).replace(",", ""))
            h = int(m.group(4).replace(",", "")) if m.group(4) else None
            labels.append((
                SYS_CODE[m.group(1)], w, h, m.group(3) is None,
                (b[0] + b[2]) / 2 / R.width, (b[1] + b[3]) / 2 / R.height,
            ))
    for run in runs:
        best = None
        best_d = 1e9
        for sysname, w, h, is_round, lx, ly in labels:
            if sysname != run.system:
                continue
            # 角ダクトは短辺が W か H のどちらかに出る。丸は直径。
            cands = [w] if is_round or h is None else [w, h]
            if not any(abs(c - run.width_mm) <= c * WIDTH_TOL for c in cands):
                continue
            d = math.hypot(lx - run.x, ly - run.y)
            if d < best_d:
                best, best_d = (w, h, is_round), d
        if best is not None and best_d < 0.12:
            run.size_w, run.size_h, run.round_duct = best


def to_items(runs: list[DuctRun], page_no: int) -> list[TakeoffItem]:
    """測った区間を、口径ごとにまとめて拾い出しの行にする。

    延長は測定値なので qty_basis は measure。展開面積は呼び寸法が取れた分だけ、
    別の行として出す（取れていない分を混ぜると、合計が「一部だけの数字」になる）。
    """
    by_key: dict[tuple, list[DuctRun]] = {}
    for r in runs:
        size = (
            f"{r.size_w}φ" if r.round_duct and r.size_w
            else f"{r.size_w}×{r.size_h}" if r.size_w and r.size_h
            else f"幅{round(r.width_mm / 25) * 25:.0f}（呼び寸法は図面から取れず）"
        )
        by_key.setdefault((r.system, size), []).append(r)

    items: list[TakeoffItem] = []
    for (system, size), group in sorted(by_key.items()):
        length = sum(g.length_m for g in group)
        items.append(TakeoffItem(
            page=page_no, name=system, spec=size,
            quantity=round(length, 1), unit="m", category="ダクト（実測延長）",
            confidence=0.8, source="duct_geometry", qty_basis="measure",
            location=f"{len(group)}区間",
        ))
        sheet = [g.sheet_area_m2 for g in group if g.sheet_area_m2 is not None]
        if sheet:
            items.append(TakeoffItem(
                page=page_no, name=f"{system}（展開面積）", spec=size,
                quantity=round(sum(sheet), 1), unit="m2",
                category="ダクト（実測延長）", confidence=0.8,
                source="duct_geometry", qty_basis="measure",
                location=f"{len(sheet)}/{len(group)}区間",
            ))
    return items


def measure_from_pdf(path: str, drawing, report) -> tuple[list[TakeoffItem], list[str]]:
    """PDF全ページのダクトを測る。図枠の居場所は report から決める。

    Returns: (拾い出しの行, ページごとの縮尺の根拠)
    """
    import fitz

    from .frame_filter import sheet_kind

    items: list[TakeoffItem] = []
    notes: list[str] = []
    by_page = {p.page: p for p in drawing.pages}
    doc = fitz.open(path)
    try:
        for i, page in enumerate(doc, start=1):
            dp = by_page.get(i)
            if dp is None or not dp.text_lines:
                continue
            if not system_colors(page):
                continue                      # ダクト図でなければ何もしない
            kind = sheet_kind(dp)
            w = page.rect.width or 1.0
            h = page.rect.height or 1.0

            def inside(x: float, y: float, _k=kind, _w=w, _h=h) -> bool:
                return not report.in_frame_zone(_k, x / _w, y / _h)

            scale, why = calibrate_scale(page)
            runs = duct_runs(page, scale=scale, inside=inside)
            if not runs:
                continue
            attach_sizes(page, runs)
            items += to_items(runs, i)
            named = sum(1 for r in runs if r.size_w)
            notes.append(
                f"ページ{i}: {why}／{len(runs)}区間 "
                f"{sum(r.length_m for r in runs):.1f}m"
                f"（呼び寸法が図面から結びついたのは {named}/{len(runs)}区間）"
            )
    finally:
        doc.close()
    return items, notes
