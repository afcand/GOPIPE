from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from llm_client import LLMClient, LLMMessage, get_llm_client

from .color_sample import sample_region, tile_box_to_page
from .equipment_table import extract_from_text
from .locale import resolve as resolve_knowledge
from .models import BBox, Drawing, DrawingPage, TakeoffItem, Tile

logger = logging.getLogger("gopipe.extractor")

PROMPT_PATH        = Path(__file__).resolve().parents[2] / "prompts" / "extraction.txt"
VERIFY_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "verification.txt"
LEGEND_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "legend.txt"

# Claude の vision 呼び出しで一度に返せる JSON 件数上限を考慮した値
DEFAULT_MAX_TOKENS    = 8192   # v1 は 4096 だったが A1 密度の高い図面で途中切れが発生
VERIFY_MAX_TOKENS     = 4096

# 数量の出所（models.TakeoffItem.qty_basis）。表>計数>寸法計算>推定 の順に確かさが下がる。
_QTY_BASIS = ("table", "count", "measure", "estimate", "none")
# 出所が混ざった行は「一番弱い根拠」で語る。強い方を採ると人が検算をやめる。
_BASIS_DOUBT = {"table": 0, "count": 1, "measure": 2, "estimate": 3, "none": 4}
ESTIMATE_MAX_CONFIDENCE = 0.45

# テキスト層から記号コードを抽出する正規表現 (例: EI2-GR06, GR01, P02, LA04, SOK-A)
_SYMBOL_CODE_RE = re.compile(r"\b([A-Z]{1,4}[0-9]{1,2}[-_]?[A-Z0-9]{0,4})\b")


