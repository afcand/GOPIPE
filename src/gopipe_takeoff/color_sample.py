"""図面の色を機械で測って、拾い出しの行に貼る。

なぜ AI に色を聞かないのか（2026-08-20 実測）:
  プロンプトで色の意味を教えると、区分の書式は100%守られたが
  **中身の正解率は30%・2回実行の再現率は41%** だった。
  一方「色だけ」を聞けば6問中4問当たる＝モデルは色を見えている。
  外れる真因は色覚でなく設計で、拾い出しは同一 name×spec を1行に集約するのに
  工事区分はダクト1本ごとの属性（9径中4径が青と橙の両方に実在）。
  集約した行に単一の区分は原理的に載らない。
  → 色は画素から測る。AI は「それが何か」を読む係に徹する。

しきい値は実験E（2026-08-20・5枚で較正）の実測値:
  有彩ゲート S>=0.20 または (S>=0.12 かつ V>=0.78)、0.20<=V<=0.995
  これより緩いと、黒い印刷線のスキャン色かぶりを色として拾う
  （資料⑤で色画素の44%＝119,500px がそれだった）。
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger("gopipe.color")

# 色相の安全窓（実験Eの実測。濃い線は±8〜18°、淡い塗りは±25〜43°ぶれる）
HUE_WINDOWS: list[tuple[str, float, float]] = [
    ("赤", 340.0, 375.0),      # 340-360 と 0-15 をまたぐ（+360 して判定）
    ("橙茶", 13.0, 45.0),
    ("黄", 45.0, 78.0),
    ("緑", 78.0, 175.0),
    ("水", 175.0, 196.0),
    ("青", 196.0, 230.0),
    ("藤紫", 230.0, 262.0),
    ("紫", 262.0, 290.0),
    ("赤紫", 290.0, 340.0),
]
S_MIN = 0.20
S_MIN_BRIGHT = 0.12
V_BRIGHT = 0.78
V_MIN, V_MAX = 0.20, 0.995
# この割合の画素が同じ色でなければ「色が付いている」と言わない。
# 少数の偽色（JPEGのにじみ）で行に色を貼ると、人が信じてしまう。
MIN_SHARE = 0.18
MIN_PIXELS = 12
# 🔴文字を囲んだ箱で色を貼らないための門。
# 実測(2026-08-20 資料③): AIの bbox は「200φ」等の寸法文字を囲むことがあり、
# **文字の色と、その径の管の本体の色が違う**（文字は青・本体は橙）。
# 文字を測ると系統を取り違える。
# 線は同じ色が連続して長く伸びる（管の描線）。文字は短い画が散る。
# 200dpi では文字の画は概ね15px以下、管の描線は数十px以上続く。
MIN_RUN_PX = 22


def _longest_run(mask) -> int:
    """縦横それぞれで、True が連続する最大の長さ。線か文字かの見分けに使う。"""
    import numpy as np

    best = 0
    for m in (mask, mask.T):
        if m.size == 0:
            continue
        # 各行の連続長を、累積和のリセットで数える
        run = np.zeros(m.shape[0], dtype=np.int32)
        cur = np.zeros(m.shape[0], dtype=np.int32)
        for c in range(m.shape[1]):
            col = m[:, c]
            cur = np.where(col, cur + 1, 0)
            run = np.maximum(run, cur)
        best = max(best, int(run.max()) if run.size else 0)
    return best


def _hsv(arr):
    import numpy as np

    a = arr.astype("float32")
    mx, mn = a.max(-1), a.min(-1)
    d = mx - mn
    v = mx / 255.0
    s = np.where(mx > 0, d / np.maximum(mx, 1), 0)
    h = np.zeros_like(mx)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    nz = d > 0
    dd = np.where(d == 0, 1, d)
    rm = (mx == r) & nz
    gm = (mx == g) & nz & ~rm
    bm = (mx == b) & nz & ~rm & ~gm
    h[rm] = (60 * ((g - b) / dd)[rm]) % 360
    h[gm] = (60 * ((b - r) / dd + 2))[gm]
    h[bm] = (60 * ((r - g) / dd + 4))[bm]
    return h, s, v


def hue_name(hue: float) -> str | None:
    """色相（度）→ 色の名前。窓の外は None（推測で名前を付けない）。"""
    h = hue % 360
    for name, lo, hi in HUE_WINDOWS:
        if lo > 300 and hi > 360:  # 赤: 340-375（0-15 をまたぐ）
            if h >= lo or h < hi - 360:
                return name
        elif lo <= h < hi:
            return name
    return None


def sample_region(page_image: bytes, box: tuple[float, float, float, float]) -> dict | None:
    """ページ画像の指定領域で、いちばん多い有彩色を返す。

    Returns: {"color": "青", "hue": 209.3, "share": 0.42, "pixels": 812} or None
      None = その領域に有彩色がほとんど無い（黒だけ＝色で区別されていない部材）。
      🔴 黒を「その他＝新設」に混ぜない。区分なしとして分ける。
    """
    try:
        import numpy as np
        from PIL import Image

        im = Image.open(io.BytesIO(page_image)).convert("RGB")
        x0, y0, x1, y1 = (max(0, int(v)) for v in box)
        x1, y1 = min(im.width, x1), min(im.height, y1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        a = np.asarray(im.crop((x0, y0, x1, y1)))
        h, s, v = _hsv(a)
        ink = (v >= V_MIN) & (v <= V_MAX)
        colored = ink & ((s >= S_MIN) | ((s >= S_MIN_BRIGHT) & (v >= V_BRIGHT)))
        total_ink = int(ink.sum())
        if colored.sum() < MIN_PIXELS or total_ink <= 0:
            return None
        names: dict[str, list[float]] = {}
        for hv in h[colored].ravel():
            nm = hue_name(float(hv))
            if nm:
                names.setdefault(nm, []).append(float(hv))
        if not names:
            return None
        best = max(names.items(), key=lambda kv: len(kv[1]))
        share = len(best[1]) / max(total_ink, 1)
        if share < MIN_SHARE:
            return None
        # 文字を囲んだ箱を弾く（線なら同じ色が長く続く）
        # 🔴ここで h（色相の配列）を上書きしないこと。窓の変数名を別にする。
        lo, hi = None, None
        for _nm, _lo, _hi in HUE_WINDOWS:
            if _nm == best[0]:
                lo, hi = _lo, _hi
                break
        if lo is not None:
            hh = h % 360
            dom = colored & (
                ((hh >= lo) & (hh < hi)) if hi <= 360 else ((hh >= lo) | (hh < hi - 360))
            )
            run = _longest_run(dom)
            if run < MIN_RUN_PX:
                return None
        vals = np.array(best[1])
        # 赤は 0/360 をまたぐので円環平均で出す
        ang = np.deg2rad(vals)
        hue = float(np.rad2deg(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())) % 360)
        return {"color": best[0], "hue": round(hue, 1),
                "share": round(share, 3), "pixels": len(best[1])}
    except Exception:  # noqa: BLE001  色が測れなくても拾い出しは続ける
        return None


def tile_box_to_page(tile, bbox) -> tuple[float, float, float, float] | None:
    """タイル画像の座標 → ページ画像の座標。

    タイルは重なり込みでページの一部を切って描いたもの。page_rect がその範囲。
    これが無いと「その部材がページのどこにあるか」が分からず色を測れない。
    """
    pr = getattr(tile, "page_rect", None)
    if not pr or len(pr) != 4 or not tile.width or not tile.height:
        return None
    px0, py0, px1, py1 = pr
    sx = (px1 - px0) / tile.width
    sy = (py1 - py0) / tile.height
    return (px0 + bbox.x0 * sx, py0 + bbox.y0 * sy, px0 + bbox.x1 * sx, py0 + bbox.y1 * sy)
