"""ベクター拾い出しの成果が API を通っても消えないことを固定する。

エンジンは 2026-09-08 から「拾えていないもの(gaps)」「型に載らなかった印字(unread)」
「画像認識の回数(llm_calls)」を出しているが、**API がそれを落とすと現場には届かない**。
表に出てこない部材は、現場から見れば 0 個と区別がつかず、そのまま見積になる。
qty_basis が本番応答で全行 None だった事故（tests/test_api_qty_basis.py）と同じ型。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "shared", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from api.main import app  # noqa: E402

client = TestClient(app)


def test_takeoff_response_carries_llm_calls():
    """費用の説明の根拠。0 回なら「使っていません」と言い切れる。"""
    res = client.post("/takeoff", data={"provider": "mock"})
    assert res.status_code == 200
    assert "llm_calls" in res.json()


def test_takeoff_accepts_no_llm():
    """画像認識を使わない経路が Web から選べること（費用0・毎回同じ数）。"""
    res = client.post("/takeoff", data={"provider": "mock", "no_llm": "true"})
    assert res.status_code == 200
    assert res.json()["llm_calls"] == 0


@pytest.mark.parametrize("key", ["gaps", "unread_labels"])
def test_export_accepts_gaps_and_unread(key):
    """拾えていないものを渡して Excel が作れること（別シートが消えない）。"""
    payload = {
        "items": [{"name": "吸込口", "quantity": 1, "unit": "個", "page": 1}],
        "gaps": [{
            "item": "防火ダンパー",
            "reason": "凡例にあるが図の中に文字が0件（記号で描かれている）",
            "action": "図面で数えてください",
            "pages": [1, 2],
        }],
        "unread_labels": [{"page": 1, "text": "FD-1 φ250"}],
    }
    if key == "gaps":
        payload.pop("unread_labels")
    else:
        payload.pop("gaps")
    res = client.post("/export/xlsx", json=payload)
    assert res.status_code == 200
    assert res.content[:2] == b"PK"  # xlsx（zip）が返っている


def test_export_gaps_reach_the_workbook():
    """渡した gaps が本当にブックへ書かれること（受け取って捨てる、を防ぐ）。

    生のバイト列を探しても当たらない（xlsx の XML は日本語を文字参照へ逃がす）。
    ブックとして開いてセルを読む。
    """
    import io

    import openpyxl

    res = client.post("/export/xlsx", json={
        "items": [{"name": "吸込口", "quantity": 1, "unit": "個", "page": 1}],
        "gaps": [{"item": "防火ダンパー", "reason": "記号で描かれている",
                  "action": "図面で数えてください", "pages": [1, 2]}],
        "unread_labels": [{"page": 3, "text": "FD-1 φ250"}],
    })
    assert res.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(res.content))
    rows = list(wb["拾えていないもの"].iter_rows(values_only=True))
    flat = ["".join(str(v) for v in r if v is not None) for r in rows[1:]]
    assert any("防火ダンパー" in t and "図面で数えてください" in t for t in flat)
    assert any("FD-1" in t for t in flat)
