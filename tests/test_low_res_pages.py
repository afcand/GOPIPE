"""大判・多ページで解像度が落ちたページを「読めていない」として運ぶテスト。

2026-09-08 実測: A1 23枚を1リクエストで投げると、9回の呼び出し予算をページ数で
割るため 1ページ1タイル＝丸ごと送りになり、実効47dpi まで落ちていた
（2026-08-19 に図面の表が読めなくなった 112dpi より更に低い）。
当時これはログにしか出ておらず、結果を見た人には分からなかった。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff import pdf_loader  # noqa: E402


def test_呼び出し予算は環境変数で上げられる(monkeypatch):
    """既定の9はサーバレスの300秒を守る数であって、読める解像度の数ではない。"""
    monkeypatch.delenv("GOPIPE_MAX_LLM_CALLS", raising=False)
    assert pdf_loader.max_llm_calls() == pdf_loader.AUTO_TILE_MAX_CALLS
    monkeypatch.setenv("GOPIPE_MAX_LLM_CALLS", "60")
    assert pdf_loader.max_llm_calls() == 60
    monkeypatch.setenv("GOPIPE_MAX_LLM_CALLS", "とんでもない値")
    assert pdf_loader.max_llm_calls() == pdf_loader.AUTO_TILE_MAX_CALLS


def test_予算を上げれば多ページの大判図でも分割される(monkeypatch):
    # A1 横 (841x594mm) を 300dpi で描いたときの画素数
    w, h = 841 / 25.4 * 300, 594 / 25.4 * 300
    monkeypatch.delenv("GOPIPE_MAX_LLM_CALLS", raising=False)
    rows, cols = pdf_loader.plan_tiles(w, h, page_count=23)
    assert rows * cols == 1, "既定では23ページに予算が足りず丸ごと送りになる"

    monkeypatch.setenv("GOPIPE_MAX_LLM_CALLS", "230")
    rows, cols = pdf_loader.plan_tiles(w, h, page_count=23)
    assert rows * cols > 1, "予算を上げれば分割される"

    # 効くかどうかは「モデルに届く実効dpi」で測る。タイル数が増えた分だけ
    # 長辺が短くなり、API 側の縮小が減る＝字が読める向きに動く。
    before = pdf_loader.effective_dpi(int(w), int(h), 300)
    after = pdf_loader.effective_dpi(int(w / cols), int(h / rows), 300)
    assert after > before * 2, f"実効dpi {before:.0f} → {after:.0f} で足りない"


def test_低解像度のページは実効dpiを持ち回れる():
    """読めていないページを黙って0件にしないため、ページ自身が値を持つ。"""
    from gopipe_takeoff.models import DrawingPage

    ok = DrawingPage(page=1, width=100, height=100)
    ng = DrawingPage(page=2, width=100, height=100, low_res_dpi=47.0)
    assert ok.low_res_dpi is None
    assert ng.low_res_dpi == 47.0
