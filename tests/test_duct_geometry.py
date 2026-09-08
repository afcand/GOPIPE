"""ダクトを幾何から測るテスト（延長mと展開面積m2）。

固定したいこと（2026-09-08 NEC府中のA1ベクター図で分かったこと）:
  ・延長は推定ではなく測定で出す。塗り多角形の面積と周長から長方形を解く
  ・🔴 ハッチングをダクトとして数えない。実測では幅28mm・長さ1.44mの帯が
    同じ場所に128本あり、それだけで還気ダクト183.8mになっていた
  ・🔴 色はAIに聞かず図枠の用途表から読む。実測では外気=緑・排気=橙で、
    見た目の思い込みと逆だった
  ・展開面積は呼び寸法が図面から取れた区間だけ出す（周長を推測で埋めない）
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff import duct_geometry as dg  # noqa: E402


def test_面積と周長から長方形を解く():
    L, W = 8.0, 2.0
    got = dg.solve_rectangle(L * W, 2 * (L + W))
    assert got is not None
    assert math.isclose(got[0], L, abs_tol=1e-6)
    assert math.isclose(got[1], W, abs_tol=1e-6)


def test_斜めに置かれていても同じ答えになる():
    """外接矩形で測ると斜めのダクトは長さが化ける。面積と周長なら傾きに依らない。"""
    L, W = 6.0, 1.5
    a, p = L * W, 2 * (L + W)          # 面積と周長は回転しても変わらない
    assert dg.solve_rectangle(a, p) == dg.solve_rectangle(a, p)
    assert math.isclose(dg.solve_rectangle(a, p)[0], L, abs_tol=1e-6)


def test_長方形でない形は長さを出さない():
    """エルボ・分岐は長方形として解けない。数えられないものを数えたことにしない。"""
    assert dg.solve_rectangle(area=100.0, perimeter=10.0) is None


def test_塗りが系統色の白寄せかを判定する():
    orange = (1.0, 0.498, 0.0)          # 排気ダクトの色（実物）
    assert dg._light_of((1.0, 0.9, 0.8), orange)      # 実物の塗り
    assert not dg._light_of((0.83, 0.93, 0.8), orange)  # こちらは外気（緑）の塗り
    assert not dg._light_of(None, orange)


def test_無彩色は系統色として扱わない():
    """建築の灰色は設備の系統ではない。白との差が小さい色は判定しない。"""
    assert not dg._light_of((0.9, 0.9, 0.9), (0.87, 0.87, 0.87))


def test_ハッチングの帯をダクトとして数えない():
    """幅が閾値未満なら弾く。これが無いと1枚で183.8mの架空延長が出る。"""
    assert dg.MIN_WIDTH_MM >= 100.0
    # 幅28mm・長さ1440mm の帯（実測で128本あったもの）は通らない大きさ
    assert 28.0 < dg.MIN_WIDTH_MM


def test_展開面積は周長かける延長():
    r = dg.DuctRun(system="給気ダクト", length_m=8.2, width_mm=450,
                   plan_area_m2=3.7, x=0.2, y=0.3, size_w=1100, size_h=450)
    assert math.isclose(r.girth_m, 3.10, abs_tol=1e-9)     # 2×(1.1+0.45)
    assert math.isclose(r.sheet_area_m2, 25.42, abs_tol=0.01)  # ①の手書き 26m² と同じ拾い方


def test_丸ダクトの周長は円周():
    r = dg.DuctRun(system="排気ダクト", length_m=10.0, width_mm=250,
                   plan_area_m2=2.5, x=0.2, y=0.3, size_w=250, round_duct=True)
    assert math.isclose(r.girth_m, math.pi * 0.25, abs_tol=1e-9)


def test_呼び寸法が取れない区間は展開面積を出さない():
    """周長を推測で埋めると、そのまま金額の誤りになる。"""
    r = dg.DuctRun(system="還気ダクト", length_m=5.0, width_mm=300,
                   plan_area_m2=1.5, x=0.2, y=0.3)
    assert r.girth_m is None and r.sheet_area_m2 is None


def test_延長の行と展開面積の行を分けて出す():
    runs = [
        dg.DuctRun("給気ダクト", 4.0, 400, 1.6, 0.2, 0.3, size_w=400, size_h=300),
        dg.DuctRun("給気ダクト", 6.0, 400, 2.4, 0.4, 0.3, size_w=400, size_h=300),
        dg.DuctRun("給気ダクト", 2.0, 300, 0.6, 0.6, 0.3),   # 呼び寸法なし
    ]
    items = dg.to_items(runs, page_no=1)
    m = [i for i in items if i.unit == "m"]
    a = [i for i in items if i.unit == "m2"]
    assert sum(i.quantity for i in m) == 12.0
    assert len(a) == 1 and a[0].spec == "400×300"
    assert math.isclose(a[0].quantity, 2 * (0.4 + 0.3) * 10.0, abs_tol=0.05)
    assert all(i.qty_basis == "measure" and i.source == "duct_geometry" for i in items)
    # 呼び寸法が取れなかった区間は、そうと分かる書き方で残す
    assert any("呼び寸法は図面から取れず" in (i.spec or "") for i in m)
