"""個数モノをテンプレート照合で数え直す層（CV検算）。

なぜ要るか（実測 2026-08-20 ハルキ実図面）:
  同じ図面を2回かけると、LLMが「数えた」数量は 6/11 しか一致しなかった
  （排煙口 1→2、VD 2→1、吹出口 1→2）。数えるのは決定的アルゴリズムの領分で、
  LLM は「その記号が何か」を読む係、CV は「同じ絵が何個あるか」を数える係に分ける。

流れ:
  1. LLM がタイル抽出で返した bbox から記号の見本（テンプレート）を切り出す（extractor 側）
  2. ページ全面を NCC（symbol_match）で照合し、しきい値を動かしても件数が変わらない
     「踊り場」があるときだけ、その個数を機械の数（qty_cv）として記録する
  3. 採用規則:
     - 機械の数 == AIの数 → 一致。独立な2経路が同じ数に到達＝確度を上げる
     - 機械の数 >  AIの数 → 機械を採用（AIの見落とし。旧値は qty_vision に残す）。
       ただし🔴採用は「見本2個以上が同じ数で一致」したときだけ。LLMのbboxは時々
       記号でない場所を囲む（実測: 排煙口のbboxがただのダクトを囲んだ）。毒見本が
       1個で数を書き換えられる設計にすると、そこから嘘の数字が見積へ入る
     - 機械の数 <  AIの数 → 🔴上書きしない。線と重なった記号は NCC が拾えないことが
       実測済み（E2#20 で 9/11）＝機械側の見落としがあり得る。確度を下げて両論併記
  4. 踊り場が無い記号は qty_cv を付けない（数えられなかったものに数字を出さない）
"""
from __future__ import annotations

import io
import logging
import os

from .models import TakeoffItem

logger = logging.getLogger("gopipe.cv_count")

# 1ページで照合する記号種の上限（サーバレスの実行時間を守る）。超えた分は
# ログに残して見送る（黙って諦めない）。
MAX_KEYS_PER_PAGE = 6
MAX_TEMPLATES_PER_KEY = 3
MIN_TEMPLATE_SIDE = 14   # これより小さい bbox は記号でなくノイズ
MAX_TEMPLATE_SIDE = 220  # これより大きい bbox は「領域」であって記号ではない
# ページが大きすぎるときは CV 用に縮小する（FFT のメモリと時間の上限）。
# 🔴縮小は最後の手段: 0.75縮小で E2#20 の recall が 11→10 に落ちる実測がある。
# A3 native(3310px) はこの上限に収まるので縮小されない。
MAX_PAGE_EDGE = 3600

# しきい値帯（実験B + パリティ実測 2026-08-20 で決めた値）:
#   - 既定の0.80-0.65では「線と重なった個体」を永遠に落とす（E2#20が9/11で頭打ち）
#   - 実験B（生の native 画像）では 0.50 まで有効だったが、パイプラインの画像は
#     コントラスト強調＋鮮鋭化＋JPEG を通るためスコア分布が上へずれ、
#     0.50 の段は偽物で溢れる（実測: E2 11→12、排煙口300角 4→16）。帯の低端は 0.55。
#   - 高しきい値側だけの踊り場は「きれいな個体だけ見えている」状態と区別できない
#     （資料④で 0.80/0.75 が 2,2 と偽の踊り場を作った。真値7）
#     → 踊り場は帯の低端の件数と一致するときだけ信じる（下端アンカー）
THRESHOLD_BAND = (0.70, 0.65, 0.60, 0.55)
LOWER_BOUND_THR = 0.55
# 同一記号の作図サイズ違い（300角/350角）は、多スケールでなく
# 「実物から採った複数テンプレート＋中心距離NMSの統合」で吸収する（実験Bの結論5）。
NMS_CENTER_PX = 25
# 1テンプレートのヒットがこの数を超えたら毒テンプレート（ダクト片など反復模様を
# 囲んだ bbox）とみなして棄てる。LLM の bbox は時々記号でない場所を囲む（実測）。
RUNAWAY_HITS = 60


