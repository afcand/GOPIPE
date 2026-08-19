"""記号テンプレートマッチング（CV・段階2, numpy NCC）のテスト。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff.symbol_match import count_matches, find_matches  # noqa: E402


def _plus(size: int = 9) -> np.ndarray:
    """十字（プラス）記号テンプレート。"""
    t = np.zeros((size, size), float)
    c = size // 2
    t[c - 1:c + 2, :] = 255.0
    t[:, c - 1:c + 2] = 255.0
    return t


def _canvas(template, positions, shape=(90, 200)) -> np.ndarray:
    img = np.zeros(shape, float)
    h, w = template.shape
    for (y, x) in positions:
        img[y:y + h, x:x + w] = template
    return img


def test_find_matches_counts_distinct_symbols():
    t = _plus(9)
    pos = [(10, 10), (10, 70), (10, 140), (50, 40)]
    matches = find_matches(_canvas(t, pos), t, threshold=0.9)
    assert len(matches) == 4                     # NMS で各記号は1件に集約
    assert all(m.score > 0.99 for m in matches)  # 完全一致位置は NCC≈1.0


def test_count_matches_equals_placed():
    t = _plus(9)
    assert count_matches(_canvas(t, [(10, 10), (40, 90)]), t, threshold=0.9) == 2


def test_blank_image_returns_zero():
    t = _plus(9)
    assert count_matches(np.zeros((60, 60), float), t, threshold=0.9) == 0


# --------------------------------------------------------------------------
# FFT 版 NCC（opencv 無しで大判図面を扱えること）
# --------------------------------------------------------------------------
def test_fft_ncc_handles_large_page_without_blowing_memory():
    """A3 200dpi 相当の広さでも走ること。

    素朴なスライディングウィンドウ版だと中間配列が数千万×数千要素になり、
    実図面では落ちる（＝このモジュールが実務で一度も動かない）。
    """
    from gopipe_takeoff.symbol_match import _ncc_fft

    rng = np.random.default_rng(0)
    img = rng.random((2340, 3310)) * 40 + 200          # 白っぽい図面
    t = _plus()                                       # 20x20 の記号
    img[900:900 + t.shape[0], 1500:1500 + t.shape[1]] = t
    m = _ncc_fft(img, t)
    assert m.shape == (2340 - t.shape[0] + 1, 3310 - t.shape[1] + 1)
    y, x = np.unravel_index(np.argmax(m), m.shape)
    assert (y, x) == (900, 1500)
    assert m[y, x] > 0.99


def test_fft_ncc_matches_reference_implementation():
    """FFT 版が素朴版と同じ値を返すこと（小さい画で突き合わせる）。"""
    from gopipe_takeoff.symbol_match import _ncc_fft

    t = _plus()
    img = _canvas(t, [(10, 10), (40, 60)])
    fft = _ncc_fft(img.astype(np.float64), t.astype(np.float64))
    th, tw = t.shape
    win = np.lib.stride_tricks.sliding_window_view(img.astype(np.float64), (th, tw))
    w0 = win - win.mean(axis=(2, 3), keepdims=True)
    t0 = t.astype(np.float64) - t.mean()
    num = (w0 * t0).sum(axis=(2, 3))
    den = np.sqrt((w0 ** 2).sum(axis=(2, 3)) * float((t0 ** 2).sum()))
    ref = np.where(den > 0, num / den, 0.0)
    assert np.allclose(fft, ref, atol=1e-6)


# --------------------------------------------------------------------------
# 「踊り場」判定 — 数えられていないときに数を出さない
# --------------------------------------------------------------------------
def test_count_stable_returns_number_when_plateau_exists():
    from gopipe_takeoff.symbol_match import count_stable

    t = _plus()
    n, counts = count_stable(_canvas(t, [(10, 10), (40, 60), (60, 120)]), t)
    assert n == 3, counts


def test_count_stable_returns_none_without_plateau():
    """しきい値ごとに件数が動くなら「数えられなかった」と返すこと。"""
    from gopipe_takeoff.symbol_match import count_stable

    t = _plus()
    n, counts = count_stable(np.full((60, 60), 255.0), t)
    assert n is None
    assert set(counts) and all(v == 0 for v in counts.values())
