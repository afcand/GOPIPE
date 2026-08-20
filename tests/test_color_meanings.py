"""色の辞書（色→意味）の回帰テスト。

🔴 色の意味は会社ごと・図面ごとにしか決まらない。
実測(2026-08-20 ハルキ5枚): 「ダクト」が5枚で5通りの色。凡例があったのは1枚だけで、
その凡例の緑「(移設)」に対応する作図要素は本体に0px（凡例にあっても実体が無い）。
共通辞書へ流用すると「既存を新設で拾う」＝数量が合っていても見積が狂う事故になる。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff import store  # noqa: E402


def _spy(monkeypatch, rows=None):
    calls: list[tuple] = []

    def fake_req(method, table, *, body=None, params="", prefer=""):
        calls.append((method, table, params, body))
        return rows if method == "GET" else []

    monkeypatch.setattr(store, "ensure_org", lambda *a, **k: "org1")
    monkeypatch.setattr(store, "_req", fake_req)
    return calls


def test_meanings_are_scoped_to_the_org(monkeypatch):
    """🔴必ず org で閉じる。他社の色の意味を引くと系統を取り違える。"""
    calls = _spy(monkeypatch, [{"color": "青", "meaning": "既存再利用", "note": None}])
    got = store.load_color_meanings("acme")
    assert got == {"青": {"meaning": "既存再利用", "note": None}}
    assert "org_id=eq.org1" in calls[0][2]


def test_save_upserts_on_org_and_color(monkeypatch):
    calls = _spy(monkeypatch)
    store.save_color_meaning("acme", "橙茶", "新設")
    method, table, params, body = calls[0]
    assert (method, table) == ("POST", "color_meanings")
    assert "on_conflict=org_id,color" in params
    assert body[0]["color"] == "橙茶" and body[0]["meaning"] == "新設"


def test_delete_scopes_to_org_and_color(monkeypatch):
    calls = _spy(monkeypatch)
    store.delete_color_meaning("acme", "青")
    method, table, params, _ = calls[0]
    assert method == "DELETE" and "org_id=eq.org1" in params and "color=eq." in params


def test_seen_colors_counts_and_averages_hue(monkeypatch):
    """実在する色だけを人に聞くための集計。出ない色を聞いても答えられない。"""
    _spy(monkeypatch, [
        {"color": "青", "color_hue": 209.0},
        {"color": "青", "color_hue": 211.0},
        {"color": "橙茶", "color_hue": 27.0},
        {"color": None, "color_hue": None},
    ])
    got = store.seen_colors("acme")
    assert [g["color"] for g in got] == ["青", "橙茶"]  # 多い順
    assert got[0]["count"] == 2 and got[0]["hue"] == 210.0
    assert got[1]["count"] == 1


def test_items_persist_their_color(monkeypatch):
    """明細に色を残す。何色が出るか分からないと辞書の口に出す選択肢が作れない。"""
    from gopipe_takeoff.models import TakeoffItem

    calls = _spy(monkeypatch)
    it = TakeoffItem(page=1, name="ダクト", quantity=1, unit="m")
    it.color, it.color_hue = "青", 209.3
    store.replace_takeoff_items("p1", "o1", [it], "d1")
    posted = [c for c in calls if c[0] == "POST" and c[1] == "takeoff_items"]
    assert posted and posted[0][3][0]["color"] == "青"
    assert posted[0][3][0]["color_hue"] == 209.3