def crop_template(image_bytes: bytes, bbox: tuple[float, float, float, float], *, pad: int = 4) -> bytes | None:
    """タイル画像から記号テンプレートを切り出す。壊れた入力は None（呼び出し側で無視）。"""
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(image_bytes)).convert("L")
        x0, y0, x1, y1 = (int(v) for v in bbox)
        w, h = x1 - x0, y1 - y0
        if not (MIN_TEMPLATE_SIDE <= w <= MAX_TEMPLATE_SIDE and MIN_TEMPLATE_SIDE <= h <= MAX_TEMPLATE_SIDE):
            return None
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(im.width, x1 + pad), min(im.height, y1 + pad)
        if x1 - x0 < MIN_TEMPLATE_SIDE or y1 - y0 < MIN_TEMPLATE_SIDE:
            return None
        out = io.BytesIO()
        im.crop((x0, y0, x1, y1)).save(out, format="PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


def preprocess(gray):
    """照合前の前処理フック。既定は恒等（実験結果が出た変種だけをここへ入れる）。"""
    return gray


def _gray_array(image_bytes: bytes, *, max_edge: int | None = None):
    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(image_bytes)).convert("L")
    shrink = 1.0
    if max_edge and max(im.size) > max_edge:
        shrink = max_edge / max(im.size)
        im = im.resize((int(im.width * shrink), int(im.height * shrink)))
    return np.asarray(im, dtype=np.float64), shrink


