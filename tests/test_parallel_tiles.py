"""タイル並列読みの回帰テスト。

固定すること:
  1. 並列でも結果はタイル順で決定的（実行のたびに行の順序が変わらない）
  2. 1タイルの失敗が他のタイルの結果を道連れにしない（失敗は failures に載る）
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from llm_client.base import LLMResponse  # noqa: E402

from gopipe_takeoff.extractor import ExtractionFailed, extract  # noqa: E402
from gopipe_takeoff.models import Drawing, DrawingPage, Tile  # noqa: E402

DUMMY_PNG = b"\x89PNG\r\n\x1a\n_dummy_"


def _tiles(n):
    return [
        Tile(image_png=DUMMY_PNG, row=0, col=c, rows=1, cols=n, width=10, height=10)
        for c in range(n)
    ]


class SlowFirstClient:
    """col=0 だけ遅い擬似LLM。並列時の完了順は col=1,2,0 になる。"""

    name = "scripted"
    model = "scripted"

    def complete(self, messages, *, max_tokens=4096, temperature=0.0) -> LLMResponse:
        content = next((m.content for m in messages if m.role == "user"), "")
        col = 0 if "col=0" in content else (1 if "col=1" in content else 2)
        if col == 0:
            time.sleep(0.3)
        rows = [{
            "page": 1, "name": f"部材{col}", "spec": f"SP-{col}", "quantity": col + 1,
            "unit": "個", "location": "1F", "confidence": 0.8,
        }]
        return LLMResponse(text=json.dumps(rows, ensure_ascii=False), model=self.model)


def test_parallel_results_keep_tile_order():
    page = DrawingPage(page=1, width=30, height=10, text="", image_png=DUMMY_PNG, tiles=_tiles(3))
    items = extract(Drawing(source_path="x", pages=[page]), client=SlowFirstClient(), two_pass=False)
    assert [it.name for it in items] == ["部材0", "部材1", "部材2"], \
        "col=0 が最後に終わっても、結果はタイル順で並ぶ（順序が実行ごとに揺れない）"
    assert [it.quantity for it in items] == [1, 2, 3]


class FailMiddleClient:
    name = "scripted"
    model = "scripted"

    def complete(self, messages, *, max_tokens=4096, temperature=0.0) -> LLMResponse:
        content = next((m.content for m in messages if m.role == "user"), "")
        if "col=1" in content:
            raise ExtractionFailed("2ページ目相当: 読めない")
        col = 0 if "col=0" in content else 2
        rows = [{
            "page": 1, "name": f"部材{col}", "spec": f"SP-{col}", "quantity": 1,
            "unit": "個", "location": "1F", "confidence": 0.8,
        }]
        return LLMResponse(text=json.dumps(rows, ensure_ascii=False), model=self.model)


def test_one_tile_failure_does_not_sink_the_page():
    page = DrawingPage(page=1, width=30, height=10, text="", image_png=DUMMY_PNG, tiles=_tiles(3))
    failures: list[str] = []
    items = extract(
        Drawing(source_path="x", pages=[page]),
        client=FailMiddleClient(), two_pass=False, failures=failures,
    )
    assert sorted(it.name for it in items) == ["部材0", "部材2"]
    assert len(failures) == 1, "失敗は黙って消えず failures に載る"
