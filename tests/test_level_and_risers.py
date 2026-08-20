"""取付高さ（Z軸）と、立ち上がり・立下りの未計上検出の回帰テスト。

🔴 平面図では垂直の区間が点にしか見えず、延長が丸ごと落ちる。
実測(2026-08-20 資料②): 高さが 2,850mm にわたって12段あり、16要素すべてが 0m
＝垂直の配管・ダクトが1本も数えられていなかった。

ここで固定するのは2つ:
  1. 図面の書き方のゆれ（FL+3,065 / FL+2.830 / FL＋2700）を正しく mm にする
  2. 🔴**数量を勝手に作らない**。「跨いでいる」事実だけを出し、長さは人に残す
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff.extractor import _item_key, parse_level_mm  # noqa: E402
from gopipe_takeoff.models import TakeoffItem  # noqa: E402
from gopipe_takeoff.validate_items import check, find_missing_risers  # noqa: E402


def _duct(level, qty=0.0, name="ダクト", cat="ダクト", unit="m"):
    return TakeoffItem(page=1, name=name, quantity=qty, unit=unit,
                       category=cat, level_mm=level)


# --------------------------------------------------------------------------
# 書き方のゆれを吸収する
# --------------------------------------------------------------------------
def test_parses_comma_and_period_as_thousands():
    """ピリオドは小数点でなく桁区切りのことがある（実測: 資料④の FL+2.830）。"""
    assert parse_level_mm("3,065") == 3065
    assert parse_level_mm("2.830") == 2830
    assert parse_level_mm(2700) == 2700


def test_falls_back_to_fl_notation_in_text():
    """AIが専用の欄を返さなくても、場所や名称に紛れた FL 表記から拾う。"""
    assert parse_level_mm(None, "外気取入 FL+2,700") == 2700
    assert parse_level_mm(None, None, "ダクト FL＋3060") == 3060


def test_metre_notation_becomes_mm():
    assert parse_level_mm(2.83) == 2830


def test_out_of_range_is_rejected():
    """桁を読み違えた高さを通さない（立下りの長さが桁で狂う）。"""
    assert parse_level_mm(99) is None
    assert parse_level_mm(999999) is None
    assert parse_level_mm(None, "高さの記載なし") is None


# --------------------------------------------------------------------------
# 高さが違えば別の区間として残す
# --------------------------------------------------------------------------
def test_different_levels_are_different_rows():
    """まとめると、その間の立ち上がり・立下りが見えなくなる。"""
    a, b = _duct(2700), _duct(5300)
    assert _item_key(a) != _item_key(b)


def test_same_level_still_merges():
    assert _item_key(_duct(2700)) == _item_key(_duct(2700))


# --------------------------------------------------------------------------
# 🔴 数量を作らない。跨いでいる事実だけを出す
# --------------------------------------------------------------------------
def test_detects_span_across_levels():
    items = [_duct(2700), _duct(5300)]
    got = find_missing_risers(items)
    assert len(got) == 1
    assert got[0]["levels"] == [2700, 5300]
    assert got[0]["drop_mm"] == 2600


def test_does_not_invent_quantity():
    """🔴長さを埋めない。埋めた瞬間に「もっともらしい嘘の数字」になる。"""
    items = [_duct(2700), _duct(5300)]
    find_missing_risers(items)
    assert all(it.quantity == 0 for it in items), "数量には触らない"
    assert len(items) == 2, "行を足さない"


def test_small_difference_is_not_a_riser():
    """施工誤差や表記ゆれの範囲では言わない（誤報は本物を埋もれさせる）。"""
    assert find_missing_risers([_duct(2700), _duct(2850)]) == []


def test_existing_riser_row_is_not_double_reported():
    items = [_duct(2700), _duct(5300, name="ダクト立下り")]
    # 立下りの行が既にあるので、同じ名前の群では言わない
    names = {r["name"] for r in find_missing_risers(items)}
    assert "ダクト立下り" not in names


def test_single_level_says_nothing():
    assert find_missing_risers([_duct(2700), _duct(2700)]) == []


def test_count_items_are_out_of_scope():
    """個数モノは長さを持たないので対象外（吹出口が高さ違いで並ぶのは普通）。"""
    items = [_duct(2700, unit="個", cat="ダクト"), _duct(5300, unit="個", cat="ダクト")]
    assert find_missing_risers(items) == []


def test_check_marks_the_rows():
    """人が気づけるよう、その系統の行に印が付くこと。"""
    items = [_duct(2700), _duct(5300)]
    flags = check(items)
    assert all(any("立上り/立下り" in s for s in flags[i]) for i in (0, 1))
    assert "高低差2600mm" in " ".join(flags[0])
