"""2Dの経路＋取付高さ → 3Dの区間 の回帰テスト。

この層は**推測を含む**（高さの注記がどの区間に効くかは図面に書かれていない）。
だから固定するのは精度でなく「推測が実測の顔をしないこと」:
  - 近くに注記が無い区間には高さを与えない
  - 高さ不明ぶんは合計に混ぜず、別勘定で返す
  - 垂直区間は端点が近く高さが違うときだけ立てる
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff.space3d import assemble, assign_levels, find_risers  # noqa: E402


def _run(points):
    return {"points": points, "length_px": 0.0}


def _anchor(x, y, z):
    return {"x": x, "y": y, "level_mm": z}


# --------------------------------------------------------------------------
# 高さの割り当て — 近くに注記が無ければ与えない
# --------------------------------------------------------------------------
def test_nearby_annotation_is_applied():
    runs = [_run([(100, 100), (300, 100)])]
    got = assign_levels(runs, [_anchor(110, 130, 2700)], radius_px=120)
    assert got[0]["level_mm"] == 2700


def test_far_annotation_is_not_applied():
    """🔴離れた注記を拾わない。隣の系統の高さを当てると長さが桁で狂う。"""
    runs = [_run([(100, 100), (300, 100)])]
    got = assign_levels(runs, [_anchor(1500, 1500, 2700)], radius_px=120)
    assert got[0]["level_mm"] is None


def test_nearest_annotation_wins():
    runs = [_run([(100, 100), (300, 100)])]
    got = assign_levels(runs, [_anchor(400, 100, 5300), _anchor(310, 100, 2700)], radius_px=200)
    assert got[0]["level_mm"] == 2700


def test_no_annotations_leaves_everything_unknown():
    got = assign_levels([_run([(0, 0), (50, 0)])], [])
    assert got[0]["level_mm"] is None


# --------------------------------------------------------------------------
# 垂直区間
# --------------------------------------------------------------------------
def _leveled(points, z):
    return {"points": points, "level_mm": z}


def test_riser_between_connected_runs_at_different_heights():
    runs = [_leveled([(100, 100), (300, 100)], 2700),
            _leveled([(302, 101), (500, 101)], 5300)]
    rs = find_risers(runs)
    assert len(rs) == 1
    assert rs[0]["length_m"] == pytest.approx(2.6, abs=0.01)
    assert rs[0]["from_mm"] == 2700 and rs[0]["to_mm"] == 5300


def test_no_riser_when_endpoints_are_far_apart():
    runs = [_leveled([(100, 100), (300, 100)], 2700),
            _leveled([(900, 900), (1100, 900)], 5300)]
    assert find_risers(runs) == []


def test_no_riser_for_tiny_height_difference():
    """施工誤差・表記ゆれの範囲では立てない。"""
    runs = [_leveled([(100, 100), (300, 100)], 2700),
            _leveled([(302, 100), (500, 100)], 2850)]
    assert find_risers(runs) == []


def test_runs_without_height_make_no_riser():
    runs = [_leveled([(100, 100), (300, 100)], None),
            _leveled([(302, 100), (500, 100)], 5300)]
    assert find_risers(runs) == []


# --------------------------------------------------------------------------
# 🔴 内訳に割る — 推測を実測に混ぜない
# --------------------------------------------------------------------------
def test_unknown_height_is_kept_out_of_the_total():
    """高さ不明ぶんを合計に混ぜない。混ぜると、どこまで確かかが消える。"""
    runs = [_run([(100, 100), (200, 100)]),      # 注記あり
            _run([(2000, 2000), (2100, 2000)])]  # 注記なし
    got = assemble(runs, [_anchor(105, 105, 2700)], mm_per_px=10.0)
    assert got["horizontal_m"] == pytest.approx(1.0, abs=0.01)
    assert got["unassigned_m"] == pytest.approx(1.0, abs=0.01)
    assert got["total_m"] == pytest.approx(1.0, abs=0.01), "合計に高さ不明は入れない"
    assert got["runs_unleveled"] == 1


def test_vertical_length_is_added_to_the_total():
    runs = [_run([(100, 100), (300, 100)]), _run([(302, 100), (500, 100)])]
    anchors = [_anchor(100, 100, 2700), _anchor(500, 100, 5300)]
    got = assemble(runs, anchors, mm_per_px=10.0, radius_px=60)
    assert got["vertical_m"] == pytest.approx(2.6, abs=0.01)
    assert got["total_m"] == pytest.approx(got["horizontal_m"] + got["vertical_m"], abs=0.01)


def test_segments_carry_real_world_coordinates():
    runs = [_run([(0, 0), (100, 0)])]
    got = assemble(runs, [_anchor(0, 0, 2700)], mm_per_px=10.0)
    seg = got["segments"][0]
    assert seg["a"] == [0.0, 0.0, 2700] and seg["b"] == [1000.0, 0.0, 2700]
    assert seg["kind"] == "h"


def test_empty_input_is_zero_not_an_error():
    got = assemble([], [], mm_per_px=6.0)
    assert got["total_m"] == 0 and got["segments"] == []


# --------------------------------------------------------------------------
# 高さの伝播 — 注記は要所にしか無い
# --------------------------------------------------------------------------
def test_level_propagates_along_connected_runs():
    """注記のある区間から、繋がった先へ高さが伝わること。"""
    from gopipe_takeoff.space3d import propagate_levels

    runs = [_leveled([(0, 0), (100, 0)], 2700),
            _leveled([(101, 0), (200, 0)], None),
            _leveled([(201, 0), (300, 0)], None)]
    got = propagate_levels(runs)
    assert [r["level_mm"] for r in got] == [2700, 2700, 2700]
    assert [r["level_src"] for r in got] == ["annotated", "propagated", "propagated"]


def test_propagation_stops_at_the_hop_limit():
    """🔴広げすぎない。図面の端まで伝わると別の高さの系統を塗り潰す。"""
    from gopipe_takeoff.space3d import propagate_levels

    runs = [_leveled([(0, 0), (100, 0)], 2700)]
    runs += [_leveled([(100 * i + 1, 0), (100 * (i + 1), 0)], None) for i in range(1, 10)]
    got = propagate_levels(runs, max_hops=2)
    assert got[1]["level_mm"] == 2700 and got[2]["level_mm"] == 2700
    assert got[5]["level_mm"] is None, "6区間先までは塗らない"


def test_propagation_does_not_overwrite_an_annotation():
    from gopipe_takeoff.space3d import propagate_levels

    runs = [_leveled([(0, 0), (100, 0)], 2700), _leveled([(101, 0), (200, 0)], 5300)]
    got = propagate_levels(runs)
    assert [r["level_mm"] for r in got] == [2700, 5300], "注記を伝播で上書きしない"
    assert find_risers(got), "高さが違うまま繋がる＝そこが立上り/立下り"


def test_disconnected_runs_stay_unknown():
    from gopipe_takeoff.space3d import propagate_levels

    runs = [_leveled([(0, 0), (100, 0)], 2700), _leveled([(9000, 9000), (9100, 9000)], None)]
    got = propagate_levels(runs)
    assert got[1]["level_mm"] is None


def test_same_riser_is_not_counted_many_times():
    """🔴1本の立管には上下で複数の枝が付く。区間の組ごとに数えると同じ1本が何本にもなる
    （実測: 資料②で FL+2700→5300 の同じ立下りが47本に化けた）。"""
    runs = [_leveled([(100, 100), (300, 100)], 2700),
            _leveled([(100, 120), (300, 120)], 2700),
            _leveled([(100, 140), (300, 140)], 2700),
            _leveled([(302, 101), (500, 101)], 5300),
            _leveled([(302, 121), (500, 121)], 5300)]
    rs = find_risers(runs)
    assert len(rs) == 1, f"同じ場所の立管は1本（実際 {len(rs)}）"
    assert rs[0]["length_m"] == pytest.approx(2.6, abs=0.01)
    assert rs[0]["branches"] >= 2, "何本の枝が取り付いたかは残す"


def test_distant_risers_stay_separate():
    runs = [_leveled([(100, 100), (300, 100)], 2700),
            _leveled([(302, 100), (500, 100)], 5300),
            _leveled([(3000, 3000), (3200, 3000)], 2700),
            _leveled([(3202, 3000), (3400, 3000)], 5300)]
    assert len(find_risers(runs)) == 2, "離れた立管は別々に数える"
