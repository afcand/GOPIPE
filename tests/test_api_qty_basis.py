"""数量の出所(qty_basis)が API を通っても消えないことを固定する。

エンジンが出所を計算し、Excel の備考が「図面の表から／🔴AI推定（要検算）」を
書き分けても、**API の項目シリアライザが落とすと画面には何も届かない**。
さらに /export/xlsx は受け取った JSON から TakeoffItem を組み直すため、
出所を運ばないと **Web からダウンロードした Excel だけ備考が空** になる。
実際に一度そうなっていた（本番の応答で qty_basis が全行 None）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT))

from gopipe_takeoff.excel_writer import _note  # noqa: E402
from gopipe_takeoff.models import TakeoffItem  # noqa: E402


def _item(basis: str, qty: float = 1.0) -> TakeoffItem:
    return TakeoffItem(
        page=1, name="吸込口", spec="HS-200×200", quantity=qty, unit="個",
        confidence=0.95, qty_basis=basis,
    )


def test_items_json_carries_qty_basis():
    from api.main import _items_json

    rows = _items_json([_item("table"), _item("estimate")])
    assert [r["qty_basis"] for r in rows] == ["table", "estimate"]


def test_items_json_and_export_carry_qty_cv():
    """機械計数(qty_cv)も同じ罠を踏まないよう固定する（シリアライザが落とすと画面に届かない）。"""
    from api.main import _items_json

    it = _item("count")
    it.qty_cv = 3.0
    rows = _items_json([it])
    assert rows[0]["qty_cv"] == 3.0
    rebuilt = TakeoffItem(
        page=1, name=rows[0]["name"], spec=rows[0].get("spec"),
        quantity=float(rows[0]["quantity"]), unit=rows[0]["unit"],
        confidence=float(rows[0]["confidence"]),
        qty_basis=(rows[0].get("qty_basis") or None),
        qty_cv=(float(rows[0]["qty_cv"]) if rows[0].get("qty_cv") is not None else None),
    )
    assert rebuilt.qty_cv == 3.0


def test_export_payload_round_trips_qty_basis_into_note():
    """API の出力をそのまま /export/xlsx に戻したとき、備考が復元されること。"""
    from api.main import _items_json

    rows = _items_json([_item("table"), _item("estimate")])
    # export_xlsx が組み直すのと同じ経路
    rebuilt = [
        TakeoffItem(
            page=int(r.get("page") or 1), name=r["name"], spec=r.get("spec"),
            quantity=float(r["quantity"]), unit=r["unit"],
            location=r.get("location"), category=r.get("category"),
            confidence=float(r["confidence"]),
            qty_basis=(r.get("qty_basis") or None),
            source=(r.get("source") or None),
        )
        for r in rows
    ]
    notes = [_note(it) for it in rebuilt]
    assert "図面の表から" in notes[0]
    assert "AI推定" in notes[1], "推定が備考から消えると、人は検算すべき行を見つけられない"
