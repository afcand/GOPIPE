"""線の追跡（延長を幾何から出す土台）の回帰テスト。

🔴ダクトは2本の輪郭線で描かれる。輪郭のまま測ると長さが2倍になる。
細線化して芯を取ってから測る、が要。ここを壊すと延長が静かに倍になる。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

np = pytest.importorskip("numpy")
PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from gopipe_takeoff.trace import (  # noqa: E402
    _components,
    _length_px,
    _simplify,
    _skeleton_to_paths,
    _thin,
    trace_color,
)

BLUE = (31, 84, 135)


def _png(draw, size=(400, 300)) -> bytes:
    im = Image.new("RGB", size, (255, 255, 255))
    for (x, y, c) in draw:
        if 0 <= x < size[0] and 0 <= y < size[1]:
            im.putpixel((x, y), c)
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def _hline(y, x0, x1, w=6, c=BLUE):
    return [(x, y + j, c) for x in range(x0, x1) for j in range(w)]


def _vline(x, y0, y1, w=6, c=BLUE):
    return [(x + i, y, c) for y in range(y0, y1) for i in range(w)]


# --------------------------------------------------------------------------
# 部品
# --------------------------------------------------------------------------
def test_thinning_reduces_a_thick_bar_to_one_pixel_spine():
    m = np.zeros((40, 120), bool)
    m[15:25, 10:110] = True          # 幅10px・長さ100px の帯
    sk = _thin(m)
    assert sk.sum() < m.sum() / 5, "芯線は元より十分細いこと"
    ys = np.nonzero(sk.any(axis=1))[0]
    assert len(ys) <= 3, f"芯は1〜数px の高さに収まること（実際 {len(ys)}）"


def test_components_split_disconnected_shapes():
    m = np.zeros((60, 200), bool)
    m[10:20, 10:60] = True
    m[40:50, 120:180] = True
    comps = _components(m, min_px=100)
    assert len(comps) == 2


def test_components_drop_tiny_specks():
    m = np.zeros((60, 60), bool)
    m[5:8, 5:8] = True               # 9px のごみ
    assert _components(m, min_px=100) == []


def test_simplify_keeps_the_corner():
    path = [(0, i) for i in range(30)] + [(j, 29) for j in range(1, 30)]
    sp = _simplify(path, eps=2.0)
    assert 3 <= len(sp) <= 5, f"直線2本＝端点と角で足りる（実際 {len(sp)}）"


def test_length_of_a_right_angle_is_the_sum_of_legs():
    assert _length_px([(0, 0), (0, 30), (40, 30)]) == pytest.approx(70.0, abs=0.01)


def test_skeleton_splits_at_a_junction():
    """T字は3本の区間に切れる（分岐で切らないと長さが混ざる）。"""
    pts = [(20, x) for x in range(10, 60)] + [(y, 35) for y in range(21, 50)]
    paths = _skeleton_to_paths(pts)
    assert len(paths) == 3, f"T字は3本（実際 {len(paths)}）"


# --------------------------------------------------------------------------
# 通し
# --------------------------------------------------------------------------
def test_traces_a_straight_run_and_measures_it():
    png = _png(_hline(100, 40, 340, w=8))
    runs = trace_color(png, 196, 230)
    assert runs, "1本の線が取れること"
    longest = max(runs, key=lambda r: r["length_px"])
    # 実長 300px。細線化と間引きの誤差を見て ±12%
    assert 264 <= longest["length_px"] <= 336, longest["length_px"]


def test_thick_line_is_not_measured_twice():
    """🔴幅のある線を輪郭のまま測ると倍になる。芯で測れていることを固定する。"""
    thin_png = _png(_hline(100, 40, 340, w=4))
    thick_png = _png(_hline(100, 40, 340, w=16))
    a = max(trace_color(thin_png, 196, 230), key=lambda r: r["length_px"])["length_px"]
    b = max(trace_color(thick_png, 196, 230), key=lambda r: r["length_px"])["length_px"]
    assert abs(a - b) / a < 0.2, f"太さで長さが変わらないこと（細{a} / 太{b}）"


def test_length_in_metres_uses_the_scale():
    png = _png(_hline(100, 40, 340, w=8))
    runs = trace_color(png, 196, 230, mm_per_px=12.7)  # 200dpi × 1/100
    longest = max(runs, key=lambda r: r["length_px"])
    assert longest["length_m"] == pytest.approx(longest["length_px"] * 12.7 / 1000, abs=0.01)


def test_other_colours_are_ignored():
    png = _png(_hline(100, 40, 340, w=8, c=(183, 120, 60)))  # 橙
    assert trace_color(png, 196, 230) == [], "青の窓では橙を拾わない"


def test_black_lines_are_ignored():
    png = _png(_hline(100, 40, 340, w=8, c=(20, 20, 20)))
    assert trace_color(png, 196, 230) == []


def test_gap_from_overlapping_text_is_closed():
    """文字が重なって空いた穴で線が切れないこと（切れると長さが半分になる）。"""
    px = _hline(100, 40, 340, w=8)
    px = [(x, y, c) for (x, y, c) in px if not (185 <= x <= 191)]  # 7px の穴
    runs = trace_color(_png(px), 196, 230)
    longest = max(runs, key=lambda r: r["length_px"])
    assert longest["length_px"] > 240, f"穴で分断されないこと（実際 {longest['length_px']}）"
