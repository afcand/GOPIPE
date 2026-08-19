"""現地実測を既存の物件へ足すとき、人が手で足した行を消さないことを固定する。

🔴 実測は図面を持たない（drawing_id が無い）。置き換えで保存すると
`drawing_id is null` の行を全部消すため、/api/items/rows で人が足した行が
警告なく消える。物件を選んで実測を足す機能を入れるなら、append が必須。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff import store  # noqa: E402
from gopipe_takeoff.models import TakeoffItem  # noqa: E402


def _item(name: str) -> TakeoffItem:
    return TakeoffItem(page=1, name=name, quantity=1, unit="個")


def _spy(monkeypatch):
    calls: list[tuple] = []

    def fake_req(method, table, *, body=None, params="", prefer=""):
        calls.append((method, table, params))
        if method == "POST":
            return [{"id": "new"}]
        return []

    monkeypatch.setattr(store, "_req", fake_req)
    return calls


def test_append_deletes_nothing(monkeypatch):
    calls = _spy(monkeypatch)
    store.replace_takeoff_items("p1", "o1", [_item("弁")], None, append=True)
    assert not [c for c in calls if c[0] == "DELETE"], "足すだけ＝何も消さない"


def test_replace_without_drawing_deletes_drawingless_rows(monkeypatch):
    """既定（置き換え）の挙動は変えない＝図面なしの行を入れ替える。"""
    calls = _spy(monkeypatch)
    store.replace_takeoff_items("p1", "o1", [_item("弁")], None)
    dels = [c for c in calls if c[0] == "DELETE"]
    assert len(dels) == 1
    assert "drawing_id=is.null" in dels[0][2]


def test_replace_with_drawing_scopes_delete_to_that_drawing(monkeypatch):
    calls = _spy(monkeypatch)
    store.replace_takeoff_items("p1", "o1", [_item("弁")], "d1")
    dels = [c for c in calls if c[0] == "DELETE"]
    assert len(dels) == 1 and "drawing_id=eq.d1" in dels[0][2]


def test_empty_items_never_delete(monkeypatch):
    """0件が返ったときに前回の結果を道連れにしない（既存の不変条件）。"""
    calls = _spy(monkeypatch)
    assert store.replace_takeoff_items("p1", "o1", [], None) == 0
    assert not calls
