"""ベクター(CAD出力)PDFの印字から拾い出す — 推定を1件も混ぜない経路。

なぜこの経路が要るか（2026-09-08 実測・NEC府中事業場3号館東棟 A1ベクター23枚）:
  設備図がCADから出たPDFなら、部材の呼び名・口径・寸法・取付高さ・風量は
  すべて「文字」として入っている。画像認識に回す必要がない。実測では
  **1,627件・カバー率99.8%**（ラベルらしい行のうち型に載らなかったのは3件）。
  画像認識の計数は同じ図面を2回かけると動くが（実測 6/11 しか一致しない）、
  印字を読むのは決定的で、何度かけても同じ数になる。

この経路が返さないもの（返さないことが正しい）:
  ・ダクト・配管の延長 m、角ダクトの面積 m2 … 図面に文字が無い。線を追わないと
    出ない。推定で埋めると [[数量の出所]] が estimate まで落ちるうえ、現場は
    その数字を信じてしまう。**空のまま人に渡す。**
  ・防火/防煙ダンパー、衛生器具 … 記号で描かれ文字が無い（23枚で文字ラベル1件）。
    記号照合（symbol_match / cv_count）の担当。
  ・冷媒配管の口径 … 平面図には丸記号だけが振ってあり、口径は図枠のサイズ表で
    引く。refrigerant.py の担当。

数え方の約束:
  quantity は **図面にその表記がある箇所の数**であって、材料の数量ではない。
  1本のダクトに2箇所ラベルが振ってあれば 2 になる。だから unit は「箇所」とし、
  備考に出所を必ず書く。ここを「本」や「m」と言い換えた瞬間に嘘になる。
"""
from __future__ import annotations

import re
from collections import defaultdict

from .frame_filter import FrameReport, sheet_kind
from .frame_filter import detect as detect_frame
from .models import BBox, Drawing, TakeoffItem, TextLine

N = r"[\d,]+(?:\.\d+)?"


def _n(s: str) -> str:
    return s.replace(",", "").strip()


def _mul(s: str) -> str:
    return re.sub(r"[×xX]", "×", s)


HEIGHT = re.compile(rf"(\d?)FL\s*([+\-])\s*({N})")
AIRFLOW = re.compile(rf"\[\s*({N})\s*m³/h\s*\]")

# 空調ダクトの系統記号（図枠の「用途／記号／使用材料」表より）
DUCT_SYS = {
    "SA": "給気ダクト", "RA": "還気ダクト", "OA": "外気ダクト", "EA": "排気ダクト",
    "SOA": "外調機給気ダクト", "PASS": "パスダクト", "SM": "排煙ダクト",
}
DAMPER = {
    "VD": "風量調整ダンパー(VD)", "MD": "モーターダンパー(MD)", "FD": "防火ダンパー(FD)",
    "SD": "防煙ダンパー(SD)", "SEMD": "排煙口(SEMD)", "MFD": "防火ダンパー(MFD)",
    "SMFD": "排煙用防火ダンパー(SMFD)", "FVD": "防火風量調整ダンパー(FVD)",
    "SEHS": "排煙口(SEHS)", "HFD": "排煙防火ダンパー(HFD)",
}
# 配管の系統記号（同上）。GD は竪管=DVLP・横引管=VP で材質が変わる＝単価が別物。
PIPE_SYS = {"GD": "汚水", "AD": "空調ドレン", "CD": "冷媒ドレン", "HD": "排水(耐熱)",
            "W": "加湿給水", "R": "冷媒"}
MATERIAL = {
    "VP": "VP(硬質塩ビ管)", "HTVP": "HTVP(耐熱塩ビ管)",
    "DVLP": "DVLP(排水用塩ビライニング鋼管)", "A": "A呼称(鋼管系)",
    "VB": "SGP-VB", "VA": "SGP-VA",
}
VALVE = {"GV": "仕切弁(ゲートバルブ)", "BV": "ボール弁", "CV": "逆止弁",
         "SV": "仕切弁", "FV": "定流量弁"}

# 分類 → 単位。ダクト・配管は「箇所」（印字箇所の数）で、数量ではない。
UNIT = {
    "ダクト（丸）": "箇所", "ダクト（角）": "箇所", "配管": "箇所",
    "吹出口・吸込口": "個", "ダンパー・排煙口": "個", "チャンバー": "個",
    "消音器": "個", "ガラリ": "個", "弁": "個", "継手・付属": "個",
    "衛生器具・ます": "個", "付属品": "個", "建築付帯": "個", "機器": "台",
}

