"""F-13: 個数モノの数量潰れ回帰テスト。

**同じ絵を2回読んだ結果**と**別々のタイルを読んだ結果**は、混ぜ方が逆になる。
  - `_dedupe_items`    … 同じ絵の重複 → 合算しない（足すと倍になる）
  - `_merge_tile_items`… 別タイルの同種 → **合算する**（別の場所にある別の実体）

2026-08-19 まで両方が `_dedupe_items` だったため、タイル分割すると
弁 3個+2個→3個、配管 12m+16.5m→12m と静かに過小計上していた。
本テストはその区別を固定する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from llm_client.base import LLMResponse  # noqa: E402

from gopipe_takeoff.extractor import (  # noqa: E402
    _dedupe_items,
    _merge_tile_items,
    extract,
)
from gopipe_takeoff.models import Drawing, DrawingPage, TakeoffItem, Tile  # noqa: E402

DUMMY_PNG = b"\x89PNG\r\n\x1a\n_dummy_"


def _item(name, spec, qty, unit, loc, conf=0.8) -> TakeoffItem:
    return TakeoffItem(
        page=1, name=name, spec=spec, quantity=qty, unit=unit, location=loc, confidence=conf
    )


class ScriptedClient:
    """user メッセージ内容で応答を出し分ける擬似 LLM。"""

    name = "scripted"

    def __init__(self, routes):
        self.model = "scripted"
        self.routes = routes

    def complete(self, messages, *, max_tokens=4096, temperature=0.0) -> LLMResponse:
        content = next((m.content for m in messages if m.role == "user"), "")
        for pred, rows in self.routes:
            if pred(content):
                return LLMResponse(text=json.dumps(rows, ensure_ascii=False), model=self.model)
        return LLMResponse(text="[]", model=self.model)


def _total(items, name):
    return sum(it.quantity for it in items if it.name == name)


# --------------------------------------------------------------------------
# 1. 不変条件: dedup（同じ絵の重複）は合算しない / merge（タイル間）は合算する
# --------------------------------------------------------------------------
def test_dedupe_keeps_one_and_does_not_sum_count_items():
    """同一 spec×location の弁が 5 個別行で来ると 1 行・quantity=最大conf行 になる。"""
    rows = [
        _item("仕切弁", "GV DN20", 1, "個", "1F PS", conf=0.5),
        _item("仕切弁", "GV DN20", 1, "個", "1F PS", conf=0.9),  # 最大 conf
        _item("仕切弁", "GV DN20", 1, "個", "1F PS", conf=0.7),
        _item("仕切弁", "GV DN20", 1, "個", "1F PS", conf=0.6),
        _item("仕切弁", "GV DN20", 1, "個", "1F PS", conf=0.8),
    ]
    deduped = _dedupe_items(rows)
    assert len(deduped) == 1
    # 合算されない＝5 ではなく 1。これがプロンプト側集約を必要とする理由。
    assert _total(deduped, "仕切弁") == 1


def test_dedupe_does_not_sum_pipe_length_segments():
    """同一系統×管種×口径の配管を 2 区間で返すと延長が合算されず潰れる。"""
    rows = [
        _item("給水管(SGP)", "VLP DN20", 12.0, "m", "1F 給水系統", conf=0.8),
        _item("給水管(SGP)", "VLP DN20", 16.5, "m", "1F 給水系統", conf=0.6),
    ]
    deduped = _dedupe_items(rows)
    assert len(deduped) == 1
    assert _total(deduped, "給水管(SGP)") == 12.0  # 28.5 ではない


def test_dedupe_keeps_distinct_specs_and_locations_separate():
    """口径・系統・階が違えば別行のまま（過剰統合しない）。"""
    rows = [
        _item("仕切弁", "GV DN20", 3, "個", "1F PS"),
        _item("仕切弁", "GV DN13", 2, "個", "1F PS"),     # 口径違い → 別行
        _item("仕切弁", "GV DN20", 1, "個", "2F PS"),     # 階違い → 別行
    ]
    deduped = _dedupe_items(rows)
    assert len(deduped) == 3
    assert _total(deduped, "仕切弁") == 6


def test_merge_tiles_sums_pipe_length_across_tiles():
    """配管の延長はタイルを跨いで合算される（12m + 16.5m = 28.5m）。"""
    merged = _merge_tile_items([
        _item("給水管(SGP)", "VLP DN20", 12.0, "m", "1F 給水系統", conf=0.8),
        _item("給水管(SGP)", "VLP DN20", 16.5, "m", "1F 給水系統", conf=0.6),
    ])
    assert len(merged) == 1
    assert _total(merged, "給水管(SGP)") == 28.5


def test_merge_tiles_does_not_mix_units():
    """単位が違えば別行のまま（20m と 8m2 を足さない）。"""
    merged = _merge_tile_items([
        _item("ダクト", "400x400", 20.0, "m", "1F 空調"),
        _item("ダクト", "400x400", 8.0, "m2", "1F 空調"),
    ])
    assert len(merged) == 2


def test_merge_tiles_takes_weakest_basis():
    """出所が混ざった合計は、一番弱い根拠で語る（表 + 推定 → 推定）。"""
    a = _item("排煙口", "300x300", 4, "個", "3F")
    a.qty_basis = "table"
    b = _item("排煙口", "300x300", 1, "個", "3F")
    b.qty_basis = "estimate"
    merged = _merge_tile_items([a, b])
    assert merged[0].quantity == 5
    assert merged[0].qty_basis == "estimate"


# --------------------------------------------------------------------------
# 2. 契約: LLM が集約規約に従えば extract() で総数量が保持される
# --------------------------------------------------------------------------
def test_consolidated_rows_preserve_total_through_tile_extract():
    """各タイルが『1 行/種別・quantity=個数』で返し、別物は別キーなら保持される。"""
    client = ScriptedClient([
        (lambda c: "col=0" in c, [
            {"page": 1, "name": "仕切弁", "spec": "GV DN20", "quantity": 3,
             "unit": "個", "location": "1F PS", "confidence": 0.8},
        ]),
        # 別タイルは別系統の別部材（別キー）→ dedup で消えない
        (lambda c: "col=1" in c, [
            {"page": 1, "name": "90°エルボ", "spec": "DN20", "quantity": 7,
             "unit": "個", "location": "1F 給水系統", "confidence": 0.8},
        ]),
    ])
    t0 = Tile(image_png=DUMMY_PNG, row=0, col=0, grid=1, width=10, height=10)
    t1 = Tile(image_png=DUMMY_PNG, row=0, col=1, grid=1, width=10, height=10)
    page = DrawingPage(page=1, width=100, height=100, text="", image_png=DUMMY_PNG, tiles=[t0, t1])
    items = extract(Drawing(source_path="x", pages=[page]), client=client, two_pass=False)
    assert _total(items, "仕切弁") == 3
    assert _total(items, "90°エルボ") == 7


def test_per_instance_rows_sum_across_tiles():
    """タイルを跨いだ同一キーの個数モノは **合算** される（旧: 潰れていた）。

    タイルは重なり部分を薄くして「担当領域にあるものだけ出す」ようにしてあるので、
    別タイルの同じ弁は同じ実体ではなく別の実体＝足すのが正しい。
    """
    client = ScriptedClient([
        (lambda c: "col=0" in c, [
            {"page": 1, "name": "仕切弁", "spec": "GV DN20", "quantity": 3,
             "unit": "個", "location": "1F PS", "confidence": 0.8},
        ]),
        (lambda c: "col=1" in c, [
            {"page": 1, "name": "仕切弁", "spec": "GV DN20", "quantity": 2,
             "unit": "個", "location": "1F PS", "confidence": 0.7},
        ]),
    ])
    t0 = Tile(image_png=DUMMY_PNG, row=0, col=0, grid=1, width=10, height=10)
    t1 = Tile(image_png=DUMMY_PNG, row=0, col=1, grid=1, width=10, height=10)
    page = DrawingPage(page=1, width=100, height=100, text="", image_png=DUMMY_PNG, tiles=[t0, t1])
    items = extract(Drawing(source_path="x", pages=[page]), client=client, two_pass=False)
    # 実数 5。かつて 3（最大 conf 行）に潰れていた回帰を止める。
    assert _total(items, "仕切弁") == 5
    # 合算した行の確度は「一番弱い根拠」に合わせる（0.8 ではなく 0.7）。
    assert [it for it in items if it.name == "仕切弁"][0].confidence == 0.7


# --------------------------------------------------------------------------
# 3. プロンプトに集約ルールが入っていること（誤って削除されないよう固定）
# --------------------------------------------------------------------------
def test_extraction_prompt_has_consolidation_rule():
    text = (ROOT / "prompts" / "extraction.txt").read_text(encoding="utf-8")
    assert "数量の集約" in text
    assert "1 行" in text
    # 単位ごとの扱い（個数は数える／長さは合算）が明示されていること
    assert "個数を数えて" in text
    assert "合算" in text
