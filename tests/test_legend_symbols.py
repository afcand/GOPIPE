"""凡例の図形を手本に、ベクター図の記号を数えるテスト。

固定したいこと（2026-09-08 NEC府中の実図面で分かったこと）:
  ・記号を数えるのは決定的にできる（実測: 耐震振れ止め支持点 291個）。
    画像のテンプレートマッチと違い、同じ図面なら何度かけても同じ数になる。
  ・🔴 凡例と平面図で描き方が違う記号がある。VDは凡例に схема が描いてあるが、
    平面図ではダクト幅に合わせて伸びる簡略な形で、手本まるごとの照合は当たらない。
    だから「図の中で同じ形が繰り返し出るか」で数え、名前だけを凡例から借りる。
  ・🔴 名前が引けない形を勝手に名づけない（通り芯の丸・柱・網掛けも繰り返し出る）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff import legend_symbols as ls  # noqa: E402


def _circle(page, x, y, r=5.0, color=(1, 0, 0)):
    page.draw_circle(fitz.Point(x, y), r, color=color, width=0.6)


def _doc(n_plan: int = 5):
    """凡例に円の見本＋名前、図の中に同じ円を n_plan 個置いた紙を作る。"""
    doc = fitz.open()
    page = doc.new_page(width=800, height=600)
    for i in range(n_plan):                       # 図の中（左上寄り）
        _circle(page, 80 + i * 40, 120)
    _circle(page, 640, 520)                       # 凡例の見本
    page.insert_text((660, 524), "shien", fontsize=7)
    return doc, page


def test_同じ形の繰り返しを数える():
    doc, page = _doc(5)
    inside = lambda x, y: x < 600            # noqa: E731  右側は図枠とみなす
    glyphs = ls.count_glyphs(page.get_drawings(), inside=inside)
    doc.close()
    assert glyphs and glyphs[0].count == 5   # 凡例の見本は図枠側なので数に入らない


def test_凡例から名前を借りる():
    doc, page = _doc(4)
    inside = lambda x, y: x < 600            # noqa: E731
    cells = ls.legend_cells(page, inside=inside)
    assert any(name == "shien" and cell for name, cell in cells)
    glyphs = ls.count_glyphs(page.get_drawings(), inside=inside)
    ls.name_from_legend(glyphs, cells)
    named = [g for g in glyphs if g.names]
    doc.close()
    assert named and named[0].names == ["shien"] and named[0].count == 4


def test_名前が引けない形は名づけない():
    doc = fitz.open()
    page = doc.new_page(width=800, height=600)
    for i in range(6):
        _circle(page, 80 + i * 40, 120)      # 図の中だけ。凡例に見本が無い
    inside = lambda x, y: x < 600            # noqa: E731
    items, unnamed = ls.glyph_items(page, 1, inside=inside)
    doc.close()
    assert items == []
    assert unnamed and unnamed[0].count == 6


def test_表の見出しは凡例の名前にしない():
    """『記号』『名称』のような見出しを記号名にすると、表の飾りを部材として数え始める。"""
    doc = fitz.open()
    page = doc.new_page(width=800, height=600)
    _circle(page, 640, 520)
    page.insert_text((660, 524), "記号", fontsize=7)
    cells = ls.legend_cells(page, inside=lambda x, y: x < 600)
    doc.close()
    assert not any(name == "記号" for name, _ in cells)


def test_通り芯やレベルの文字は凡例の名前にしない():
    """図の中の通り芯・レベル・寸法を記号名にすると、丸を部材として数え始める。"""
    doc = fitz.open()
    page = doc.new_page(width=800, height=600)
    for i, t in enumerate(("Y3", "2FL+500", "1FL+5,350", "300")):
        _circle(page, 640, 400 + i * 40)
        page.insert_text((660, 404 + i * 40), t, fontsize=7)
    cells = ls.legend_cells(page, inside=lambda x, y: x < 600)
    doc.close()
    assert cells == []


def test_レベル注記は飾りが付いていても弾く():
    """実図面の『▼2FL』のように前に記号が付く。フォントによっては別の字に化ける。"""
    for t in ("▼2FL", "·2FL", "▼1FL+5,400", "FL+250"):
        assert ls._NOT_A_NAME.match(t) or ls._LEVEL_NOTE.search(t), t


def test_数えた結果は何度やっても同じ():
    doc, page = _doc(7)
    inside = lambda x, y: x < 600            # noqa: E731
    a = ls.count_glyphs(page.get_drawings(), inside=inside)
    b = ls.count_glyphs(page.get_drawings(), inside=inside)
    doc.close()
    assert [(g.key, g.count) for g in a] == [(g.key, g.count) for g in b]


def test_図形を数えた行の備考は要数え直しにしない():
    """『要数え直し』は画像認識の計数への注意書き。図形の計数は決定的なので付けない。"""
    from gopipe_takeoff.excel_writer import _note
    from gopipe_takeoff.models import TakeoffItem

    it = TakeoffItem(page=1, name="振れ止め支持点", quantity=11, unit="個",
                     source="glyph_count", qty_basis="count", confidence=0.75)
    note = _note(it)
    assert "要数え直し" not in note
    assert "凡例の図形と一致する記号を機械で数えた" in note