_RULES: list[tuple[str, re.Pattern[str], object]] = [
    ("ダクト（丸）", re.compile(rf"^(SA|RA|OA|EA|SOA|PASS|SM)\s+({N})\s*[φΦ]"),
     lambda m: (DUCT_SYS[m[1]], f"{_n(m[2])}φ")),
    ("ダクト（角）", re.compile(rf"^(SA|RA|OA|EA|SOA|PASS|SM)\s+({N})\s*[×xX]\s*({N})"),
     lambda m: (DUCT_SYS[m[1]], f"{_n(m[2])}×{_n(m[3])}")),
    ("吹出口・吸込口", re.compile(rf"^(VHS|HS|VSHS)\s*(\(結露防止型\))?\s*({N})\s*[×xX]\s*({N})"),
     lambda m: (f"{m[1]}{m[2] or ''}", f"{_n(m[3])}×{_n(m[4])}")),
    ("吹出口・吸込口", re.compile(rf"^(吸込|吹出)\s*({N})\s*[×xX]\s*({N})"),
     lambda m: (f"{m[1]}口", f"{_n(m[2])}×{_n(m[3])}")),
    ("吹出口・吸込口", re.compile(rf"^(RA|SA)\s+(CL-\d+)\s+({N})L"),
     lambda m: (f"{m[1]}ラインディフューザー", f"{m[2]} {_n(m[3])}L")),
    ("ダンパー・排煙口",
     re.compile(rf"^(SEMD|SMFD|MFD|FVD|SEHS|HFD|VD|MD|FD|SD)\s*({N})\s*([φΦ]|[×xX]\s*{N})"),
     lambda m: (DAMPER.get(m[1], m[1]),
                f"{_n(m[2])}φ" if m[3].strip() in "φΦ"
                else f"{_n(m[2])}×{_n(re.sub(r'[×xX]', '', m[3]))}")),
    ("ダンパー・排煙口", re.compile(rf"^排煙口\s*({N})\s*[×xX]\s*({N})"),
     lambda m: ("排煙口", f"{_n(m[1])}×{_n(m[2])}")),
    ("チャンバー", re.compile(rf"^(SOA|SA|RA|OA|EA|PASS|SM)?チャンバー\s*({N}[×xX]{N}[×xX]{N}H?)"),
     lambda m: (f"{m[1] or ''}チャンバー", _mul(_n(m[2])))),
    ("消音器", re.compile(rf"^消音エルボ\s*({N}\s*[×xX]\s*{N})"),
     lambda m: ("消音エルボ", _mul(_n(m[1])))),
    ("継手・付属", re.compile(rf"^(SA|RA|OA|EA|SOA|SM)\s*エルボ\s*({N})\s*[×xX]\s*({N})"),
     lambda m: (f"{DUCT_SYS[m[1]]}エルボ", f"{_n(m[2])}×{_n(m[3])}")),
    ("付属品", re.compile(r"^ホッパー\s*(.+)"), lambda m: ("ホッパー", _n(m[1]))),
    ("付属品", re.compile(r"^ドレンホース\s*(\d+)A"), lambda m: ("ドレンホース", f"{m[1]}A")),
    ("付属品", re.compile(rf"^(?:PASS\s*)?金網\s*({N})\s*[φΦ]"),
     lambda m: ("金網", f"{_n(m[1])}φ")),
    ("ガラリ",
     re.compile(rf"^(?:外気|排気|給気)?ガラリ?\s*(OAG|EAG|SAG|RAG)-(\d+)-(\d+)\s*({N}[×xX]{N})?"),
     lambda m: (f"{m[1]}ガラリ",
                f"{m[1]}-{m[2]}-{m[3]}" + (f" {_mul(_n(m[4]))}" if m[4] else ""))),
    ("機器", re.compile(r"^(MAC)\s+([\d\-]+[a-z]?)$"), lambda m: ("MAC（空調機）", m[2])),
    ("機器", re.compile(r"^(FXY[A-Z0-9]+)\b"), lambda m: ("パッケージ形空調機(PAC)", m[1])),
    ("配管", re.compile(rf"^(GD|AD|CD|HD)\s+({N})\s*(HTVP|DVLP|VP|A)\b"),
     lambda m: (f"{PIPE_SYS[m[1]]} {MATERIAL.get(m[3], m[3])}", f"{_n(m[2])}{m[3]}")),
    ("配管", re.compile(rf"^(GD|AD|CD|HD)\s+({N})\s*[φΦ]"),
     lambda m: (f"{PIPE_SYS[m[1]]}（材質記載なし）", f"{_n(m[2])}φ")),
    ("配管", re.compile(rf"^(通気|槽通気|排水|給水|給湯)\s*({N})\s*(HTVP|DVLP|VP|A)\b"),
     lambda m: (f"{m[1]} {MATERIAL.get(m[3], m[3])}", f"{_n(m[2])}{m[3]}")),
    ("配管", re.compile(rf"^(W|R)\s+({N})\s*(A|[φΦ])"),
     lambda m: (f"{PIPE_SYS[m[1]]}管", f"{_n(m[2])}{'φ' if m[3] in 'φΦ' else 'A'}")),
    ("配管", re.compile(rf"^({N})\s*(HTVP|DVLP|VP)\b"),
     lambda m: (f"系統記号なし {MATERIAL[m[2]]}", f"{_n(m[1])}{m[2]}")),
    ("配管", re.compile(rf"^({N})\s*A\b"),
     lambda m: ("系統記号なし A呼称(鋼管系)", f"{_n(m[1])}A")),
    ("弁", re.compile(r"^(GV|BV|CV|SV|FV)\s*(\d+)"),
     lambda m: (VALVE[m[1]], f"{m[2]}A")),
    ("弁", re.compile(r"^減圧弁\(圧力計付\)\s*(\d+)"),
     lambda m: ("減圧弁(圧力計付)", f"{m[1]}A")),
    ("継手・付属", re.compile(r"^(満水試験兼用)?伸縮継手\s*(\d+)"),
     lambda m: ("満水試験兼用伸縮継手" if m[1] else "伸縮継手", f"{m[2]}A")),
    ("継手・付属", re.compile(r"^(ねじ式)?CO\s*(\d+)"),
     lambda m: ("掃除口(CO)" + ("・ねじ式" if m[1] else ""), f"{m[2]}A")),
    ("継手・付属", re.compile(rf"^(?:GD\s+)?LSTL?\s*({N})\s*[×xX]\s*({N})"),
     lambda m: ("排水金具(LST)", f"{_n(m[1])}×{_n(m[2])}")),
    ("継手・付属", re.compile(r"^ST\s*(\d+)$"), lambda m: ("ST（トラップ類）", f"{m[1]}A")),
    ("衛生器具・ます", re.compile(r"^鋳鉄製マンホール\s*[φΦ]?(\d+)"),
     lambda m: ("鋳鉄製マンホール", f"φ{m[1]}")),
    ("衛生器具・ます",
     re.compile(r"^(散水栓|掃除流し|洗面器|大便器|小便器|汚物流し|電気温水器)"),
     lambda m: (m[1], "")),
    ("建築付帯", re.compile(rf"^竪樋\s*({N})\s*[φΦ]"), lambda m: ("竪樋", f"{_n(m[1])}φ")),
    ("ダクト（丸）", re.compile(rf"^({N})\s*[φΦ]\s*\(t[\d.]+\)"),
     lambda m: ("系統記号なし（丸・板厚指定）", f"{_n(m[1])}φ")),
    ("ダクト（角）", re.compile(rf"^({N})\s*[×xX]\s*({N})\s+\d?FL"),
     lambda m: ("系統記号なし（角・要確認）", f"{_n(m[1])}×{_n(m[2])}")),
    ("ダクト（丸）", re.compile(rf"^({N})\s*[φΦ]\s*(?:\d?FL|$)"),
     lambda m: ("系統記号なし（丸・要確認）", f"{_n(m[1])}φ")),
]

