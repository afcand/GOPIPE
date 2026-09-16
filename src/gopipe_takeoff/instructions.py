"""人が指した指示を、次の図面でも効かせる。

2026-09-16 に「指して拾う」を出したが、指示はその場限りで消えていた。
同じ様式の紙が次に来ても、また囲み直し・また色を指し直しになる。
拾い出しの堀は「この会社はここをこう拾う」が溜まって初めて効く。

覚えるのは2つ。
  箇所（region）… この図面の型の、ここを拾う
  色  （color） … この色は「◯◯として数える／長さを測る／拾わない」

🔴 色の意味は会社ごと・図面ごとにしか決まらない（実測: 5枚で「ダクト」が5通りの色）。
共通辞書へ流用しない。必ず会社で閉じる。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .models import TakeoffItem
from .pdf_shapes import visible_drawings

# 色の拾い方。名前を付けるだけでは数量にならない。
ACTIONS = ("count", "measure", "skip")


@dataclass
class ColorRule:
    """この色をどう拾うか。"""

    hex: str
    name: str
    action: str = "count"          # count=数える / measure=長さを測る / skip=拾わない
    unit: str = ""
    category: str | None = None

    @classmethod
    def from_payload(cls, p: dict) -> "ColorRule | None":
        h = str(p.get("hex") or "").strip().lower()
        if not h.startswith("#") or len(h) != 7:
            return None
        act = str(p.get("action") or "count").strip().lower()
        if act not in ACTIONS:
            act = "count"
        unit = str(p.get("unit") or "").strip() or ("m" if act == "measure" else "個")
        return cls(hex=h, name=str(p.get("name") or "").strip() or h,
                   action=act, unit=unit, category=(p.get("category") or None))


def sheet_key_of(drawing, report=None) -> str:
    """図面の「型」の鍵。同じ様式の紙（M-001-01〜08 など）は同じ鍵になる。

    図枠に刷られた文字は、同じ様式の紙で同じ座標に繰り返し出る（frame_filter がそれを
    見分けている）。その文字の集まりを鍵にすれば、物件が変わっても様式が同じなら当たる。
    文字が無い紙（スキャン）は鍵を作らない。指示を取り違えるより当たらないほうが安全なので、
    鍵が弱いときは空文字を返して「全図面向け」に倒す。
    """
    texts: set[str] = set()
    for t in (report or []):
        t = str(t).strip()
        if 2 <= len(t) <= 24:
            texts.add(t)
    if len(texts) < 5:
        return ""
    joined = "|".join(sorted(texts)[:40])
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


def _hex_of(rgb) -> str:
    if not rgb:
        return ""
    r, g, b = (int(round(max(0.0, min(1.0, float(v))) * 255)) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _near(a: str, b: str, tol: int = 20) -> bool:
    """スキャンや書き出しで色は少しぶれる。近ければ同じ色とみなす。"""
    if not a or not b or len(a) != 7 or len(b) != 7:
        return False
    try:
        pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    except ValueError:
        return False
    return all(abs(x - y) <= tol for x, y in zip(pa, pb))


def color_items(page, rules: list[ColorRule], *, page_no: int, scale: float = 1.0) -> list[TakeoffItem]:
    """覚えている色の指示を、この紙に当てて行にする。

    count なら図形の数、measure なら線の長さの合計。skip は行を作らない。
    """
    from .duct_geometry import PT2MM

    if not rules:
        return []
    mm_per_pt = PT2MM * scale
    tally: dict[str, list[float]] = {r.hex: [0.0, 0.0] for r in rules}   # [個数, 長さpt]
    for d in visible_drawings(page):
        hx = _hex_of(d.get("color")) or _hex_of(d.get("fill"))
        if not hx:
            continue
        for r in rules:
            if not _near(hx, r.hex):
                continue
            tally[r.hex][0] += 1
            for it in d.get("items", []):
                if it[0] == "l":
                    p, q = it[1], it[2]
                    tally[r.hex][1] += ((p.x - q.x) ** 2 + (p.y - q.y) ** 2) ** 0.5
                elif it[0] == "re":
                    rr = it[1]
                    tally[r.hex][1] += 2 * (rr.width + rr.height)
            break

    out: list[TakeoffItem] = []
    for r in rules:
        n, length_pt = tally.get(r.hex, [0.0, 0.0])
        if r.action == "skip" or n <= 0:
            continue
        qty = (length_pt * mm_per_pt / 1000.0) if r.action == "measure" else n
        out.append(TakeoffItem(
            page=page_no, name=r.name, spec=f"色 {r.hex}",
            quantity=round(qty, 1), unit=r.unit,
            category=r.category, confidence=0.85,
            source="color_rule",
            qty_basis="measure" if r.action == "measure" else "count",
            location="この会社が覚えさせた色の指示",
        ))
    return out


def apply_skips(items: list[TakeoffItem], rules: list[ColorRule]) -> tuple[list[TakeoffItem], int]:
    """「拾わない」と覚えさせた色の行を落とす。落とした数も返す（黙って消さない）。"""
    skips = [r for r in rules if r.action == "skip"]
    if not skips:
        return items, 0
    kept, dropped = [], 0
    for it in items:
        spec = it.spec or ""
        if it.color and any(_near(str(it.color), r.hex) for r in skips):
            dropped += 1
            continue
        if spec.startswith("色 #") and any(_near(spec[2:].strip(), r.hex) for r in skips):
            dropped += 1
            continue
        kept.append(it)
    return kept, dropped
