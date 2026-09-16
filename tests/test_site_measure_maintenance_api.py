"""現地実測 / 更新提案の Web 導線が拠って立つ API の回帰テスト。

アニメ『AIと源さん』第5章で客に見せている2つ（スマホ実測・更新時期の提案）は、
ここが崩れると画面が黙って嘘をつく。特に更新提案は「どこから明細を持ってくるか」が
急所で、サンプル図面の部材で台帳が出来てしまうと、顧客の建物ではない中身に
顧客の布設年が刺さる。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _restore_env():
    before = os.environ.get("GOPIPE_API_KEY")
    yield
    if before is None:
        os.environ.pop("GOPIPE_API_KEY", None)
    else:
        os.environ["GOPIPE_API_KEY"] = before


# --- 現地実測 ---------------------------------------------------------------


def test_site_measure_converts_duct_to_area():
    """角ダクト 500×400 を 10m → 展開面積 2(W+H)×L = 18 m²。"""
    r = client.post(
        "/site_measure",
        json=[{"kind": "角ダクト", "name": "角ダクト", "width_mm": 500,
               "height_mm": 400, "length_m": 10}],
    )
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["unit"] == "m2"
    assert item["quantity"] == pytest.approx(18.0)


def test_site_measure_handles_pipe_and_count():
    r = client.post(
        "/site_measure",
        json=[
            {"kind": "配管", "name": "冷温水配管", "dia_mm": 80, "length_m": 12},
            {"kind": "個数", "name": "吹出口", "count": 8},
        ],
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert (items[0]["unit"], items[0]["quantity"]) == ("m", 12.0)
    assert (items[1]["unit"], items[1]["quantity"]) == ("個", 8.0)


def test_site_measure_persist_requires_key():
    """保存は service_role で書く。無認証で他社の台帳に書かせない。"""
    os.environ["GOPIPE_API_KEY"] = "test-key"
    r = client.post("/site_measure?persist=true", json=[{"kind": "個数", "name": "弁", "count": 1}])
    assert r.status_code == 401


def test_site_measure_org_dictionary_requires_key():
    os.environ["GOPIPE_API_KEY"] = "test-key"
    r = client.post("/site_measure?org_slug=haruki", json=[{"kind": "個数", "name": "弁", "count": 1}])
    assert r.status_code == 401


# --- 更新提案（保存済み明細から）--------------------------------------------


SAVED_ITEMS = [
    {"name": "給水管", "spec": "VP20", "unit": "m", "quantity": 30, "category": "配管"},
    {"name": "排水管", "spec": "VU75", "unit": "m", "quantity": 18, "category": "配管"},
    # 台帳の対象外（m単位でも機器でもない）。混ぜても落ちないこと。
    {"name": "吹出口", "spec": None, "unit": "個", "quantity": 8, "category": "空調"},
]


def test_maintenance_from_items_uses_the_items_it_was_given():
    """台帳はサンプル図面ではなく、渡した明細だけから出来ること。

    ここが崩れると「10年前に施工したあのビル」の提案が、別の建物の部材で出る。
    """
    r = client.post(
        "/maintenance/from_items?installed_year=2008&current_year=2026", json=SAVED_ITEMS
    )
    assert r.status_code == 200
    body = r.json()
    names = {row["name"] for row in body["ledger"]}
    assert names == {"給水管", "排水管"}  # 個数の行は台帳の対象外
    assert body["source_items"] == 3


def test_maintenance_from_items_sorts_by_remaining_years():
    r = client.post(
        "/maintenance/from_items?installed_year=2008&current_year=2026", json=SAVED_ITEMS
    )
    remaining = [row["remaining"] for row in r.json()["ledger"]]
    assert remaining == sorted(remaining)
    for row in r.json()["ledger"]:
        # 残存年 = 布設年 + 耐用年数 − 評価年。画面の🔴判定がこれに乗っている。
        assert row["remaining"] == row["installed_year"] + row["service_life"] - 2026
        assert row["recommend_year"] == row["installed_year"] + row["service_life"]


def test_maintenance_from_items_requires_installed_year():
    """布設年が無いまま台帳を作らせない（0年起点の嘘の残存年を出さない）。"""
    r = client.post("/maintenance/from_items", json=SAVED_ITEMS)
    assert r.status_code == 400


def test_maintenance_from_items_org_requires_key():
    os.environ["GOPIPE_API_KEY"] = "test-key"
    r = client.post(
        "/maintenance/from_items?installed_year=2008&org_slug=haruki", json=SAVED_ITEMS
    )
    assert r.status_code == 401


def test_maintenance_from_items_survives_a_broken_row():
    """1行壊れていても、残りの台帳は出す（1件で全部落とさない）。"""
    rows = [*SAVED_ITEMS, {"name": "壊れた行", "unit": "m", "quantity": "あ"}]
    r = client.post(
        "/maintenance/from_items?installed_year=2008&current_year=2026", json=rows
    )
    assert r.status_code == 200
    assert {row["name"] for row in r.json()["ledger"]} == {"給水管", "排水管"}


def test_maintenance_from_items_empty_ledger_is_not_an_error():
    """対象が無いのと失敗は別物。画面が「0件」と「作れなかった」を取り違えないように。"""
    r = client.post(
        "/maintenance/from_items?installed_year=2008",
        json=[{"name": "吹出口", "unit": "個", "quantity": 8, "category": "空調"}],
    )
    assert r.status_code == 200
    assert r.json()["ledger"] == []
    assert r.json()["source_items"] == 1


# --- ドキュメンテーション ---------------------------------------------------


@pytest.mark.parametrize(
    "name", ["insulation", "site_measure", "riser", "maintenance_from_items"]
)
def test_body_endpoints_keep_their_docstrings(name: str):
    """docstring がガード呼び出しの後ろに落ちると /docs から説明が消える（実際に起きていた）。"""
    import api.main as m

    assert getattr(m, name).__doc__, f"{name} の docstring が失われている"