# 「ラベルらしいのに型に載らなかった」を捕まえる網。0件と「読めていない」を
# 混同させないため、これに掛かったものは捨てずに呼び出し側へ返す。
_SUSPECT = re.compile(rf"({N}\s*[φΦ]|{N}\s*[×xX]\s*{N}|{N}\s*(VP|A|HTVP|DVLP)\b)")
# 建築の仕上げ記号・注記など、設備の拾い出しの対象外と分かっているもの。
_IGNORE = re.compile(
    r"^(L\d|ぶL|天L|-天|-GR|SC\d|CH=|H=|▼|【|■|□|※|以降|但し|取付|吊|振れ|防火|延焼"
    r"|階高|凡|例|区画|キープラン|用途|記号|使用|備考|確認図|承認|\(|（)"
    r"|GR12|GH9|KK7|GF21|合板|ガラス|目地|ｶﾞﾗｽ|点検|ﾊﾟﾈﾙ|パネル|グレーチング"
    r"|ウレタン|フラットバー|柱芯|通り芯|見切|⾒切|継手|以下|以上|版使"
)
_OUTSCOPE = re.compile(r"^(600×600|450×450|5×30|[\d.]+×[\d.]+$)")
# タイトル欄（図枠下帯）。ここの文字は図面名・図番・日付で、部材ではない。
TITLE_BAND_Y = 0.935


