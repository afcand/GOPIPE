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
MAX_TEMPLATES_PER_KEY = 2
MIN_TEMPLATE_SIDE = 14   # これより小さい bbox は記号でなくノイズ
MAX_TEMPLATE_SIDE = 220  # これより大きい bbox は「領域」であって記号ではない
# ページが大きすぎるときは CV 用に縮小する（FFT のメモリと時間の上限）
MAX_PAGE_EDGE = 3600


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
    def _group_key(it: TakeoffItem) -> tuple:
        key = item_key(it)
        # item_key = ("spec", spec, unit, loc) | ("catname", cat, name, unit, loc)
        return key[:-1]

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

    page_gray, shrink = _gray_array(page_image, max_edge=MAX_PAGE_EDGE)
    page_gray = preprocess(page_gray)
    base_scales = (0.85, 1.0, 1.15)
    scales = tuple(s * scale_hint * shrink for s in base_scales)

    from .symbol_match import count_stable_fast

    for g, rows in work:
        name = rows[0].name
        plateaus: list[int] = []
        for tpl_bytes in pooled[g][:MAX_TEMPLATES_PER_KEY]:
            tpl_gray, _ = _gray_array(tpl_bytes)
            tpl_gray = preprocess(tpl_gray)
            if min(tpl_gray.shape) < MIN_TEMPLATE_SIDE * min(scales):
                continue
            n, counts = count_stable_fast(page_gray, tpl_gray, scales=scales)
            if n is not None:
                plateaus.append(n)
        if not plateaus:
            notes.append(f"CV検算: {name} は踊り場なし＝数えられず（qty_cv は付けない）")
            continue
        if len(set(plateaus)) > 1:
            # 同じ記号の別テンプレートで数が割れた＝安定していない。数字を出さない。
            notes.append(f"CV検算: {name} はテンプレート間で不一致 {plateaus}＝数えられず")
            continue
        n = plateaus[0]
        total = sum(r.quantity for r in rows)
        for r in rows:
            r.qty_cv = float(n)
        if abs(n - total) < 1e-9:
            for r in rows:
                r.confidence = max(r.confidence, 0.85)
            notes.append(
                f"CV検算: {name} 図面全体{n}個 = AI合計{total:g}個（{len(rows)}行・一致・確度0.85へ）"
            )
        elif n > total and adopt and len(plateaus) >= 2 and len(rows) == 1:
            it = rows[0]
            it.qty_vision = it.quantity
            it.quantity = float(n)
            it.source = "cv_count"
            it.confidence = max(it.confidence, 0.8)
            notes.append(f"CV検算: {name} = {n}個を採用（AIは{it.qty_vision:g}個＝見落とし・見本{len(plateaus)}個一致）")
        else:
            for r in rows:
                r.confidence = min(r.confidence, 0.6)
            notes.append(
                f"CV検算: {name} AI合計{total:g}個 vs 機械{n}個で不一致（上書きせず要確認・{len(rows)}行）"
            )
    return notes
