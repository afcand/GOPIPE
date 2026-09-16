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


# --- 指した範囲だけ拾う ------------------------------------------------------


def test_takeoff_rejects_broken_region():
    """範囲の指定が壊れていたら、黙って全体を拾わずに断る。

    ここで黙って1枚まるごと拾うと、人は「範囲を指したつもり」なのに全体の数量が
    返り、しかも見た目には気づけない。
    """
    res = client.post("/takeoff", data={"provider": "mock", "region": "{壊れた"})
    assert res.status_code == 400


def _pdf_bytes(pages: int = 2) -> bytes:
    """検査用の小さな図面（範囲を指す口は、実物の紙が無いと確かめられない）。"""
    import fitz

    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=800, height=600)
        pg.insert_text((40, 60), f"TOPLEFT{i} FD-1")
        pg.insert_text((600, 520), f"BOTRIGHT{i} VD-2")
    return doc.tobytes()


def test_takeoff_region_is_echoed_back():
    """どの範囲から出た明細かを応答に載せる（複数の範囲を積み上げるため）。"""
    res = client.post(
        "/takeoff",
        data={"provider": "mock", "region": '{"page":1,"x0":0.1,"y0":0.1,"x1":0.6,"y1":0.6}'},
        files={"file": ("d.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("region", {}).get("page") == 1
    assert "範囲" in body["region"]["label"]


def test_takeoff_refuses_a_region_without_a_drawing():
    """図面が無いのに範囲だけ来たら、黙ってデモのサンプル全体を返さない。"""
    res = client.post("/takeoff", data={
        "provider": "mock", "region": '{"page":1,"x0":0,"y0":0,"x1":0.5,"y1":0.5}'})
    assert res.status_code == 400


def test_page_png_returns_an_image_and_page_count():
    """画面に図面を出す口。ページ数も返さないと、2枚目以降へ行けない。"""
    res = client.post("/page_png", data={"page": "1", "dpi": "60"},
                      files={"file": ("d.pdf", _pdf_bytes(3), "application/pdf")})
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert res.headers["x-page-count"] == "3"


def test_page_png_refuses_a_page_that_does_not_exist():
    res = client.post("/page_png", data={"page": "99"},
                      files={"file": ("d.pdf", _pdf_bytes(), "application/pdf")})
    assert res.status_code == 400


# --- 1点を指して拾う --------------------------------------------------------


def test_pick_requires_a_drawing():
    res = client.post("/pick", data={"mode": "duct", "x": "0.5", "y": "0.5"})
    assert res.status_code == 400


def test_pick_refuses_an_unknown_mode():
    res = client.post("/pick", data={"mode": "taste", "x": "0.5", "y": "0.5"},
                      files={"file": ("d.pdf", _pdf_bytes(), "application/pdf")})
    assert res.status_code == 400


def test_pick_color_reports_the_color_and_how_many():
    """線を指すと、その色と、同じ色が何個あるかが返る。"""
    import fitz

    doc = fitz.open()
    pg = doc.new_page(width=400, height=400)
    for i in range(5):
        pg.draw_line(fitz.Point(50, 50 + i * 20), fitz.Point(350, 50 + i * 20), color=(1, 0, 0))
    pg.draw_line(fitz.Point(50, 300), fitz.Point(350, 300), color=(0, 0, 1))
    res = client.post(
        "/pick",
        data={"mode": "color", "x": str(200 / 400), "y": str(50 / 400)},
        files={"file": ("d.pdf", doc.tobytes(), "application/pdf")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["detail"]["hex"] == "#ff0000"
    assert body["detail"]["shapes"] == 5, "同じ色の線だけを数える（青は入らない）"


def test_pick_symbol_counts_the_same_shape():
    """記号を指すと、同じ形が図面に何個あるかを数える。"""
    import fitz

    doc = fitz.open()
    pg = doc.new_page(width=400, height=400)
    for i in range(7):
        x, y = 40 + (i % 4) * 80, 40 + (i // 4) * 80
        pg.draw_rect(fitz.Rect(x, y, x + 12, y + 12), color=(0, 0, 0))
        pg.draw_line(fitz.Point(x, y), fitz.Point(x + 12, y + 12), color=(0, 0, 0))
    res = client.post(
        "/pick",
        data={"mode": "symbol", "x": str(46 / 400), "y": str(46 / 400)},
        files={"file": ("d.pdf", doc.tobytes(), "application/pdf")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["detail"]["count"] >= 1
    assert body["items"][0]["unit"] == "個"


def test_pick_duct_says_why_when_there_is_no_fill():
    """塗りが無い場所を指したら、0mと答えずに理由を返す。"""
    import fitz

    doc = fitz.open()
    pg = doc.new_page(width=400, height=400)
    pg.draw_line(fitz.Point(10, 10), fitz.Point(390, 10), color=(0, 0, 0))
    res = client.post("/pick", data={"mode": "duct", "x": "0.5", "y": "0.9"},
                      files={"file": ("d.pdf", doc.tobytes(), "application/pdf")})
    assert res.status_code == 200
    body = res.json()
    assert body["items"] == []
    assert "塗られた図形がありません" in body["note"]


# --- 覚えた指示 --------------------------------------------------------------


def test_instructions_need_a_key():
    """他社の覚えた指示を無認証で覗けない。"""
    assert client.get("/instructions?org_slug=x").status_code in (401, 503)


def test_instruction_refuses_an_unreadable_color():
    import os

    os.environ["GOPIPE_API_KEY"] = "test-key"
    res = client.post("/instructions", json={"org_slug": "x", "kind": "color",
                                             "payload": {"hex": "あお", "name": "青"}},
                      headers={"x-gopipe-key": "test-key"})
    assert res.status_code in (400, 503)


def test_instruction_refuses_an_unknown_kind():
    import os

    os.environ["GOPIPE_API_KEY"] = "test-key"
    res = client.post("/instructions", json={"org_slug": "x", "kind": "flavor", "payload": {}},
                      headers={"x-gopipe-key": "test-key"})
    assert res.status_code == 400


def test_page_png_carries_the_sheet_key():
    """画面が「図面の型」を持ち回れること（覚えた指示を引く鍵）。"""
    res = client.post("/page_png", data={"page": "1", "dpi": "60"},
                      files={"file": ("d.pdf", _pdf_bytes(), "application/pdf")})
    assert res.status_code == 200
    assert "x-sheet-key" in res.headers