def _level_mm(text: str) -> float | None:
    """取付高さ FL+◯◯ を mm で返す。階数つき（2FL+500）は階の分は持たない。"""
    m = HEIGHT.search(text)
    if not m:
        return None
    try:
        v = float(_n(m[3]))
    except ValueError:
        return None
    return -v if m[2] == "-" else v


def _airflow_near(line: TextLine, flows: list[tuple[str, TextLine]]) -> str | None:
    """ラベルのすぐ下にある [◯ m³/h] を結びつける。吹出口の風量は別行に書かれる。"""
    cx = (line.x + line.x1) / 2
    best, best_d = None, 1e9
    for val, fl in flows:
        dy = fl.y - line.y1
        dx = abs((fl.x + fl.x1) / 2 - cx)
        if -0.004 <= dy <= 0.014 and dx <= 0.026:
            d = dy + dx * 0.3
            if d < best_d:
                best, best_d = val, d
    return best


def classify_line(text: str) -> tuple[str, str, str] | None:
    """1行を (分類, 品目, 仕様) にする。型に載らなければ None。"""
    for cat, rx, fn in _RULES:
        m = rx.match(text)
        if m:
            name, spec = fn(m)  # type: ignore[operator]
            return cat, name, spec
    return None


def extract(
    drawing: Drawing, *, report: FrameReport | None = None
) -> tuple[list[TakeoffItem], list[tuple[int, str]]]:
    """ベクターPDFの印字から拾い出す。

    Returns:
        (items, unread) — unread は「ラベルらしいのに型に載らなかった行」
        （ページ番号, 原文）。**必ず呼び出し側で画面まで運ぶこと。**
        黙って捨てると「0件」と「読めていない」の区別が消える。
    """
    rep = report or detect_frame(drawing)
    items: list[TakeoffItem] = []
    unread: list[tuple[int, str]] = []

    for page in drawing.pages:
        if not page.text_lines:
            continue
        kind = sheet_kind(page)
        flows = [
            (m[1], ln) for ln in page.text_lines if (m := AIRFLOW.search(ln.text))
        ]
        # (品目, 仕様, 取付高さ) ごとに、同じページの印字箇所を数える
        bucket: dict[tuple[str, str, str, float | None], list[TextLine]] = defaultdict(list)
        extra: dict[tuple[str, str, str, float | None], str] = {}
        for ln in page.text_lines:
            if ln.y > TITLE_BAND_Y or rep.is_frame(kind, ln):
                continue
            hit = classify_line(ln.text)
            if hit is None:
                if (
                    _SUSPECT.search(ln.text)
                    and not _IGNORE.search(ln.text)
                    and not _OUTSCOPE.match(ln.text)
                    and len(ln.text) < 45
                ):
                    unread.append((page.page, ln.text))
                continue
            cat, name, spec = hit
            key = (cat, name, spec, _level_mm(ln.text))
            bucket[key].append(ln)
            af = _airflow_near(ln, flows) or (
                m[1] if (m := AIRFLOW.search(ln.text)) else None
            )
            if af and key not in extra:
                extra[key] = af

        for (cat, name, spec, level), lines in bucket.items():
            first = lines[0]
            af = extra.get((cat, name, spec, level))
            items.append(
                TakeoffItem(
                    page=page.page,
                    name=name,
                    spec=spec or None,
                    quantity=float(len(lines)),
                    unit=UNIT.get(cat, "箇所"),
                    category=cat,
                    # 印字を機械で数えた＝何度かけても同じ数になる。ただし数えたのは
                    # 「ラベル」であって部材ではないので、確定は人。
                    confidence=0.9,
                    source="vector_text",
                    qty_basis="count",
                    raw_name=first.text,
                    level_mm=level,
                    location=f"風量 {af} m³/h" if af else None,
                    bbox=BBox(
                        x0=first.x * page.width, y0=first.y * page.height,
                        x1=first.x1 * page.width, y1=first.y1 * page.height,
                    ),
                )
            )
    return items, unread