def recount(
    page_image: bytes,
    items: list[TakeoffItem],
    templates: dict[tuple, list[bytes]],
    *,
    item_key,
    group_key=None,
    scale_hint: float = 1.0,
    adopt: bool | None = None,
) -> list[str]:
    """個数モノ（単位=個）を全面テンプレート照合で数え直し、items を直接更新する。

    Returns: 人が読めるログ（何を数え、何を採用し、何を見送ったか）。
    """
    try:
        import numpy as np  # noqa: F401
    except Exception:  # noqa: BLE001
        return ["CV検算スキップ: numpy がこの環境に無い"]

    if adopt is None:
        # 🔴既定は「記録と警告のみ」。機械が多いときだけ書き換える規則は、
        # 過積算方向へ偏る非対称バイアスになる（Sol査読 2026-08-20 で指摘・妥当）。
        # 実図面での precision 実測が揃うまで、数の書き換えは環境変数で明示した時だけ。
        adopt = os.environ.get("GOPIPE_CV_ADOPT", "0") == "1"

    # 🔴照合の単位は「同種の記号（name×spec×unit）」であって行ではない。
    # 行は場所ごとに分かれている（役員室1の排煙口2個・役員室2の1個…）のに、
    # 機械はページ全体を数える。行と比べると物差し違いの比較になり、
    # 偶然の一致に確度を上げてしまう（実測で露呈）。必ず同種合計と比べる。
    # 🔴鍵の末尾を落として作らない。鍵の並びが変わると静かに壊れる
    # （実際、集約キーに色を足した日に場所ではなく色を落として壊した）。
    _group_key = group_key or (lambda it: item_key(it)[:-1])

    groups: dict[tuple, list[TakeoffItem]] = {}
    pooled: dict[tuple, list[bytes]] = {}
    for it in items:
        if it.unit != "個":
            continue
        g = _group_key(it)
        if item_key(it) in templates:
            pooled.setdefault(g, []).extend(templates[item_key(it)])
        groups.setdefault(g, []).append(it)
    work = [(g, rows) for g, rows in groups.items() if pooled.get(g)]
    if not work:
        return []
    notes: list[str] = []
    if len(work) > MAX_KEYS_PER_PAGE:
        notes.append(
            f"CV検算: 対象{len(work)}種のうち{MAX_KEYS_PER_PAGE}種のみ照合"
            f"（実行時間の上限。残りは確度順で見送り）"
        )
        work = sorted(work, key=lambda gr: min(r.confidence for r in gr[1]))[:MAX_KEYS_PER_PAGE]

    import numpy as np
    from PIL import Image

    from .symbol_match import find_matches

    page_gray, shrink = _gray_array(page_image, max_edge=MAX_PAGE_EDGE)
    page_gray = preprocess(page_gray)
    # テンプレートはタイル(ほぼ native dpi)から、ページ画像は used_dpi から来る。
    # 倍率が違う環境ではテンプレート側を合わせる。同倍率なら1.0＝実験Bの推奨どおり
    # 単一スケール（多スケールは所要5倍のわりに偽陽性を持ち込むだけだった）。
    factor = scale_hint * shrink
    nms_dist = NMS_CENTER_PX * max(factor, 0.5)

    # 🔴同じ名前で寸法違いの記号（排煙口300角と350角）は、見積上は別行だが
    # 形がほぼ同じで互いのテンプレートに引っかかる（実測: 350角の見本が300角を
    # 4個拾い、350角1個の行へ「拾い漏れの疑い」の誤警報を出した）。
    # → 名前単位でまとめて照合し、各ヒットは最もスコアの高い見本の行へ割り当てる。
    supers: dict[tuple, list[tuple]] = {}
    for g, _rows in work:
        # g = ("spec", spec, unit, ...) | ("catname", cat, name, unit, ...) → 名前で束ねる
        nkey = (_norm_name(_rows[0].name), _rows[0].unit)
        supers.setdefault(nkey, []).append(g)
    gmap = dict(work)

    for nkey, gkeys in supers.items():
        name = gmap[gkeys[0]][0].name
        # (cx, cy, score, 所属グループ) — 所属は「どの行の見本が最も似たか」で決まる
        peaks: list[tuple[float, float, float, tuple]] = []
        used_tpls = 0
        for g in gkeys:
            for tpl_bytes in pooled[g][:MAX_TEMPLATES_PER_KEY]:
                tpl_gray, _ = _gray_array(tpl_bytes)
                tpl_gray = preprocess(tpl_gray)
                if factor != 1.0:
                    im = Image.fromarray(tpl_gray.astype("uint8"))
                    tpl_gray = np.asarray(
                        im.resize((max(4, int(im.width * factor)), max(4, int(im.height * factor)))),
                        dtype=np.float64,
                    )
                th_, tw_ = tpl_gray.shape
                if min(th_, tw_) < MIN_TEMPLATE_SIDE * 0.7:
                    continue
                hits = find_matches(page_gray, tpl_gray, threshold=min(THRESHOLD_BAND))
                if len(hits) > RUNAWAY_HITS:
                    notes.append(
                        f"CV検算: {name} の見本1個が{len(hits)}ヒット＝反復模様を囲んだ毒見本とみなして除外"
                    )
                    continue
                used_tpls += 1
                peaks.extend((m.x + tw_ / 2, m.y + th_ / 2, m.score, g) for m in hits)
        if not used_tpls:
            notes.append(f"CV検算: {name} は使える見本なし＝照合せず")
            continue
        # NMSで1記号=1ヒットに寄せる。残ったヒットの所属＝最良スコアの見本の行
        peaks.sort(key=lambda t: -t[2])
        merged: list[tuple[float, float, float, tuple]] = []
        for cx, cy, sc, g in peaks:
            if all((cx - mx) ** 2 + (cy - my) ** 2 > nms_dist * nms_dist for mx, my, _, _ in merged):
                merged.append((cx, cy, sc, g))

        # 🔴同名で寸法が近い変種（排煙口300角/350角=差16%）は、スコアでの行割り当てが
        # 信用できない（見本の切れ方の綺麗さで勝敗が決まる。実測: 350角の見本が300角を
        # 4個取り、350角の行に qty_cv=4 の嘘が立った）。差が25%未満なら行別の数字は
        # 出さず、名前全体の合計だけを照合する。行別の種別付けは隣の文字（300×300等）を
        # 読めるLLMの領分で、形しか見ないNCCの領分ではない。
        tpl_sides: dict[tuple, float] = {}
        for g in gkeys:
            for tpl_bytes in pooled[g][:1]:
                arr, _ = _gray_array(tpl_bytes)
                tpl_sides[g] = (arr.shape[0] + arr.shape[1]) / 2
        ambiguous = False
        if len(gkeys) > 1 and tpl_sides:
            side_vals = list(tpl_sides.values())
            ambiguous = (max(side_vals) / max(min(side_vals), 1)) < 1.25

        if ambiguous:
            all_rows = [r for g in gkeys for r in gmap[g]]
            counts = {t: sum(1 for _, _, sc, _ in merged if sc >= t) for t in THRESHOLD_BAND}
            vals = list(counts.values())
            _decide_name_total(all_rows, name, counts, vals, notes)
        else:
            for g in gkeys:
                rows = gmap[g]
                counts = {
                    t: sum(1 for _, _, sc, mg in merged if mg == g and sc >= t)
                    for t in THRESHOLD_BAND
                }
                vals = list(counts.values())  # 高→低の順
                _decide(rows, name, counts, vals, notes, adopt, used_tpls)
    return notes


