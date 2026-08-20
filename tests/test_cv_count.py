"""CV検算層（cv_count）の回帰テスト。

固定する規則:
  - 機械の数 == AIの数 → 一致として確度を上げる
  - 機械の数 >  AIの数 → 採用（AIの見落とし。旧値は qty_vision へ）
  - 機械の数 <  AIの数 → 🔴上書きしない（線と重なった記号はNCCが拾えない実測があるため）
  - 踊り場が無い → qty_cv を付けない（数えられなかったものに数字を出さない）
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from PIL import Image, ImageDraw  # noqa: E402

from llm_client.base import LLMResponse  # noqa: E402

from gopipe_takeoff.cv_count import crop_template, recount  # noqa: E402
from gopipe_takeoff.extractor import _item_key, _shape_key, extract  # noqa: E402
from gopipe_takeoff.models import Drawing, DrawingPage, TakeoffItem, Tile  # noqa: E402


def _symbol_page(positions, *, size=(760, 560), sym=36) -> tuple[bytes, tuple]:
    """白地に「□の中に×」の記号スタンプを置いたページPNGと、最初の記号のbboxを返す。"""
    im = Image.new("L", size, 255)
    d = ImageDraw.Draw(im)
    for (x, y) in positions:
        d.rectangle([x, y, x + sym, y + sym], outline=0, width=3)
        d.line([x, y, x + sym, y + sym], fill=0, width=3)
        d.line([x + sym, y, x, y + sym], fill=0, width=3)
    out = io.BytesIO()
    im.save(out, format="PNG")
    x, y = positions[0]
    return out.getvalue(), (x - 2, y - 2, x + sym + 2, y + sym + 2)


def _item(qty, *, unit="個", conf=0.5) -> TakeoffItem:
    return TakeoffItem(
        page=1, name="排煙口", spec="300×300", quantity=qty, unit=unit,
        location="3F", confidence=conf, qty_basis="count",
    )


def _setup(qty, *, n_templates=2):
    """毒見本対策: 採用には見本2個以上の一致が要る。既定で2個渡す。"""
    page, bbox = _symbol_page([(60, 60), (300, 180), (560, 400)])
    tpl = crop_template(page, bbox)
    tpl2 = crop_template(page, (298, 178, 338, 218))  # 2個目の記号から採った見本
    assert tpl is not None and tpl2 is not None
    it = _item(qty)
    return page, {_item_key(it): [tpl, tpl2][:n_templates]}, it


def test_default_is_annotate_only():
    """既定では数を書き換えない（過積算方向の非対称バイアスを避ける）。"""
    page, templates, it = _setup(qty=1)
    recount(page, [it], templates, item_key=_item_key)
    assert it.qty_cv == 3, "機械の数は記録する"
    assert it.quantity == 1, "書き換えは既定でしない"


def test_adopts_when_machine_finds_more(monkeypatch):
    monkeypatch.setenv("GOPIPE_CV_ADOPT", "1")
    page, templates, it = _setup(qty=1)
    notes = recount(page, [it], templates, item_key=_item_key)
    assert it.qty_cv == 3
    assert it.quantity == 3, "機械が多い＝AIの見落としなので採用"
    assert it.qty_vision == 1
    assert it.source == "cv_count"
    assert any("採用" in n for n in notes)


def test_single_template_records_but_does_not_adopt(monkeypatch):
    """🔴毒見本ガード: LLMのbboxは時々記号でない場所を囲む（実測）。
    採用モードでも、見本が1個しか無いときは数を書き換えない（不一致として人へ）。"""
    monkeypatch.setenv("GOPIPE_CV_ADOPT", "1")
    page, templates, it = _setup(qty=1, n_templates=1)
    notes = recount(page, [it], templates, item_key=_item_key)
    assert it.qty_cv == 3, "記録はする"
    assert it.quantity == 1, "書き換えはしない"
    assert it.source != "cv_count"
    assert it.confidence <= 0.6
    assert any("不一致" in n for n in notes)


def test_machine_count_compares_against_group_total_across_locations():
    """🔴物差し合わせ: 機械は図面全体を数える。場所ごとの行と直接比べず、
    同種（name×spec×unit）の合計と比べる（実図面で偶然一致に確度を上げた実測から）。"""
    page, bbox = _symbol_page([(60, 60), (300, 180), (560, 400)])
    tpl = crop_template(page, bbox)
    tpl2 = crop_template(page, (298, 178, 338, 218))
    a = _item(2)                # 役員室1に2個
    b = _item(1)
    b.location = "役員室2"       # 役員室2に1個 → 合計3個
    templates = {_item_key(a): [tpl], _item_key(b): [tpl2]}
    notes = recount(page, [a, b], templates, item_key=_item_key, group_key=_shape_key)
    assert a.qty_cv == 3 and b.qty_cv == 3, "qty_cv は図面全体の機械計数"
    assert a.quantity == 2 and b.quantity == 1, "行の数量は動かさない"
    assert a.confidence >= 0.85 and b.confidence >= 0.85, "合計3=機械3で一致"
    assert any("AI合計3" in n or "合計" in n for n in notes)


def test_agreement_boosts_confidence():
    page, templates, it = _setup(qty=3)
    recount(page, [it], templates, item_key=_item_key)
    assert it.qty_cv == 3
    assert it.quantity == 3
    assert it.confidence >= 0.85


def test_machine_undercount_does_not_overwrite():
    page, templates, it = _setup(qty=5)
    notes = recount(page, [it], templates, item_key=_item_key)
    assert it.qty_cv == 3
    assert it.quantity == 5, "機械が少ない＝機械側の見落としがあり得るので上書きしない"
    assert it.confidence <= 0.6
    assert any("不一致" in n for n in notes)


def test_adopt_flag_must_be_exactly_one(monkeypatch):
    monkeypatch.setenv("GOPIPE_CV_ADOPT", "yes")  # "1" 以外は全部OFF
    page, templates, it = _setup(qty=1)
    recount(page, [it], templates, item_key=_item_key)
    assert it.qty_cv == 3, "記録はする"
    assert it.quantity == 1, "採用はしない"


def test_non_count_units_are_ignored():
    page, bbox = _symbol_page([(60, 60), (300, 180)])
    tpl = crop_template(page, bbox)
    it = _item(2.5, unit="m")
    recount(page, [it], {_item_key(it): [tpl]}, item_key=_item_key)
    assert it.qty_cv is None


def test_no_plateau_means_no_number():
    """記号が1つも無い白紙 → 踊り場ゼロ件 → qty_cv を付けない。"""
    blank = io.BytesIO()
    Image.new("L", (400, 300), 255).save(blank, format="PNG")
    page, bbox = _symbol_page([(60, 60)])
    tpl = crop_template(page, bbox)
    it = _item(2)
    notes = recount(blank.getvalue(), [it], {_item_key(it): [tpl]}, item_key=_item_key)
    assert it.qty_cv is None
    assert it.quantity == 2
    assert any("数えられず" in n for n in notes)


def test_crop_template_rejects_regions():
    page, _ = _symbol_page([(60, 60)])
    assert crop_template(page, (0, 0, 500, 400)) is None, "巨大bboxは記号でなく領域"
    assert crop_template(page, (0, 0, 8, 8)) is None, "小さすぎるbboxはノイズ"
    assert crop_template(b"not-an-image", (0, 0, 40, 40)) is None


# --------------------------------------------------------------------------
# extract() 通し（タイル→テンプレート採取→CV検算→採用）
# --------------------------------------------------------------------------
class OneTileClient:
    """1タイル構成で、記号1個ぶんのbbox付き行を返す擬似LLM。"""

    name = "scripted"

    def __init__(self, bbox):
        self.model = "scripted"
        self._bbox = list(bbox)

    def complete(self, messages, *, max_tokens=4096, temperature=0.0) -> LLMResponse:
        rows = [{
            "page": 1, "name": "排煙口", "spec": "300×300", "quantity": 1,
            "unit": "個", "location": "3F", "qty_basis": "count",
            "confidence": 0.5, "bbox": self._bbox,
        }]
        return LLMResponse(text=json.dumps(rows, ensure_ascii=False), model=self.model)


def test_extract_end_to_end_harvests_template_and_recounts():
    """2タイルがそれぞれ記号1個を報告 → 合算2個 → 機械照合3個（見本2個一致）で採用。"""
    page_png, bbox = _symbol_page([(60, 60), (300, 180), (560, 400)])
    im = Image.open(io.BytesIO(page_png))
    tiles = [
        Tile(image_png=page_png, row=r, col=0, rows=2, cols=1,
             width=im.width, height=im.height, core=[0.0, 0.0, 1.0, 1.0])
        for r in range(2)
    ]
    page = DrawingPage(
        page=1, width=im.width, height=im.height, text="",
        image_png=page_png, tiles=tiles,
    )
    items = extract(Drawing(source_path="x", pages=[page]), client=OneTileClient(bbox), two_pass=False)
    smoke = [it for it in items if it.name == "排煙口"]
    assert len(smoke) == 1
    assert smoke[0].bbox is None, "タイル座標のbboxはページに載せない"
    assert smoke[0].qty_cv == 3, "機械照合の数が記録される"
    assert smoke[0].quantity == 2, "既定では書き換えない（合算の2個のまま・要確認へ）"
    assert smoke[0].confidence <= 0.6
