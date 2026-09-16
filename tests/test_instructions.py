"""指した指示を次の図面でも効かせる仕組み。

指示がその場限りで消えると、同じ様式の紙が来るたびに囲み直しになる。
「この色は◯◯として数える」が数量の行になるところまでを固定する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "shared", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from gopipe_takeoff.instructions import (  # noqa: E402
    ColorRule, apply_skips, color_items, sheet_key_of,
)
from gopipe_takeoff.models import TakeoffItem  # noqa: E402

fitz = pytest.importorskip("fitz")


def _page(tmp_path: Path):
    doc = fitz.open()
    pg = doc.new_page(width=400, height=400)
    for i in range(4):
        pg.draw_line(fitz.Point(20, 30 + i * 20), fitz.Point(380, 30 + i * 20), color=(1, 0, 0))
    pg.draw_line(fitz.Point(20, 300), fitz.Point(380, 300), color=(0, 0, 1))
    out = tmp_path / "c.pdf"
    doc.save(out)
    doc.close()
    return fitz.open(out)[0]


def test_color_rule_counts(tmp_path):
    pg = _page(tmp_path)
    rules = [ColorRule(hex="#ff0000", name="還気ダクト", action="count", unit="本")]
    items = color_items(pg, rules, page_no=1)
    assert len(items) == 1
    assert items[0].name == "還気ダクト" and items[0].quantity == 4 and items[0].unit == "本"
    assert items[0].qty_basis == "count", "色の指示で数えた、と出所が分かること"


def test_color_rule_measures_length(tmp_path):
    pg = _page(tmp_path)
    items = color_items(pg, [ColorRule(hex="#ff0000", name="還気ダクト", action="measure", unit="m")],
                        page_no=1, scale=1.0)
    assert items[0].unit == "m" and items[0].quantity > 0


def test_skip_rule_makes_no_row(tmp_path):
    pg = _page(tmp_path)
    assert color_items(pg, [ColorRule(hex="#ff0000", name="通り芯", action="skip")], page_no=1) == []


def test_skip_removes_existing_rows_and_says_how_many():
    rows = [
        TakeoffItem(page=1, name="A", quantity=1, unit="個", spec="色 #ff0000"),
        TakeoffItem(page=1, name="B", quantity=1, unit="個", spec="色 #0000ff"),
    ]
    kept, dropped = apply_skips(rows, [ColorRule(hex="#ff0000", name="x", action="skip")])
    assert [i.name for i in kept] == ["B"] and dropped == 1


def test_color_rule_tolerates_a_slightly_different_color(tmp_path):
    """書き出しやスキャンで色はぶれる。近ければ同じ色として扱う。"""
    pg = _page(tmp_path)
    items = color_items(pg, [ColorRule(hex="#f50505", name="還気", action="count")], page_no=1)
    assert items and items[0].quantity == 4


def test_sheet_key_is_empty_when_there_is_little_to_go_on():
    """鍵が弱いときは空にして『全図面向け』に倒す（取り違えるより安全）。"""
    assert sheet_key_of(None, ["A", "B"]) == ""
    k1 = sheet_key_of(None, ["M-001", "空調ダクト図", "1階", "NEC", "S=1/50", "2026"])
    k2 = sheet_key_of(None, ["2026", "S=1/50", "NEC", "1階", "空調ダクト図", "M-001"])
    assert k1 and k1 == k2, "同じ図枠なら、並び順が違っても同じ鍵になる"


def test_rule_from_payload_defaults_are_safe():
    r = ColorRule.from_payload({"hex": "#AABBCC", "name": "外気"})
    assert r and r.action == "count" and r.unit == "個"
    assert ColorRule.from_payload({"hex": "あお"}) is None, "色として読めないものは覚えない"


def test_sheet_key_reads_frame_report_shape():
    """🔴 図枠の入れ物（FrameReport）はそのまま回せない。

    `for f in frame` と書いて 'FrameReport' object is not iterable で落ち、
    覚えた指示が黙って当たらなくなっていた（拾い出しは続くので気づけない）。
    図枠の文字は keys の2番目に入っている。
    """
    from gopipe_takeoff.frame_filter import FrameReport

    rep = FrameReport(keys={("duct", "M-001", 0.1, 0.2), ("duct", "空調ダクト図", 0.3, 0.4),
                            ("duct", "1階", 0.5, 0.6), ("duct", "NEC", 0.7, 0.8),
                            ("duct", "S=1/50", 0.9, 0.1), ("duct", "2026", 0.2, 0.3)})
    texts = [k[1] for k in rep.keys]
    assert sheet_key_of(None, texts), "図枠の文字から鍵が作れること"
