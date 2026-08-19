"""タイル分割の判定を「モデルに届く実効解像度」で固定する回帰テスト。

2026-08-19 の実測で分かったこと:
  1. スキャン図を PNG で送ると A3 200dpi = 7.6MB。byte 上限に当たって 112dpi へ
     自動降格し、図面に印刷された表(12行22個)を 1 個も拾えなかった。
     同じ画を JPEG にすると 2.05MB で、降格が起きない。
  2. ただし JPEG にしただけでは足りない。Anthropic の画像仕様では長辺 1568px を
     超えると API 側で縮小されるため、A3 を丸ごと送る限り実効 ~95dpi にしかならない。
     → 分割の判定は「レンダリング DPI」ではなく「縮小後の実効 DPI」で行う必要がある。
  3. 正方分割(3x3=9回)は A3 横では無駄。2行3列=6回で各タイルが 1568px 以下に収まる。

🔴 1 を直して 2 を忘れると、used_dpi が 200 のままになりタイル分割が静かに止まる
（＝表の 22 個が再び 0 個に戻る）。本テストはその組み合わせ事故を止める。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff.pdf_loader import (  # noqa: E402
    AUTO_TILE_MIN_DPI,
    VISION_MAX_EDGE,
    effective_dpi,
    load_pdf,
    native_dpi,
    plan_tiles,
)

fitz = pytest.importorskip("fitz")
PIL = pytest.importorskip("PIL")

A3_W_PT, A3_H_PT = 1191.0, 842.0  # A3 横 (420x297mm)


def _make_a3_scan_pdf(path: Path, *, dpi: int = 200) -> Path:
    """A3 横に「テキスト層ゼロのスキャン画像」を貼った PDF を作る（実物と同じ形）。"""
    import io

    from PIL import Image, ImageDraw

    w = int(A3_W_PT / 72 * dpi)
    h = int(A3_H_PT / 72 * dpi)
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    # 図面らしい線とノイズ（真っ白だと JPEG が極端に小さくなり判定が実物とズレる）
    for i in range(0, w, 37):
        d.line([(i, 0), (i, h)], fill=(120, 120, 190), width=1)
    for j in range(0, h, 41):
        d.line([(0, j), (w, j)], fill=(190, 120, 120), width=1)
    for j in range(0, h, 7):
        d.line([(0, j), (w, j)], fill=(230, 230, 230), width=1)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88)

    doc = fitz.open()
    page = doc.new_page(width=A3_W_PT, height=A3_H_PT)
    page.insert_image(fitz.Rect(0, 0, A3_W_PT, A3_H_PT), stream=buf.getvalue())
    doc.save(path)
    doc.close()
    return path


# --------------------------------------------------------------------------
# 実効 DPI
# --------------------------------------------------------------------------
def test_effective_dpi_accounts_for_vision_downscale():
    """長辺 1568px を超えると、送信 DPI ではなく縮小後が実効値になる。"""
    # A3 200dpi 全面 = 3310x2340 → 1568/3310 に縮む
    assert effective_dpi(3310, 2340, 200) == pytest.approx(200 * 1568 / 3310, rel=1e-6)
    assert effective_dpi(3310, 2340, 200) < AUTO_TILE_MIN_DPI  # ← 分割が要る側


def test_effective_dpi_unchanged_when_within_cap():
    """1568px 以下なら縮小されない＝送った DPI がそのまま届く。"""
    assert effective_dpi(1103, 1170, 200) == 200
    assert effective_dpi(1568, 1000, 300) == 300


def test_raising_render_dpi_alone_does_not_help():
    """『300dpi でレンダリングすれば読める』が誤りであることを固定する。

    A3 を丸ごと送る限り、200dpi でも 300dpi でも実効値は同じ ~95dpi に落ちる。
    """
    at200 = effective_dpi(3310, 2340, 200)
    at300 = effective_dpi(4966, 3510, 300)
    assert at200 == pytest.approx(at300, rel=0.01)
    assert at300 < AUTO_TILE_MIN_DPI


# --------------------------------------------------------------------------
# 分割の形（正方にしない）
# --------------------------------------------------------------------------
def test_plan_tiles_follows_paper_aspect_not_square():
    """A3 横 200dpi は 2行3列。3x3=9回ではなく 6回で足りる。"""
    assert plan_tiles(3310, 2340) == (2, 3)


def test_plan_tiles_keeps_every_tile_within_vision_cap():
    """各タイルが 1568px 以下＝API 側で再縮小されないこと。"""
    rows, cols = plan_tiles(3310, 2340)
    assert 3310 / cols <= VISION_MAX_EDGE
    assert 2340 / rows <= VISION_MAX_EDGE


def test_plan_tiles_no_split_when_small_enough():
    assert plan_tiles(1200, 900) == (1, 1)


def test_plan_tiles_respects_call_budget():
    """呼び出し回数の上限を超えない（サーバレスの実行時間を守る）。"""
    rows, cols = plan_tiles(9000, 9000, max_calls=9)
    assert rows * cols <= 9
    rows, cols = plan_tiles(3310, 2340, max_calls=9, page_count=3)
    assert rows * cols <= 3


# --------------------------------------------------------------------------
# 実 PDF での通し（合成 A3 スキャン）
# --------------------------------------------------------------------------
def test_native_dpi_detects_embedded_scan_resolution(tmp_path):
    pdf = _make_a3_scan_pdf(tmp_path / "a3.pdf", dpi=200)
    doc = fitz.open(pdf)
    assert native_dpi(doc[0]) == pytest.approx(200, abs=2)
    doc.close()


def test_a3_scan_is_sent_as_jpeg_without_dpi_downgrade(tmp_path):
    """🔴 スキャンは JPEG で送る。PNG だと byte 上限で解像度が落ちる。"""
    pdf = _make_a3_scan_pdf(tmp_path / "a3.pdf", dpi=200)
    page = load_pdf(pdf).pages[0]
    assert page.image_png[:3] == b"\xff\xd8\xff", "スキャンは JPEG で送られること"
    # 200dpi のまま = 降格していない（旧実装は 112dpi まで落ちていた）
    assert page.width == pytest.approx(3310, abs=8)


def test_a3_scan_is_auto_tiled_2x3_with_overlap(tmp_path):
    """A3 スキャンは指定なしで 2行3列に割れ、各タイルに重なりが付くこと。"""
    pdf = _make_a3_scan_pdf(tmp_path / "a3.pdf", dpi=200)
    page = load_pdf(pdf).pages[0]
    assert len(page.tiles) == 6
    assert {(t.row, t.col) for t in page.tiles} == {(r, c) for r in range(2) for c in range(3)}
    for t in page.tiles:
        assert (t.n_rows, t.n_cols) == (2, 3)
        assert max(t.width, t.height) <= VISION_MAX_EDGE, "タイルは縮小されない大きさ"
        assert t.image_png[:3] == b"\xff\xd8\xff"
        # 重なりを付けた分、担当領域は画像全体より狭い
        assert t.core != [0.0, 0.0, 1.0, 1.0]
        assert 0.0 <= t.core[0] < t.core[2] <= 1.0
        assert 0.0 <= t.core[1] < t.core[3] <= 1.0


def test_tile_core_regions_tile_the_page_without_gaps(tmp_path):
    """担当領域を並べるとページ全体を隙間なく覆う（＝拾い落とす帯が無い）。"""
    pdf = _make_a3_scan_pdf(tmp_path / "a3.pdf", dpi=200)
    page = load_pdf(pdf).pages[0]
    covered = 0.0
    for t in page.tiles:
        # 担当領域の実寸(pt)換算: タイル画像の実寸 × core の割合
        covered += (t.core[2] - t.core[0]) * t.width * (t.core[3] - t.core[1]) * t.height
    full = page.width * page.height
    assert covered == pytest.approx(full, rel=0.02)


def test_explicit_grid_still_forces_square_split(tmp_path):
    """呼び出し側が grid を明示したときは従来どおり正方分割（後方互換）。"""
    pdf = _make_a3_scan_pdf(tmp_path / "a3.pdf", dpi=200)
    page = load_pdf(pdf, grid=3).pages[0]
    assert len(page.tiles) == 9