def _load_prompt(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _format_learned_hint(aliases: dict, *, en: bool = False, limit: int = 300) -> str:
    """学習の堀（過去の修正）を抽出プロンプトに差し込む few-shot ヒント文。

    limit は「プロンプトが長くなりすぎない上限」であって、堀の上限ではない。
    25件で切っていた頃は、26件目以降を教えても永久に効かず、しかも載る25件が
    実行ごとに変わって「昨日は直ったのに今日は戻る」を起こしていた。
    呼び出し側（store.load_learned_aliases）が hits の多い順で渡す。
    """
    lines: list[str] = []
    for raw, info in list((aliases or {}).items())[:limit]:
        info = info or {}
        canon = (info.get("canonical") or "").strip()
        if not canon:
            continue
        disp = info.get("raw", raw)
        cat = info.get("category")
        if en:
            tag = f" ({cat})" if cat else ""
            lines.append(f'- if the drawing shows "{disp}", treat it as "{canon}"{tag}')
        else:
            tag = f"（{cat}）" if cat else ""
            lines.append(f'- 図面に "{disp}" とあれば名称「{canon}」{tag} として扱う')
    if not lines:
        return ""
    header = ("\n\n## Confirmed normalizations for this org (from past corrections — always follow)\n"
              if en else
              "\n\n## この組織で確定済みの正規化（過去の修正＝必ず従う）\n")
    return header + "\n".join(lines)


def _learned_hint() -> str:
    """現在ロケールの学習別名から few-shot ヒントを生成（無ければ空）。"""
    try:
        from .learned import load_aliases
        from .locale import current_locale
        return _format_learned_hint(load_aliases(), en=(current_locale() == "en"))
    except Exception:  # noqa: BLE001
        return ""


def load_suppressions() -> list[dict]:
    """この会社が「要らない」と繰り返し消してきた品目（拾わないことの学習）。"""
    try:
        import os

        from . import store

        if not store.is_enabled():
            return []
        return store.load_suppressions(os.environ.get("GOPIPE_ORG") or "default")
    except Exception:  # noqa: BLE001  学習が引けなくても抽出は続ける
        return []


def _suppression_hint(sups: list[dict]) -> str:
    """「この会社では拾わない」を抽出プロンプトへ差し込む。

    🔴 ここで教えるだけにして、出てきた行を後段で黙って捨てない。
    捨てると、学習が一度でも外れたときに **その品目が二度と表に出なくなる**。
    拾い出しで一番怖いのは間違いより「無かったことになる」こと。
    出てきたら印を付けて人に見せる（_mark_suppressed）。
    """
    if not sups:
        return ""
    lines = []
    for x in sups[:60]:
        spec = f"（例: {', '.join(x['specs'])}）" if x.get("specs") else ""
        lines.append(f"- 「{x['name']}」{spec} … 過去{x['hits']}回・{x['projects']}物件で削除")
    return (
        "\n\n## この会社が「要らない」と消してきた品目（原則として拾わない）\n"
        "下記は、この会社の積算担当が過去に繰り返し消した品目です。**原則として行に起こさないでください。**\n"
        "ただし今回の図面で**明らかに工事対象**（施工範囲を示す色・記号・注記が付いている等）なら、\n"
        "拾ったうえで location の末尾に「※要確認」と書いてください。黙って落とさないこと。\n"
        + "\n".join(lines)
    )


def _mark_suppressed(items: list[TakeoffItem], sups: list[dict]) -> int:
    """学習で「拾わない」とした品目が出てきたら印を付ける（消さない）。

    Returns: 印を付けた行数。
    """
    if not sups:
        return 0
    keys = {_norm(x["name"]) for x in sups}
    n = 0
    for it in items:
        if _norm(it.raw_name or it.name) in keys or _norm(it.name) in keys:
            it.source = "suppressed_hit"
            it.confidence = min(it.confidence, 0.5)
            n += 1
    return n


def _strip_code_fence(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    return m.group(1) if m else text


class ExtractionFailed(RuntimeError):
    """LLM の応答を項目に落とせなかった。「0件」と区別するために投げる。

    黙って [] を返すと、読めていないページが「その図面には何も無かった」に化ける。
    拾い出しで一番怖いのは間違いより「無かったことになる」ことなので、必ず表に出す。
    """


def _parse_response(raw: str, *, page_number: int, drop_bbox: bool = False) -> list[TakeoffItem]:
    """LLM レスポンスの JSON を TakeoffItem のリストにする。"""
    if not raw:
        raise ExtractionFailed(f"{page_number}ページ: AIの応答が空でした")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        tail = raw.strip()[-80:]
        raise ExtractionFailed(
            f"{page_number}ページ: AIの応答を読み取れませんでした"
            f"（途中で切れた可能性があります。末尾: …{tail}）"
        ) from None
    if not isinstance(data, list):
        raise ExtractionFailed(f"{page_number}ページ: AIの応答の形式が想定と違いました")
    items: list[TakeoffItem] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            bbox_val = None if drop_bbox else row.get("bbox")
            qty = float(row.get("quantity", 0) or 0)
            basis = (row.get("qty_basis") or "").strip().lower() or None
            if basis not in _QTY_BASIS:
                basis = None
            if qty == 0 and basis is None:
                basis = "none"
            conf = float(row.get("confidence", 1.0) or 1.0)
            # 推定値に高い確度を持たせない。実測(2026-08-19)では推定の数量は
            # 同じ図面を2回かけると動いた（25.0m→20.0m）。0.9 と並べて出すと
            # 人が検算すべき行が「🟢そのままでOK」に化ける。
            if basis == "estimate":
                conf = min(conf, ESTIMATE_MAX_CONFIDENCE)
            items.append(
                TakeoffItem(
                    page=row.get("page", page_number),
                    name=row.get("name", ""),
                    spec=row.get("spec"),
                    quantity=qty,
                    unit=row.get("unit", ""),
                    location=row.get("location"),
                    bbox=BBox.from_list(bbox_val) if bbox_val else None,
                    confidence=conf,
                    qty_basis=basis,
                )
            )
        except Exception:
            continue
    return items


def _call_llm_for_image(
    client: LLMClient,
    system_prompt: str,
    *,
    image_png: bytes | None,
    user_text: str,
    page_number: int,
    drop_bbox: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[TakeoffItem]:
    """1 画像 + ユーザーテキストで LLM を呼んで TakeoffItem のリストを返す。"""
    msgs = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(
            role="user",
            content=user_text,
            images=[image_png] if image_png else [],
        ),
    ]
    # LLM 側の一時障害（500/529/429）は珍しくない。ここで素通しすると
    # 「3分待った末に英語のエラー」になり、現場は理由が分からないまま手作業へ戻る。
    # 数回だけ待って試し、それでも駄目ならページ単位の失敗として日本語で伝える。
    last: Exception | None = None
    for attempt in range(3):
        try:
            resp = client.complete(msgs, max_tokens=max_tokens, temperature=0.0)
            raw = _strip_code_fence(resp.text).strip()
            return _parse_response(raw, page_number=page_number, drop_bbox=drop_bbox)
        except ExtractionFailed:
            raise
        except Exception as e:  # noqa: BLE001  LLM SDK の例外型に依存しない
            last = e
            logger.warning(
                "page %d: AI呼び出しに失敗 (%d回目): %s", page_number, attempt + 1, e
            )
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise ExtractionFailed(
        f"{page_number}ページ: AIの呼び出しに繰り返し失敗しました"
        f"（{type(last).__name__}）。時間をおいて実行し直してください。"
    )


def _norm(s: str | None) -> str:
    if not s:
        return ""
    return unicodedata.normalize("NFKC", s).replace(" ", "").replace("　", "").lower()


def _item_key(it: TakeoffItem) -> tuple:
    """同一部材とみなす鍵。

    単位を鍵に含めるのが要点。含めないと「ダクト400×400 を m で見た行」と
    「同じものを m2 で見た行」が同じ鍵になり、20m と 8m2 を足す/選ぶという
    意味の無い演算が起きる。単位が食い違うなら、それは人に見せるべき不一致。
    """
    loc = _norm(it.location)
    unit = _norm(it.unit)
    if it.spec and it.spec.strip():
        return ("spec", _norm(it.spec), unit, loc)
    return ("catname", _norm(it.category), _norm(it.name), unit, loc)


def _dedupe_items(items: list[TakeoffItem]) -> list[TakeoffItem]:
    """**同じものが二重に来た**ときに 1 行へ寄せる（数量は合算しない）。

    使う場面は「同じ絵をもう一度読んだ」結果を混ぜるとき（Pass1 と Pass2 など）。
    同じ絵を2回読んで足したら倍になるので、confidence が高い方を残す。
    タイル間は別物なので合算する（_merge_tile_items）。混同しないこと。
    """
    by_key: dict[tuple, TakeoffItem] = {}
    order: list[tuple] = []
    for it in items:
        key = _item_key(it)
        cur = by_key.get(key)
        if cur is None:
            by_key[key] = it
            order.append(key)
            continue
        if it.confidence > cur.confidence:
            by_key[key] = it
    return [by_key[k] for k in order]


def _merge_tile_items(items: list[TakeoffItem]) -> list[TakeoffItem]:
    """**別々のタイル**から来た項目を 1 ページ分にまとめる（数量は合算する）。

    タイルは重なり部分を薄くして「担当領域にあるものだけ出す」ようにしてあるので、
    別タイルの同一キーは *同じ部材が二重に来た* のではなく *別の場所にある同じ種類*
    ＝足すのが正しい。
    🔴 ここを keep-one にしていたのが、実測で見えていた過小計上の正体だった:
       弁 3個(タイルA) + 2個(タイルB) → 3個、配管 12m + 16.5m → 12m。
    確度は合算した中の **最小** を採る。合計は一番弱い根拠と同じだけしか信用できない。
    """
    by_key: dict[tuple, TakeoffItem] = {}
    order: list[tuple] = []
    n_src: dict[tuple, int] = {}
    for it in items:
        key = _item_key(it)
        cur = by_key.get(key)
        if cur is None:
            by_key[key] = it
            order.append(key)
            n_src[key] = 1
            continue
        cur.quantity += it.quantity
        cur.confidence = min(cur.confidence, it.confidence)
        if cur.bbox is None and it.bbox is not None:
            cur.bbox = it.bbox
        if _BASIS_DOUBT.get(it.qty_basis or "none", 4) > _BASIS_DOUBT.get(cur.qty_basis or "none", 4):
            cur.qty_basis = it.qty_basis
        if cur.color is None and it.color is not None:
            cur.color, cur.color_hue = it.color, it.color_hue
        n_src[key] += 1
    for key, n in n_src.items():
        if n > 1:
            logger.info(
                "タイル合算: %s → %s %s（%dタイル分）",
                by_key[key].name, by_key[key].quantity, by_key[key].unit, n,
            )
    return [by_key[k] for k in order]


def _qty_close(a: float, b: float, tol: float = 0.05) -> bool:
    hi = max(abs(a), abs(b))
    return True if hi == 0 else abs(a - b) / hi <= tol


def _spec_match(a: str | None, b: str | None) -> bool:
    """型番/口径の一致。完全一致 or 3文字以上の包含（'DN20'⊂'GV DN20' 等）。"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return len(min(na, nb, key=len)) >= 3 and (na in nb or nb in na)


def _find_table_match(items: list[TakeoffItem], ti: TakeoffItem) -> TakeoffItem | None:
    """機器表行 ti に対応する vision 抽出項目を探す（spec 一致優先、無ければ name 一致）。"""
    if ti.spec and _norm(ti.spec):
        for it in items:
            if _spec_match(it.spec, ti.spec):
                return it
    tn = _norm(ti.name)
    if tn:
        for it in items:
            inm = _norm(it.name)
            if inm and (inm == tn or (len(inm) >= 3 and inm in tn)):
                return it
    return None


def reconcile_with_text_table(
    vision_items: list[TakeoffItem], page_text: str, *, page: int = 1,
) -> list[TakeoffItem]:
    """テキスト層の機器表（確定情報）で vision 抽出を補正・補完する。

    ベクター(CAD)PDF はテキスト層に台数・型番・口径が「文字」で入っているため、
    画像認識より正確。本処理は:
      - 機器表に対応する vision 項目があれば数量・型番を機器表優先で採用し、
        信頼度を引き上げる（数量ズレは警告ログ）。
      - vision に無い機器表項目は「拾い漏れ」として追加する。
      - テキスト層が無い（画像 PDF）場合は vision をそのまま返す（フォールバック）。
    """
    table_items = extract_from_text(page_text or "", page=page)
    if not table_items:
        return vision_items

    result = list(vision_items)
    matched = mismatched = added = 0
    for ti in table_items:
        m = _find_table_match(result, ti)
        if m is None:
            ti.source = "text_table"
            result.append(ti)
            added += 1
            continue
        matched += 1
        disagreed = not _qty_close(m.quantity, ti.quantity)
        if disagreed:
            logger.warning(
                "page %d: 数量ズレ '%s' vision=%s 機器表=%s → 機器表を採用（図面側の読みは残す）",
                page, m.name, m.quantity, ti.quantity,
            )
            mismatched += 1
            # 図面側の読みを捨てない。捨てると「最も検算すべき行」が
            # 🟢そのままでOK に化けて、人が確認する機会そのものが消える。
            m.qty_vision = m.quantity
        m.quantity = ti.quantity
        if ti.spec and not (m.spec and m.spec.strip()):
            m.spec = ti.spec
        # 一致したときだけ「機器表で裏が取れた」として信頼度を上げる。
        # 食い違った行を 0.95 にするのは、人に嘘の安心を渡すことになる。
        m.confidence = max(m.confidence, 0.95) if not disagreed else min(m.confidence, 0.6)
        m.source = "reconciled"
    logger.info(
        "page %d: 機器表突合 matched=%d (ズレ%d) added=%d", page, matched, mismatched, added
    )
    return result


def _extract_symbol_codes(text: str) -> list[str]:
    """テキスト層から図面記号コードを抽出する（例: EI2-GR06, GR01, SOK-A 等）。"""
    if not text:
        return []
    codes = sorted(set(_SYMBOL_CODE_RE.findall(text)))
    # 一般的な英単語 (THE, AND, NOT 等) はフィルタ
    _stop = {"AND", "NOT", "THE", "FOR", "ALL", "NEW", "OLD", "TOP", "PER", "ROW", "COL"}
    return [c for c in codes if c not in _stop and len(c) >= 2]


def _tile_user_text(page: DrawingPage, tile: Tile, symbol_codes: list[str]) -> str:
    """タイル抽出時のユーザーテキスト。担当領域と記号コードを明示する。"""
    codes_hint = ""
    if symbol_codes:
        codes_hint = (
            f"\nテキスト層で検出した記号コード（これらを図面上で探してください）: "
            f"{', '.join(symbol_codes[:40])}"
        )
    faded = tile.core != [0.0, 0.0, 1.0, 1.0]
    core_rule = (
        "### この画像の見方（重要）\n"
        "画像の外周は**うすく**なっています。うすい帯は隣の区画との重なりで、"
        "**前後のつながりを見るためだけ**に写しています。\n"
        "🔴 **はっきり見えている中心部分にあるものだけ**を行にしてください。"
        "うすい帯にしか無いものは出さないでください"
        "（隣の区画が担当します。両方が出すと同じものを二重に数えます）。\n"
        "中心部分から出て隣へ続く配管・ダクトは、**この画像の中に見えている分の長さだけ**を"
        "数量にしてください（続きは隣の区画が足します）。\n"
        if faded else
        "この区画に写っているものだけを行にしてください。"
        "隣の区画にあるものは出さないでください（二重に数えるため）。\n"
    )
    return (
        f"## ページ {page.page} — {tile.n_rows}行×{tile.n_cols}列 に分けたうちの "
        f"{tile.row + 1}行目 {tile.col + 1}列目 (row={tile.row}, col={tile.col})\n"
        f"{core_rule}"
        f"この区画から拾い出し項目を JSON 配列で返してください。\n"
        f"🔴**すべての行に bbox を入れてください**: [x0,y0,x1,y1]（この画像のピクセル座標）。\n"
        f"  ・単位が「個」の行 … その記号の**代表1個だけ**をぴったり囲む（複数個をまとめて囲まない）\n"
        f"  ・配管・ダクトの行 … その管が**最もはっきり描かれている区間を1つ**、線の太さぶんだけ囲む\n"
        f"    （系統全体を囲まない。囲みが大きいと他の色が混ざって系統の判定が狂います）\n"
        f"    🔴**寸法の文字・引出線・注記を囲まない。**「200φ」「FL+3,065」等の文字ではなく、\n"
        f"    **描かれている管そのものの線**を囲んでください。文字と管は色が違うことがあり、\n"
        f"    文字を囲むと系統を取り違えます（実測: 寸法文字は青、同じ径の管の本体は橙）。\n"
        f"  ・機器・器具の行 … その機器の外形を囲む\n"
        f"bbox は2つに使います: 同じ絵を機械が数え直すための見本と、"
        f"**その部材が何色で描かれているか**の実測（設備図は色で既存再利用・移設・新設を分けるため）。\n"
        f"テキスト層 (ページ全体): {(page.text or '(なし)')[:2000]}"
        f"{codes_hint}"
    )


def _whole_page_user_text(page: DrawingPage, symbol_codes: list[str]) -> str:
    codes_hint = ""
    if symbol_codes:
        codes_hint = (
            f"\n\n## テキスト層で検出した記号コード（これらを図面上で必ず探す）\n"
            f"{', '.join(symbol_codes[:60])}\n"
            f"上記コードが図面に描かれていれば、対応する部材を行に起こしてください。"
        )
    return (
        f"## ページ {page.page}\n"
        f"このページから拾い出し項目を JSON 配列で返してください。\n"
        f"テキスト層（機器表・数量表があれば最優先で使う）: {page.text[:8000] if page.text else '(なし)'}"
        f"{codes_hint}"
    )


def _verification_user_text(
    page: DrawingPage,
    existing_items: list[TakeoffItem],
    symbol_codes: list[str],
) -> str:
    """Pass2 (verification) 用のユーザーテキスト。既抽出リストを渡す。"""
    existing_summary = "\n".join(
        f"  - {it.name} / spec={it.spec or '-'} / loc={it.location or '-'}"
        for it in existing_items[:60]
    )
    codes_hint = ""
    if symbol_codes:
        # Pass2 では特に未抽出のコードに絞る
        extracted_specs = {_norm(it.spec) for it in existing_items if it.spec}
        missing_codes = [c for c in symbol_codes if _norm(c) not in extracted_specs]
        if missing_codes:
            codes_hint = (
                f"\n\n## まだ抽出されていないテキスト層の記号コード（重点確認）\n"
                f"{', '.join(missing_codes[:40])}"
            )
    return (
        f"## ページ {page.page} — 追加漏れ確認\n\n"
        f"### すでに抽出済みの項目（これらは出力しない）:\n"
        f"{existing_summary or '  (なし)'}\n"
        f"{codes_hint}\n\n"
        f"上記リストに**載っていない**部材を図面から探して JSON 配列で返してください。\n"
        f"テキスト層: {page.text[:2000] if page.text else '(なし)'}"
    )


def _harvest_template(templates: dict[tuple, list[bytes]], it: TakeoffItem, tile: Tile) -> None:
    """タイルのLLM検出（bbox付き・単位=個）から記号テンプレートを切り出して貯める。

    CV検算（cv_count.recount）の入力。ここで採るのは「LLMが個数モノだと判定した
    領域の見た目」であって、正しさはこの時点では問わない（数えるのは踊り場判定側）。
    """
    if it.unit != "個" or it.bbox is None:
        return
    key = _item_key(it)
    bucket = templates.setdefault(key, [])
    if len(bucket) >= 3:
        return
    from .cv_count import crop_template

    crop = crop_template(tile.image_png, (it.bbox.x0, it.bbox.y0, it.bbox.x1, it.bbox.y1))
    if crop:
        bucket.append(crop)


def extract(
    drawing: Drawing,
    *,
    client: LLMClient | None = None,
    two_pass: bool = False,
    use_text_table: bool = True,
    failures: list[str] | None = None,
) -> list[TakeoffItem]:
    """各ページを LLM に投げて TakeoffItem のリストを返す。

    two_pass=True のとき:
      Pass1: 通常の拾い出し
      Pass2: Pass1 の結果を見せた上で「漏れがないか」確認する verification pass
      両者を dedup してマージ。

    DrawingPage に tiles が乗っていれば、各タイルを個別に LLM 呼び出しし、
    最後に spec / (cat,name) で重複除去する。タイル分割時の bbox は無視する。
    画像も tile も無いページはテキストのみで投げる（フォールバック）。

    use_text_table=True のとき、各ページのテキスト層(機器表/数量表)から確定情報を
    抽出し、vision 結果と突合する（数量・型番を機器表優先で採用、拾い漏れを補完）。
    """
    client = client or get_llm_client()
    sups = load_suppressions()
    if sups:
        logger.info("拾わない学習: %d品目（過去に消された品目をプロンプトで教える）", len(sups))
    system_prompt = (
        _load_prompt(resolve_knowledge("extraction.txt"))
        + _learned_hint()
        + _suppression_hint(sups)
    )
    verify_prompt  = _load_prompt(resolve_knowledge("verification.txt")) if two_pass else ""
    all_items: list[TakeoffItem] = []

    for page in drawing.pages:
        symbol_codes = _extract_symbol_codes(page.text or "")
        if symbol_codes:
            logger.info("page %d: %d symbol codes detected in text layer", page.page, len(symbol_codes))

        # ---- Pass 1: 通常抽出 ----
        templates: dict[tuple, list[bytes]] = {}
        if page.tiles:
            tile_items: list[TakeoffItem] = []

            def _one_tile(tile: Tile) -> tuple[Tile, list[TakeoffItem] | None, str | None]:
                logger.info(
                    "page %d: extracting tile (r=%d, c=%d) of %d行×%d列",
                    page.page, tile.row, tile.col, tile.n_rows, tile.n_cols,
                )
                try:
                    got = _call_llm_for_image(
                        client,
                        system_prompt,
                        image_png=tile.image_png,
                        user_text=_tile_user_text(page, tile, symbol_codes),
                        page_number=page.page,
                        # bbox はテンプレート採取に使う（採取後に必ず捨てる）
                        drop_bbox=False,
                    )
                    return tile, got, None
                except ExtractionFailed as e:
                    # このタイルだけ諦める。他のタイルの結果は捨てない。
                    return tile, None, str(e)

            # タイルは互いに独立なので並列で読む。直列だと6タイルで70〜90秒かかり、
            # CV検算や将来の検証パスに使う時間予算が残らない（実測: 並列で約1/4）。
            workers = min(len(page.tiles), int(os.environ.get("GOPIPE_TILE_CONCURRENCY", "6")))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(_one_tile, page.tiles))
            for tile, got, err in results:
                if err is not None:
                    logger.error("page %d: タイル(r=%d,c=%d)を読み取れず: %s", page.page, tile.row, tile.col, err)
                    if failures is not None:
                        failures.append(err)
                    continue
                for it in got:
                    _harvest_template(templates, it, tile)
                    # タイル座標のままページに載せると位置が嘘になる。
                    # 捨てずにページ座標へ直す＝ここが「色で系統を判定する」工事の入口。
                    if it.bbox is not None:
                        conv = tile_box_to_page(tile, it.bbox)
                        it.bbox = BBox.from_list(list(conv)) if conv else None
                tile_items.extend(got)
            before = len(tile_items)
            page_items = _merge_tile_items(tile_items)
            logger.info("page %d: pass1 %d行 → %d行（タイル間は合算）", page.page, before, len(page_items))
        else:
            try:
                page_items = _call_llm_for_image(
                    client,
                    system_prompt,
                    image_png=page.image_png,
                    user_text=_whole_page_user_text(page, symbol_codes),
                    page_number=page.page,
                    drop_bbox=False,
                )
            except ExtractionFailed as e:
                # このページは「0件」ではなく「読めていない」。区別して必ず人に伝える。
                logger.error("page %d: 読み取り失敗: %s", page.page, e)
                if failures is not None:
                    failures.append(str(e))
                continue
            logger.info("page %d: pass1 extracted %d items", page.page, len(page_items))

        # ---- Pass 2: Verification (two_pass=True のみ) ----
        if two_pass and verify_prompt and page.image_png:
            logger.info("page %d: running verification pass ...", page.page)
            try:
                extra_items = _call_llm_for_image(
                    client,
                    verify_prompt,
                    image_png=page.image_png,
                    user_text=_verification_user_text(page, page_items, symbol_codes),
                    page_number=page.page,
                    drop_bbox=False,
                    max_tokens=VERIFY_MAX_TOKENS,
                )
            except ExtractionFailed as e:
                logger.error("page %d: 漏れ確認パス失敗: %s", page.page, e)
                if failures is not None:
                    failures.append(str(e))
                extra_items = []
            logger.info("page %d: verification pass found %d additional items", page.page, len(extra_items))
            combined = page_items + extra_items
            before = len(combined)
            page_items = _dedupe_items(combined)
            logger.info("page %d: after dedup %d → %d items", page.page, before, len(page_items))

        # ---- 図面の色を測って行に貼る（AIに色を聞かない）----
        # 集約が終わった後の行に貼る。集約前に貼ると、統合で消える側を塗って空振りする。
        color_src = page.image_raw or page.image_png
        if color_src:
            painted = 0
            for it in page_items:
                if it.bbox is None:
                    continue
                got = sample_region(
                    color_src, (it.bbox.x0, it.bbox.y0, it.bbox.x1, it.bbox.y1)
                )
                if got:
                    it.color = got["color"]
                    it.color_hue = got["hue"]
                    painted += 1
            if painted:
                logger.info(
                    "page %d: %d行に色を貼った（機械で実測・意味の対応付けは会社の辞書）",
                    page.page, painted,
                )

        # ---- 個数モノのCV検算（記号テンプレート照合で数え直す）----
        if templates and page.image_png:
            try:
                from .cv_count import recount

                # タイルはほぼ原寸(native dpi)、ページ画像は used_dpi。倍率が違う環境でも
                # テンプレートの縮尺が合うよう、実測の幅比からヒントを渡す。
                row0 = [t for t in page.tiles if t.row == 0]
                tiled_w = sum((t.core[2] - t.core[0]) * t.width for t in row0)
                hint = (page.width / tiled_w) if tiled_w else 1.0
                for note in recount(
                    page.image_png, page_items, templates, item_key=_item_key, scale_hint=hint
                ):
                    logger.info("page %d: %s", page.page, note)
            except Exception as e:  # noqa: BLE001  検算層の失敗で抽出を道連れにしない
                logger.warning("page %d: CV検算をスキップ（%s）", page.page, e)

        # ---- 機器表テキスト層との突合（確定情報を優先）----
        if use_text_table:
            before = len(page_items)
            page_items = reconcile_with_text_table(page_items, page.text or "", page=page.page)
            if len(page_items) != before:
                logger.info(
                    "page %d: 機器表突合で %d → %d items", page.page, before, len(page_items)
                )

        all_items.extend(page_items)

    if sups:
        hit = _mark_suppressed(all_items, sups)
        if hit:
            logger.info(
                "拾わない学習: %d行が「消したはずの品目」として出てきた（消さずに印を付けた）", hit
            )

    return all_items