def _decide_name_total(rows, name, counts, vals, notes) -> None:
    """寸法違いの変種が並ぶ同名記号: 名前全体の合計だけ照合し、行別の数字は出さない。"""
    n = None
    for i in range(len(vals) - 1):
        if vals[i] == vals[i + 1] and vals[i] > 0 and vals[i] == vals[-1]:
            n = vals[i]
            break
    total = sum(r.quantity for r in rows)
    lower = counts[LOWER_BOUND_THR]
    if n is not None:
        if abs(n - total) < 1e-9:
            for r in rows:
                r.confidence = max(r.confidence, 0.85)
            notes.append(
                f"CV検算: {name}（寸法違い{len(rows)}行の合計）図面全体{n}個 = AI合計{total:g}個・一致"
            )
        else:
            for r in rows:
                r.confidence = min(r.confidence, 0.6)
            notes.append(
                f"CV検算: {name}（寸法違い{len(rows)}行の合計）AI合計{total:g}個 vs 機械{n}個で不一致"
                f"（行別の内訳は寸法表記の目視で・要確認）"
            )
    elif lower > total and lower <= 5 * max(total, 1) + 10:
        for r in rows:
            r.confidence = min(r.confidence, 0.6)
        notes.append(
            f"CV検算: {name}（寸法違い{len(rows)}行の合計）確実なヒットだけで{lower}個 > "
            f"AI合計{total:g}個＝拾い漏れの疑い（要確認・{counts}）"
        )
    elif lower > total:
        notes.append(
            f"CV検算: {name}（寸法違い{len(rows)}行）は見本がヒットしすぎ（{lower}個）＝照合不能とする"
        )
    else:
        notes.append(f"CV検算: {name}（寸法違い{len(rows)}行）は数えられず（{counts}）")


def _norm_name(s: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKC", s or "").replace(" ", "").replace("　", "").lower()


def _decide(rows, name, counts, vals, notes, adopt, used_tpls) -> None:
    """1つの行グループ（name×spec×unit）の照合結果を確定する。"""
    # 下端アンカー付きの踊り場: 隣接一致 かつ その値が帯の低端の件数と同じときだけ信じる
    n = None
    for i in range(len(vals) - 1):
        if vals[i] == vals[i + 1] and vals[i] > 0 and vals[i] == vals[-1]:
            n = vals[i]
            break
    total = sum(r.quantity for r in rows)
    lower = counts[LOWER_BOUND_THR]
    spec = rows[0].spec or ""
    label = f"{name} {spec}".strip()
    if n is None:
        # 下限警報の正気度: 見本が反復模様（ダクト片など）だと下限が跳ね上がる
        # （実測: 250φダクト片の見本で58ヒット→「拾い漏れの疑い58個」という無意味な警報）。
        # AI合計の5倍+10 を超える下限は見本の質を疑い、警報でなく照合不能として返す。
        sane_cap = 5 * max(total, 1) + 10
        if lower > total and lower <= sane_cap:
            for r in rows:
                r.confidence = min(r.confidence, 0.6)
            notes.append(
                f"CV検算: {label} は踊り場なし。ただし確実なヒットだけで{lower}個 > "
                f"AI合計{total:g}個＝拾い漏れの疑い（要確認・{counts}）"
            )
        elif lower > sane_cap:
            notes.append(
                f"CV検算: {label} は見本がヒットしすぎ（{lower}個）＝見本の質を疑い照合不能とする"
            )
        else:
            notes.append(f"CV検算: {label} は踊り場なし＝数えられず（{counts}）")
        return
    for r in rows:
        r.qty_cv = float(n)
    if abs(n - total) < 1e-9:
        for r in rows:
            r.confidence = max(r.confidence, 0.85)
        notes.append(
            f"CV検算: {label} 図面全体{n}個 = AI合計{total:g}個（{len(rows)}行・一致・確度0.85へ）"
        )
    elif n > total and adopt and used_tpls >= 2 and len(rows) == 1:
        it = rows[0]
        it.qty_vision = it.quantity
        it.quantity = float(n)
        it.source = "cv_count"
        it.confidence = max(it.confidence, 0.8)
        notes.append(f"CV検算: {label} = {n}個を採用（AIは{it.qty_vision:g}個＝見落とし・見本{used_tpls}個）")
    else:
        for r in rows:
            r.confidence = min(r.confidence, 0.6)
        notes.append(
            f"CV検算: {label} AI合計{total:g}個 vs 機械{n}個で不一致（上書きせず要確認・{len(rows)}行）"
        )
