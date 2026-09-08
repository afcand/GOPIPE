"""ベクター(CAD)PDFの印字からの拾い出しテスト。

固定したいこと:
  ・印字を機械で数える＝何度かけても同じ数（LLMの計数は 6/11 しか一致しない）
  ・数えたのは「ラベル」であって部材ではない＝単位は「箇所」。ここを本やmと
    言い換えたら嘘になる
  ・延長mと面積m2は返さない（図面に文字が無いものを推定で埋めない）
  ・型に載らなかった行は捨てず unread で返す（0件と読めていないを混同させない）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff import vector_takeoff as vt  # noqa: E402
from gopipe_takeoff.models import Drawing, DrawingPage, TextLine  # noqa: E402


def _page(no: int, sheet: str, lines: list[tuple[str, float, float]]) -> DrawingPage:
    return DrawingPage(
        page=no, width=1000, height=700, text=f"{sheet} 図面",
        text_lines=[TextLine(text=t, x=x, y=y, x1=x + 0.03, y1=y + 0.008) for t, x, y in lines],
    )


def test_設備図の代表的なラベルを型にはめる():
    cases = {
        "SA 400×300 1FL+5,350(下端)": ("ダクト（角）", "給気ダクト", "400×300"),
        "EA 250φ": ("ダクト（丸）", "排気ダクト", "250φ"),
        "SOA 375φ 1FL+5,350": ("ダクト（丸）", "外調機給気ダクト", "375φ"),
        "VHS(結露防止型) 300×300": ("吹出口・吸込口", "VHS(結露防止型)", "300×300"),
        "吸込850×850": ("吹出口・吸込口", "吸込口", "850×850"),
        "VD 250×550": ("ダンパー・排煙口", "風量調整ダンパー(VD)", "250×550"),
        "SEMD 650×800": ("ダンパー・排煙口", "排煙口(SEMD)", "650×800"),
        "SAチャンバー500×1400×600H": ("チャンバー", "SAチャンバー", "500×1400×600H"),
        "消音エルボ950×800": ("消音器", "消音エルボ", "950×800"),
        "GD 100VP 1FL+143": ("配管", "汚水 VP(硬質塩ビ管)", "100VP"),
        "GD 125A": ("配管", "汚水 A呼称(鋼管系)", "125A"),
        "通気50VP 1FL+610": ("配管", "通気 VP(硬質塩ビ管)", "50VP"),
        "20A 1FL+440": ("配管", "系統記号なし A呼称(鋼管系)", "20A"),
        "GV20": ("弁", "仕切弁(ゲートバルブ)", "20A"),
        "減圧弁(圧力計付)40": ("弁", "減圧弁(圧力計付)", "40A"),
        "ねじ式CO100": ("継手・付属", "掃除口(CO)・ねじ式", "100A"),
        "満水試験兼用伸縮継手125": ("継手・付属", "満水試験兼用伸縮継手", "125A"),
        "MAC 1-5-1a": ("機器", "MAC（空調機）", "1-5-1a"),
    }
    for text, want in cases.items():
        assert vt.classify_line(text) == want, text


def test_汚水は竪管と横引管で材質が分かれる():
    """GD 125A は DVLP(鋼管系)、GD 100VP は塩ビ。単価が別物なので混ぜない。"""
    a = vt.classify_line("GD 125A")
    b = vt.classify_line("GD 100VP")
    assert a is not None and b is not None
    assert a[1] != b[1]


def test_数えるのはラベルであって部材ではない():
    dw = Drawing(source_path="t.pdf", pages=[_page(1, "M-001-01", [
        ("EA 250φ", 0.2, 0.3), ("EA 250φ", 0.5, 0.3), ("EA 250φ", 0.7, 0.6),
        ("VD 150φ", 0.4, 0.4),
    ])])
    items, unread = vt.extract(dw)
    ea = next(i for i in items if i.spec == "250φ")
    assert ea.quantity == 3
    assert ea.unit == "箇所"              # 「本」でも「m」でもない
    assert ea.qty_basis == "count" and ea.source == "vector_text"
    assert not unread


def test_延長mと面積m2は返さない():
    """図面に文字が無いものを推定で埋めない。人が入れる欄として空けておく。"""
    dw = Drawing(source_path="t.pdf", pages=[_page(1, "M-001-01", [("SA 400×300", 0.2, 0.3)])])
    items, _ = vt.extract(dw)
    assert all(i.unit not in ("m", "m2") for i in items)


def test_取付高さを拾う():
    """設備図はここにZ軸が印刷されている。落とすと立上り・立下りが丸ごと消える。"""
    dw = Drawing(source_path="t.pdf", pages=[_page(1, "M-001-01", [
        ("SA 400×300 1FL+5,350(下端)", 0.2, 0.3),
        ("EA 150φ FL-300", 0.4, 0.3),
    ])])
    items, _ = vt.extract(dw)
    assert next(i for i in items if i.spec == "400×300").level_mm == 5350
    assert next(i for i in items if i.spec == "150φ").level_mm == -300


def test_取付高さが違えば別の行にする():
    dw = Drawing(source_path="t.pdf", pages=[_page(1, "M-001-01", [
        ("SA 400×300 1FL+5,350", 0.2, 0.3),
        ("SA 400×300 1FL+4,000", 0.5, 0.3),
    ])])
    items, _ = vt.extract(dw)
    assert sorted(i.level_mm for i in items) == [4000, 5350]


def test_直下の風量を吹出口に結びつける():
    dw = Drawing(source_path="t.pdf", pages=[_page(1, "M-001-01", [
        ("VHS(結露防止型) 550×550", 0.30, 0.400),
        ("[2,250 m³/h]", 0.305, 0.409),
    ])])
    items, _ = vt.extract(dw)
    assert items[0].location == "風量 2,250 m³/h"


def test_図枠の参照表は数えない():
    """2026-09-08 の実害の回帰。8ページに同じ座標で刷られたサイズ表を配管にしない。"""
    pages = [
        _page(i, f"MP-001-0{i}", [
            ("19.1φ", 0.90, 0.50),                 # 図枠の冷媒サイズ表
            ("GD 100VP", 0.2 + i * 0.04, 0.30),    # 図の中の配管
        ])
        for i in range(1, 9)
    ]
    items, _ = vt.extract(Drawing(source_path="t.pdf", pages=pages))
    assert sum(i.quantity for i in items) == 8          # 8ページ分の配管だけ
    assert not any("19.1" in (i.spec or "") for i in items)


def test_タイトル欄の文字は数えない():
    dw = Drawing(source_path="t.pdf", pages=[_page(1, "M-001-01", [
        ("EA 250φ", 0.2, 0.30),
        ("EA 250φ", 0.2, 0.97),   # 図枠の下帯
    ])])
    items, _ = vt.extract(dw)
    assert sum(i.quantity for i in items) == 1


def test_型に載らなかったラベルは捨てずに返す():
    """0件と『読めていない』を混同させない。"""
    dw = Drawing(source_path="t.pdf", pages=[_page(1, "M-001-01", [
        ("EA 250φ", 0.2, 0.3),
        ("ZZZ 123×456", 0.5, 0.3),   # 型に無いがラベルらしい
        ("柱芯", 0.6, 0.3),           # 建築＝対象外
    ])])
    items, unread = vt.extract(dw)
    assert len(items) == 1
    assert unread == [(1, "ZZZ 123×456")]


def test_同じ図面を二度かけても同じ数になる():
    """印字を数えるのは決定的。LLMの計数と違い、結果が動かないことを固定する。"""
    dw = Drawing(source_path="t.pdf", pages=[_page(1, "M-001-01", [
        ("EA 250φ", 0.2, 0.3), ("EA 250φ", 0.5, 0.3), ("VD 250×550", 0.4, 0.5),
    ])])
    a = {(i.name, i.spec, i.quantity) for i in vt.extract(dw)[0]}
    b = {(i.name, i.spec, i.quantity) for i in vt.extract(dw)[0]}
    assert a == b
