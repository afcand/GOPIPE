"""「拾わない」学習の回帰テスト。

削除は積算で最も強い信号（要るか要らないかの判断そのもの・ワンクリック）だが、
学び方を間違えると **その品目が二度と表に出なくなる**＝拾い出しで最悪の失敗。
ここでは「学びすぎない」「黙って消さない」の2つを固定する。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff import store  # noqa: E402
from gopipe_takeoff.extractor import (  # noqa: E402
    _mark_suppressed,
    _suppression_hint,
)
from gopipe_takeoff.models import TakeoffItem  # noqa: E402


def _removed(name, project, spec=None, raw=None):
    return {
        "before": {"name": name, "raw_name": raw, "spec": spec},
        "project_id": project,
    }


def _stub(monkeypatch, rows):
    monkeypatch.setattr(store, "ensure_org", lambda *a, **k: "org1")
    monkeypatch.setattr(store, "_req", lambda *a, **k: rows)


# --------------------------------------------------------------------------
# 学びすぎない
# --------------------------------------------------------------------------
def test_single_deletion_is_not_learned(monkeypatch):
    """1回消しただけでは学ばない（誤削除・その物件限りの事情がある）。"""
    _stub(monkeypatch, [_removed("緞帳", "p1")])
    assert store.load_suppressions("acme") == []


def test_same_project_repeats_are_not_learned(monkeypatch):
    """同じ物件で連打しただけでは学ばない。物件をまたいで初めて会社の傾向。"""
    _stub(monkeypatch, [_removed("緞帳", "p1"), _removed("緞帳", "p1"), _removed("緞帳", "p1")])
    assert store.load_suppressions("acme") == []


def test_learned_across_two_projects(monkeypatch):
    _stub(monkeypatch, [_removed("緞帳", "p1"), _removed("緞帳", "p2")])
    got = store.load_suppressions("acme")
    assert [g["name"] for g in got] == ["緞帳"]
    assert got[0]["hits"] == 2 and got[0]["projects"] == 2


def test_raw_name_is_the_key(monkeypatch):
    """鍵はAIが読んだ生名称。表示名を鍵にすると別部材を巻き添えにする。"""
    _stub(monkeypatch, [
        _removed("照明バトン", "p1", raw="バトン"),
        _removed("吊物バトン", "p2", raw="バトン"),
    ])
    got = store.load_suppressions("acme")
    assert [g["name"] for g in got] == ["バトン"]


def test_rows_without_name_are_ignored(monkeypatch):
    _stub(monkeypatch, [_removed("", "p1"), _removed(None, "p2")])
    assert store.load_suppressions("acme") == []


# --------------------------------------------------------------------------
# 黙って消さない
# --------------------------------------------------------------------------
def _item(name, raw=None, conf=0.9):
    return TakeoffItem(page=1, name=name, quantity=1, unit="個", confidence=conf, raw_name=raw)


def test_suppressed_item_is_marked_not_deleted():
    items = [_item("緞帳"), _item("仕切弁")]
    n = _mark_suppressed(items, [{"name": "緞帳", "hits": 3, "projects": 2}])
    assert n == 1
    assert len(items) == 2, "🔴消さない。消すと、学習が外れた品目が二度と表に出ない"
    assert items[0].source == "suppressed_hit"
    assert items[0].confidence <= 0.5, "人の目に留まるよう確度を落とす"
    assert items[1].source is None and items[1].confidence == 0.9


def test_mark_matches_on_raw_name_too():
    items = [_item("照明バトン", raw="バトン")]
    assert _mark_suppressed(items, [{"name": "バトン", "hits": 2, "projects": 2}]) == 1


def test_no_suppressions_changes_nothing():
    items = [_item("緞帳")]
    assert _mark_suppressed(items, []) == 0
    assert items[0].source is None and items[0].confidence == 0.9


# --------------------------------------------------------------------------
# プロンプトの文言（黙って落とさせない指示が入っていること）
# --------------------------------------------------------------------------
def test_hint_tells_model_not_to_drop_silently():
    h = _suppression_hint([{"name": "緞帳", "hits": 3, "projects": 2, "specs": []}])
    assert "緞帳" in h
    assert "要確認" in h, "工事対象なら拾って印を付けろ、と書いてあること"
    assert "黙って落とさない" in h


def test_hint_is_empty_without_learning():
    assert _suppression_hint([]) == ""
