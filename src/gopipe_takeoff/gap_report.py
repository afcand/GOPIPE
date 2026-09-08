"""その図面で「拾えていないもの」を、図面自身から見つけて申告する。

なぜ要るか:
  拾い出し表に出てこない部材は、現場からは「0個」に見える。だが実際には
  「無い」のではなく「この経路では数えられない」だけのことがある。
  実測(2026-09-08 NEC府中 A1ベクター23枚)では、防火ダンパー・防煙ダンパーの
  文字ラベルが23枚で1件しかなかった。実体は記号で描かれている。表に出ないまま
  渡せば、防火ダンパーが1個の見積になる。

  0件と「読めていない」を混同させない、という掟を、品目の単位でも守るための仕組み。

見つけ方は図面自身から:
  ・凡例（図枠）に記号が定義されているのに、図の中にその文字がほとんど無い
    → 記号で描かれている。文字では数えられない
  ・ダクト・配管を拾えた → その延長 m と角ダクトの面積 m2 は図面に文字が無い
  ・冷媒配管サイズ表がある → 口径は表を引く形。平面の記号は図形
  ・図面名が「2.3.4階」のように複数フロアを持つ → 1枚の数量に掛ける数が要る
  ・1枚に断面図・詳細図が同居 → 平面と同じ部材が二度描かれている
  決め打ちの一覧ではなく図面から導くので、案件が変わっても効く。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .frame_filter import FrameReport, sheet_kind
from .frame_filter import detect as detect_frame
from .models import Drawing, TakeoffItem

# 凡例に載っていたら「図の中にも在るはず」の記号。名前は凡例側の表記に合わせる。
_SYMBOL_CODES = {
    "FD": "防火ダンパー(FD)",
    "SD": "防煙ダンパー(SD)",
    "FD/SD": "防火ダンパー・防煙ダンパー(FD/SD)",
    "SEMD": "排煙口(SEMD)",
    "SMFD": "排煙用防火ダンパー(SMFD)",
    "MFD": "防火ダンパー(MFD)",
    "HFD": "排煙防火ダンパー(HFD)",
    "VD": "風量調整ダンパー(VD)",
    "MD": "モーターダンパー(MD)",
}
# この数以下しか図の中に文字が無ければ「記号で描かれている」とみなす
_TEXT_FLOOR = 2

_SECTION = re.compile(r"([A-Z]-[A-Z]'|[A-Z]－[A-Z]’)\s*断面図|詳細図")
_MULTI_FLOOR = re.compile(r"(\d)[\.,・](\d)[\.,・](\d)\s*階|(\d)[\.,・](\d)\s*階")
_REFRIGERANT = re.compile(r"冷媒配管")


@dataclass
class Gap:
    """拾えていないものと、その理由・打ち手。"""

    item: str        # 何が
    reason: str      # なぜ拾えていないか
    action: str      # 人はどうすればよいか
    pages: list[int]

    def line(self) -> str:
        p = "・".join(str(x) for x in self.pages[:8]) + ("…" if len(self.pages) > 8 else "")
        return f"{self.item}｜{self.reason}｜{self.action}（ページ {p}）"


def find(
    drawing: Drawing,
    items: list[TakeoffItem],
    *,
    report: FrameReport | None = None,
) -> list[Gap]:
    """図面から「この拾い出し表に出てこないもの」を洗い出す。"""
    rep = report or detect_frame(drawing)
    gaps: list[Gap] = []

    # 図の中の文字（図枠を除いたもの）を、ページごとに数える
    inner_text: dict[int, str] = {}
    frame_text: dict[int, str] = {}
    for p in drawing.pages:
        kind = sheet_kind(p)
        ins, frs = [], []
        for ln in p.text_lines:
            (frs if rep.is_frame(kind, ln) else ins).append(ln.text)
        inner_text[p.page] = "\n".join(ins)
        frame_text[p.page] = "\n".join(frs)

    # 1. 凡例にあるのに図の中に文字が無い記号＝記号で描かれている
    # 凡例が "FD/SD" のように連記されているときは、その1行で足りる。
    # FD・SD を別々にも出すと、同じ宿題が3行に増えて読む気を削ぐ。
    combined = any("FD/SD" in t for t in frame_text.values())
    for code, name in _SYMBOL_CODES.items():
        if combined and code in ("FD", "SD"):
            continue
        rx = re.compile(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])")
        on_legend = [p for p, t in frame_text.items() if rx.search(t)]
        found = sum(len(rx.findall(t)) for t in inner_text.values())
        if on_legend and found <= _TEXT_FLOOR:
            gaps.append(Gap(
                item=name,
                reason=f"凡例に定義があるのに、図の中の文字は{found}件しかありません"
                       "（実体は記号で描かれています）",
                action="図の記号を数えてください。この表の数量には入っていません",
                pages=sorted(on_legend),
            ))

    # 2. 延長 m と面積 m2 は図面に文字が無い
    # 🔴 category だけを見ない。分類(classify)は辞書でカテゴリを塗り替えるため、
    # 分類後の項目を渡されると「配管の延長が要る」が黙って消える（実際に一度そうなった）。
    # 品目名でも判定して、呼ぶ順番に結果が左右されないようにする。
    def _is(it: TakeoffItem, *words: str) -> bool:
        hay = f"{it.category or ''} {it.name or ''} {it.raw_name or ''}"
        return any(w in hay for w in words)

    duct = sorted({i.page for i in items if _is(i, "ダクト")})
    pipe = sorted({i.page for i in items if _is(i, "配管", "管", "汚水", "通気", "給水", "ドレン")}
                  - set(duct))
    measured = [i for i in items if i.source == "duct_geometry"]
    if duct and not measured:
        gaps.append(Gap(
            item="ダクトの延長 m・角ダクトの面積 m2",
            reason="図面に文字として書かれていません（線を追わないと出ません）",
            action="推定値は入れていません。人が図から測って記入してください",
            pages=duct,
        ))
    elif measured:
        # 測れたときも、そのまま信じてよい数字ではない。何が混じりうるかを書く。
        no_size = sum(1 for i in measured
                      if i.unit == "m" and "呼び寸法は図面から取れず" in (i.spec or ""))
        with_area = sum(1 for i in measured if i.unit == "m2")
        gaps.append(Gap(
            item="ダクトの延長 m・展開面積 m2（幾何から測った値）",
            reason=f"塗り多角形の面積と周長から測りました。ただし断面図に描かれた"
                   f"同じダクトが混じっている可能性があり、エルボや分岐は長方形として"
                   f"解けないため含みません。展開面積を出せたのは{with_area}行で、"
                   f"呼び寸法が図面から結びつかなかった行が{no_size}行あります",
            action="1ゾーンだけ人の拾い出しと突き合わせて、ずれ方の癖を掴んでから使ってください",
            pages=sorted({i.page for i in measured}),
        ))
    if pipe:
        gaps.append(Gap(
            item="配管の延長 m",
            reason="図面に文字として書かれていません",
            action="推定値は入れていません。人が図から測って記入してください",
            pages=pipe,
        ))

    # 3. 冷媒配管は平面に丸記号だけが振ってあり、口径は図枠の表で引く
    ref_pages = sorted(p for p, t in frame_text.items() if _REFRIGERANT.search(t))
    if ref_pages:
        gaps.append(Gap(
            item="冷媒配管",
            reason="平面図には丸記号だけが振ってあり、口径は図枠の冷媒配管サイズ表で"
                   "引く形です。記号は図形で描かれているため文字では数えられません",
            action="サイズ表（別シート）を見ながら、記号ごとに人が拾ってください",
            pages=ref_pages,
        ))

    # 4. 1枚で複数フロアを受け持つ図
    for p in drawing.pages:
        m = _MULTI_FLOOR.search(p.text or "")
        if m:
            floors = [g for g in m.groups() if g]
            gaps.append(Gap(
                item=f"このページの数量（{len(floors)}フロア分）",
                reason=f"図面名が {'・'.join(floors)}階 の共用図です。1枚に見えて"
                       f"{len(floors)}フロア分あります",
                action=f"このページから拾った数量は ×{len(floors)} が要ります",
                pages=[p.page],
            ))

    # 5. 平面図と断面図・詳細図の同居＝同じ部材が二度描かれている
    sect: dict[int, int] = {}
    for p in drawing.pages:
        n = len(set(_SECTION.findall(p.text or "")))
        if n:
            sect[p.page] = n
    if sect:
        dup = _duplicate_ratio(drawing, rep)
        gaps.append(Gap(
            item="平面図と断面図の二重計上",
            reason=f"1枚に断面図・詳細図が同居しています"
                   f"（同一ページで同じ表記が2回以上出る割合 {dup:.0%}）",
            action="明細シートの x・y でどの図から来たかを切り分けてから合計してください",
            pages=sorted(sect),
        ))
    return gaps


def _duplicate_ratio(drawing: Drawing, rep: FrameReport) -> float:
    """同一ページ内で同じ表記が2回以上出る割合。二重計上リスクの上限の目安。

    全部が重複というわけではない（別の場所の別の部材が同じ呼び名のこともある）。
    あくまで「どれくらい気をつける紙か」を数で言うための目安。
    """
    dup = tot = 0
    for p in drawing.pages:
        kind = sheet_kind(p)
        texts = [ln.text for ln in p.text_lines if not rep.is_frame(kind, ln)]
        c = Counter(texts)
        dup += sum(v - 1 for v in c.values() if v > 1)
        tot += len(texts)
    return dup / tot if tot else 0.0
