"""図枠（凡例・参照表・キープラン・タイトル欄）の文字を、図の中の部材と見分ける。

なぜ要るか（2026-09-08 実測・NEC府中事業場3号館東棟 A1ベクター図）:
  空調衛生設備の配管図8枚のテキスト層から冷媒配管の呼び径らしき文字が **608件**
  取れた。だが内訳は8ページに **76件ずつ、しかも同じ座標**。図枠に印刷された
  「冷媒配管サイズ表」と「吊り支持間隔表」であって、配管の実体ではない。
  そのまま数えれば608本の架空計上になる。

なぜ座標の決め打ちでは駄目か:
  「右下25%は図枠」のような決め打ちは、紙のサイズ・図枠の様式・会社が変わると
  必ずズレる。実際この案件でも、図枠の参照表は右上(y=0.45付近)にもあり、
  逆に平面図は x=0.84 まで伸びているページがあった。片側に寄せて切ると、
  図枠を残すか、図の中の部材を捨てるかのどちらかになる。

どう見分けるか:
  設備図は同じ工事・同じ図面種別なら図枠が全ページ同一に刷られている。だから
  **「同じ文字が、同じ座標に、同じ種別のページの大半で現れる」ものは図枠**。
  逆に図の中の部材は、ページごとに位置も内容も変わる。紙が変わっても効く。

安全側の設計:
  ・ページが少ない（既定 3 未満）ときは判定しない。1〜2枚では「毎ページ出る」に
    意味が無く、本物の部材を図枠と誤判定して黙って捨てる方が危ない。
  ・除外したものは捨てずに理由つきで返す（reasons）。0件と「読めていない」を
    混同させないため、呼び出し側は必ず画面・帳票まで運ぶこと。
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .models import Drawing, DrawingPage, TextLine

# 図枠判定に要る最小ページ数。これ未満は判定しない（安全側）。
MIN_PAGES = 3
# 同一種別のページのうち、何割に出れば図枠とみなすか。
REPEAT_RATIO = 0.6
# 座標の一致とみなす丸め桁（0.001 = 紙の 0.1%）。A1 長辺 841mm なら約 0.8mm。
COORD_ND = 3
# 図番から図面種別を取る（M-001-01 → M、PWC-005-01 → PWC）。種別ごとに図枠が違う。
_SHEET_NO = re.compile(r"\b([A-Z]{1,4})-\d{2,4}-\d{1,3}\b")


@dataclass
class FrameReport:
    """図枠と判定した文字と、その理由。捨てた事実を必ず運ぶための入れ物。"""

    keys: set[tuple[str, str, float, float]] = field(default_factory=set)
    reasons: dict[tuple[str, str, float, float], str] = field(default_factory=dict)
    pages_by_kind: dict[str, int] = field(default_factory=dict)

    def is_frame(self, kind: str, line: TextLine) -> bool:
        return _key(kind, line) in self.keys

    def why(self, kind: str, line: TextLine) -> str | None:
        return self.reasons.get(_key(kind, line))

    def __len__(self) -> int:  # 除外した文字の種類数
        return len(self.keys)


def _key(kind: str, line: TextLine) -> tuple[str, str, float, float]:
    return (kind, line.text, round(line.x, COORD_ND), round(line.y, COORD_ND))


def sheet_kind(page: DrawingPage) -> str:
    """そのページの図面種別。図番の記号部分を使い、取れなければ空文字。

    種別で分けるのは、同じPDFでも「ダクト図」と「配管図」で図枠の中身が違うため。
    混ぜて数えると、片方にしか無い図枠が「大半のページに出ない」ことになり
    見逃す。
    """
    m = _SHEET_NO.search(page.text or "")
    return m.group(1) if m else ""


def detect(
    drawing: Drawing,
    *,
    min_pages: int = MIN_PAGES,
    repeat_ratio: float = REPEAT_RATIO,
) -> FrameReport:
    """図枠・凡例・参照表として繰り返し刷られている文字を洗い出す。"""
    rep = FrameReport()
    pages = [p for p in drawing.pages if p.text_lines]
    if len(pages) < min_pages:
        return rep

    seen: dict[tuple[str, str, float, float], set[int]] = defaultdict(set)
    total: dict[str, set[int]] = defaultdict(set)
    for p in pages:
        kind = sheet_kind(p)
        total[kind].add(p.page)
        for ln in p.text_lines:
            seen[_key(kind, ln)].add(p.page)

    rep.pages_by_kind = {k: len(v) for k, v in total.items()}
    for key, on_pages in seen.items():
        n = len(total[key[0]])
        if n < min_pages:
            continue
        need = max(min_pages, int(n * repeat_ratio))
        if len(on_pages) >= need:
            rep.keys.add(key)
            rep.reasons[key] = (
                f"図枠の共通表と判定（{n}枚中{len(on_pages)}枚の同じ座標に同じ文字）"
            )
    return rep


def split(
    drawing: Drawing, report: FrameReport | None = None
) -> tuple[dict[int, list[TextLine]], dict[int, list[tuple[TextLine, str]]]]:
    """ページごとに、図の中の文字 と 図枠の文字 に分ける。

    Returns: ({ページ番号: 図の中の行}, {ページ番号: [(図枠の行, 理由)]})
    """
    rep = report or detect(drawing)
    inner: dict[int, list[TextLine]] = {}
    frame: dict[int, list[tuple[TextLine, str]]] = {}
    for p in drawing.pages:
        kind = sheet_kind(p)
        keep, drop = [], []
        for ln in p.text_lines:
            if rep.is_frame(kind, ln):
                drop.append((ln, rep.why(kind, ln) or "図枠"))
            else:
                keep.append(ln)
        inner[p.page], frame[p.page] = keep, drop
    return inner, frame
