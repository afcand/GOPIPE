"""凡例の図形を手本にして、ベクター図の記号を数える（決定的・API 0回）。

なぜ画像のテンプレートマッチではなくベクターの照合か:
  ベクター(CAD)PDFでは、同じ記号は同じ図形ブロックとして、同じ相対座標で描かれる。
  だから画像に落として相関を取る（symbol_match）より、図形そのものを突き合わせた
  ほうが確かで速い。実測(2026-08-19 A3スキャン)では画像側は排煙口 2/6 個しか
  拾えなかったが、あれは「スキャン図にはこれしか手が無い」からで、ベクター図には
  もっと確かな手がある。

  ⚠ スキャン図（テキスト層なし）には効かない。そちらは従来どおり
  symbol_match / cv_count の担当。

どう照合するか（点集合の照合）:
  1. 記号を「小さな図形の集まり」として持つ。手本は凡例のシンボル欄から採る。
  2. 手本のうち**いちばん珍しい形**を目印にして、図面上のその出現位置ごとに
     「手本の残りの図形が、あるべき相対位置に居るか」を数える。
  3. 一定割合そろっていれば1個と数える。
  この作りなら、記号の上にダクトの線が重なって図形が増えても平気（余分は無視する）。
  逆に、線に食われて図形が欠けたぶんは取りこぼす。だから **取りこぼしうる前提で
  「そろい具合」を必ず併記する**。数え切れないまま「数えました」とは言わない。

回転:
  ダクトは縦にも横にも走るので、記号は90度ずつ回って置かれる。手本を4方向に
  回して全部を照合する。鏡像は今のところ見ていない。
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

from .models import TakeoffItem

# 図形の座標をこの単位に丸めて突き合わせる（pt）。CADの配置は正確なので細かくてよい。
GRID = 0.5
# 手本のうち、この割合の図形がそろっていれば1個と数える。
MATCH_RATIO = 0.7
# 同じ記号とみなす距離（pt）。近すぎる当たりは1個に畳む。
DEDUPE_PT = 6.0
# 手本として受け付ける大きさ（pt）。小さすぎるのは文字、大きすぎるのは図そのもの。
MIN_SIZE, MAX_SIZE = 4.0, 60.0
# 手本に要る図形の数。1〜2本の線は図面のどこにでもあり、目印にならない。
MIN_PARTS = 3


def _points(item) -> list[tuple[float, float]]:
    """描画コマンド1つ分の座標を取り出す（線・矩形・ベジエ・四辺形）。"""
    out: list[tuple[float, float]] = []
    for v in item[1:]:
        if hasattr(v, "x") and hasattr(v, "y"):
            out.append((v.x, v.y))
        elif hasattr(v, "x0"):
            out += [(v.x0, v.y0), (v.x1, v.y1)]
        elif hasattr(v, "ul"):  # Quad
            out += [(v.ul.x, v.ul.y), (v.lr.x, v.lr.y)]
    return out


def _rotate(pts: list[tuple[float, float]], quarter: int) -> list[tuple[float, float]]:
    """90度単位の回転。図面の記号はダクトの向きに合わせて回って置かれる。"""
    out = pts
    for _ in range(quarter % 4):
        out = [(-y, x) for x, y in out]
    return out


def _unrotate(pt: tuple[float, float], quarter: int) -> tuple[float, float]:
    """回した座標を紙の座標へ戻す。

    照合は「図面のほうを回す」やり方で行うので、見つけた位置は回った空間の値に
    なっている。戻さないと紙の外の座標が出るうえ、回転違いの同じ記号が別物に見えて
    1個が4個に化ける。
    """
    x, y = pt
    for _ in range(quarter % 4):
        x, y = y, -x
    return x, y


@dataclass
class Part:
    """記号を構成する図形1つ。形（丸めた座標列のハッシュ）と、記号内での位置。"""

    key: str
    dx: float
    dy: float


def _part_key(ops: list[tuple[str, list[tuple[float, float]]]]) -> str:
    """図形の「形」を表す鍵。自分の左上を原点にして丸めるので、位置に依らない。"""
    if not ops:
        return ""
    xs = [x for _, pts in ops for x, _ in pts]
    ys = [y for _, pts in ops for _, y in pts]
    ox, oy = min(xs), min(ys)
    body = [
        (op, tuple((round((x - ox) / GRID), round((y - oy) / GRID)) for x, y in pts))
        for op, pts in ops
    ]
    body.sort()
    return hashlib.md5(repr(body).encode()).hexdigest()[:12]


def parts_of(drawings, *, quarter: int = 0) -> list[tuple[str, float, float]]:
    """描画物の一覧を (形の鍵, x, y) に変換する。quarter は回転（0..3）。"""
    out: list[tuple[str, float, float]] = []
    for dr in drawings:
        ops: list[tuple[str, list[tuple[float, float]]]] = []
        for it in dr.get("items", []):
            pts = _points(it)
            if pts:
                ops.append((it[0], _rotate(pts, quarter)))
        if not ops:
            continue
        xs = [x for _, pts in ops for x, _ in pts]
        ys = [y for _, pts in ops for _, y in pts]
        out.append((_part_key(ops), min(xs), min(ys)))
    return out


@dataclass
class SymbolTemplate:
    """凡例から採った記号の手本。"""

    name: str
    width: float
    height: float
    # 回転ごとの部品リスト（0/90/180/270 度）
    by_quarter: dict[int, list[Part]] = field(default_factory=dict)

    @property
    def parts(self) -> int:
        return len(self.by_quarter.get(0, []))


@dataclass
class SymbolHit:
    """図面上で見つけた記号1個。"""

    name: str
    x: float
    y: float
    score: float   # 手本の図形がどれだけそろっていたか（0..1）
    quarter: int


def build_template(name: str, drawings) -> SymbolTemplate | None:
    """凡例のシンボル欄の描画物から手本を作る。"""
    base = parts_of(drawings, quarter=0)
    if len(base) < MIN_PARTS:
        return None
    xs0 = [x for _, x, _ in base]
    ys0 = [y for _, _, y in base]
    w = max(xs0) - min(xs0)
    h = max(ys0) - min(ys0)
    if not (MIN_SIZE <= max(w, h) <= MAX_SIZE):
        return None
    tpl = SymbolTemplate(name=name, width=w, height=h)
    for q in range(4):
        pl = parts_of(drawings, quarter=q)
        ox, oy = min(x for _, x, _ in pl), min(y for _, _, y in pl)
        tpl.by_quarter[q] = [Part(k, x - ox, y - oy) for k, x, y in pl]
    return tpl


def find_symbols(
    drawings, templates: list[SymbolTemplate], *, match_ratio: float = MATCH_RATIO
) -> list[SymbolHit]:
    """図面の描画物から、手本に合う記号を探して位置を返す。"""
    hits: list[SymbolHit] = []
    # 回転ごとに図面側の索引を作る。手本を回すのではなく図面を回すと、
    # 図面側の索引を4回作るだけで済む（手本の数だけ回すより速い）。
    index_by_q: dict[int, dict[str, list[tuple[float, float]]]] = {}
    for q in range(4):
        idx: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for k, x, y in parts_of(drawings, quarter=q):
            idx[k].append((x, y))
        index_by_q[q] = idx

    for tpl in templates:
        found: list[SymbolHit] = []
        for q, parts in tpl.by_quarter.items():
            idx = index_by_q[q]
            if not parts:
                continue
            # いちばん珍しい形を目印にする。ありふれた線を目印にすると当たりが爆発する。
            anchor = min(parts, key=lambda p: len(idx.get(p.key, ())) or 10**9)
            spots = idx.get(anchor.key, ())
            if not spots or len(spots) > 4000:
                continue
            need = max(MIN_PARTS, math.ceil(len(parts) * match_ratio))
            for ax, ay in spots:
                ox, oy = ax - anchor.dx, ay - anchor.dy
                got = 0
                for p in parts:
                    tx, ty = ox + p.dx, oy + p.dy
                    for px, py in idx.get(p.key, ()):
                        if abs(px - tx) <= GRID * 2 and abs(py - ty) <= GRID * 2:
                            got += 1
                            break
                if got >= need:
                    # 位置は目印にした図形の実座標を紙の向きへ戻して持つ
                    px_, py_ = _unrotate((ax, ay), q)
                    found.append(SymbolHit(tpl.name, px_, py_, got / len(parts), q))
        hits += found
    return _dedupe(hits)


def _dedupe(hits: list[SymbolHit]) -> list[SymbolHit]:
    """近すぎる当たりを1個に畳む。回転違いで同じ場所が二重に当たることがある。"""
    out: list[SymbolHit] = []
    for h in sorted(hits, key=lambda h: -h.score):
        if all(abs(h.x - o.x) > DEDUPE_PT or abs(h.y - o.y) > DEDUPE_PT for o in out):
            out.append(h)
    return out

def split_cell(drawings, *, gap: float = 3.0) -> list[list]:
    """凡例の1セルに複数の記号が並んでいるとき、近さで分ける。

    実物では VD・CD・FD/SD の欄に **角ダクト用と丸ダクト用が横に並ぶ**。まとめて
    1つの手本にすると、両方そろっている凡例そのものにしか当たらない（実測: 図面
    全体で4個しか出なかった）。ここで分けるのが肝。
    セルを横切る中心線のような細長い図形は、記号ではないので落とす。
    """
    drs = [d for d in drawings if d.get("items")]
    if not drs:
        return []
    n = len(drs)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        ri = drs[i]["rect"]
        for j in range(i + 1, n):
            rj = drs[j]["rect"]
            if (ri.x0 - gap <= rj.x1 and rj.x0 - gap <= ri.x1
                    and ri.y0 - gap <= rj.y1 and rj.y0 - gap <= ri.y1):
                a, b = find(i), find(j)
                if a != b:
                    parent[b] = a

    groups: dict[int, list] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(drs[i])

    out = []
    for g in groups.values():
        xs = [d["rect"] for d in g]
        w = max(r.x1 for r in xs) - min(r.x0 for r in xs)
        h = max(r.y1 for r in xs) - min(r.y0 for r in xs)
        if len(g) < MIN_PARTS or max(w, h) > MAX_SIZE:
            continue
        # 中心線のような細長いものは記号ではない
        if min(w, h) < 2.0 or (max(w, h) / max(min(w, h), 0.1)) > 6.0:
            continue
        out.append(g)
    return out


def templates_from_cell(name: str, drawings) -> list[SymbolTemplate]:
    """凡例の1セルから手本を作る。並んでいる記号はそれぞれ別の手本にする。"""
    out = []
    for g in split_cell(drawings):
        t = build_template(name, g)
        if t:
            out.append(t)
    if not out:
        t = build_template(name, drawings)
        if t:
            out.append(t)
    return out

# ---------------------------------------------------------------------------
# 繰り返される図形から数える（凡例と図の描き方が違っても効く経路）
# ---------------------------------------------------------------------------
# 実測(2026-09-08 NEC府中 A1ダクト図): 凡例のVDは角型・丸型の схема が描いてあるが、
# 平面図のVDはダクト幅に合わせて伸びる簡略な描かれ方で、**凡例とは別物**だった。
# 手本まるごとの照合は当たらない。一方、耐震振れ止め支持点の⊗は凡例と平面で同じ
# 図形（9.6pt の円＋斜線）で、図の中に11個あった。
#
# つまり「凡例と一致するか」ではなく「図の中で同じ形が何度も出るか」で数え、
# 名前だけを凡例から借りるのが確か。名前が引けない形は、数と大きさと場所を出して
# 人に見てもらう（勝手に名づけない）。

# 記号らしい図形の条件。線1本や巨大な図形は記号ではない。
GLYPH_MIN_PT, GLYPH_MAX_PT = 3.0, 30.0
GLYPH_MIN_ITEMS = 3
# 図の中でこの回数以上出てくる形を「繰り返し記号」とみなす。
GLYPH_MIN_COUNT = 2


@dataclass
class GlyphCount:
    """図の中で繰り返し出てくる図形と、その個数。"""

    key: str
    count: int
    width: float
    height: float
    positions: list[tuple[float, float]]
    names: list[str] = field(default_factory=list)   # 凡例から引けた名前（候補）

    @property
    def named(self) -> str:
        return " または ".join(self.names) if self.names else ""


def _is_glyph(dr) -> bool:
    r = dr["rect"]
    w, h = r.width, r.height
    if len(dr.get("items", [])) < GLYPH_MIN_ITEMS:
        return False
    if not (GLYPH_MIN_PT <= max(w, h) <= GLYPH_MAX_PT):
        return False
    # 極端に細長いものは線であって記号ではない
    return min(w, h) >= 1.0 and max(w, h) / max(min(w, h), 0.1) <= 4.0


def count_glyphs(
    drawings,
    *,
    inside=None,
    min_count: int = GLYPH_MIN_COUNT,
) -> list[GlyphCount]:
    """図の中で繰り返し出てくる図形を数える。

    inside は「図の中か」を返す関数（図枠の凡例・キープランを外すために使う）。
    None なら全部を対象にする。
    """
    buckets: dict[str, list] = defaultdict(list)
    for dr in drawings:
        if not _is_glyph(dr):
            continue
        r = dr["rect"]
        if inside is not None and not inside(r.x0, r.y0):
            continue
        k, x, y = parts_of([dr])[0]
        buckets[k].append((x, y, r))
    out = []
    for k, v in buckets.items():
        if len(v) < min_count:
            continue
        r0 = v[0][2]
        out.append(GlyphCount(
            key=k, count=len(v), width=r0.width, height=r0.height,
            positions=[(x, y) for x, y, _ in v],
        ))
    out.sort(key=lambda g: -g.count)
    return out


def name_from_legend(
    glyphs: list[GlyphCount],
    cells: list[tuple[str, list]],
    *,
    frame_counts: dict[str, int] | None = None,
    max_in_frame: int = 3,
) -> None:
    """凡例のセルに同じ形があれば、その名前を候補として付ける（その場で書き換える）。

    同じ形が複数の凡例に出るときは候補を並べる。勝手に1つへ決めない
    （VDとFD/SDは外形が同じで、違うのは小さな印だけ、ということが実際にある）。
    """
    by_key: dict[str, set[str]] = defaultdict(set)
    for name, drs in cells:
        for k, _, _ in parts_of(drs):
            by_key[k].add(name)
    for g in glyphs:
        # 凡例の見本は「図枠に1〜数個、図の中に何個も」出るもの。図枠のほうに
        # たくさんあるなら、それは見本ではなく表の飾り（罫線の端点や網掛け）。
        if frame_counts is not None and frame_counts.get(g.key, 0) > max_in_frame:
            continue
        g.names = sorted(by_key.get(g.key, ()))


# 凡例の名前として受け付けない文字（通り芯・寸法・レベル・番号だけ、など）
# 表の見出しや、縦書きが1文字ずつに割れたもの。記号の名前ではない。
_STOP_NAMES = {
    "記号", "名称", "用途", "備考", "凡例", "例", "凡", "支", "持", "図", "種",
    "シンボル", "使用材料", "使用継手", "区分", "番号", "摘要",
}
_NOT_A_NAME = re.compile(
    r"^([XY]\d{1,2}|[\d,.\s]+|\d?FL[+\-].*|[▼△▽◀▶·・].*|\(.*\)|[A-Z]-[A-Z]\'?.*"
    r"|[【〔（].*|.*[表欄]$|.*タイプ|.*箇所)$"
)
# 取付高さの注記はどこに現れても記号名ではない（▼2FL のように前に飾りが付く）
_LEVEL_NOTE = re.compile(r"\d?FL\s*[+\-]|[▼△]\s*\d?FL")


def legend_cells(page, *, inside=None, is_frame=None) -> list[tuple[str, list]]:
    """凡例らしい「名前の左に図形」の組を拾う。表の形に依存しない作り。

    凡例の版組は会社ごとに違うので、罫線や列位置を決め打ちにしない。
    「短い名前のテキストがあり、その左隣の同じ高さに小さな図形が固まっている」
    という形だけを頼りにする。

    🔴 is_frame（図枠かどうかを返す関数）を渡すこと。凡例は毎ページ同じ位置に
    刷られるので必ず図枠側にある。これを渡さないと、断面図の中の「Y3」や「▼2FL」を
    凡例の名前と取り違えて、通り芯の丸を部材として数えはじめる（実測で起きた）。
    """
    drs = [d for d in page.get_drawings() if _is_glyph(d)]
    if not drs:
        return []
    out: list[tuple[str, list]] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(sp.get("text", "") for sp in line.get("spans", [])).strip()
            if not text or len(text) > 14 or _NOT_A_NAME.match(text):
                continue
            if text in _STOP_NAMES or len(text) < 2 or _LEVEL_NOTE.search(text):
                continue
            x0, y0, x1, y1 = line["bbox"]
            if inside is not None and inside(x0, y0):
                continue      # 図の中の文字は凡例ではない
            if is_frame is not None and not is_frame(text, x0, y0):
                continue      # 毎ページ同じ位置に無いものは凡例ではない
            cy = (y0 + y1) / 2
            h = max(y1 - y0, 6.0)
            cell = [
                d for d in drs
                if x0 - 60 <= d["rect"].x0 and d["rect"].x1 <= x0 - 1
                and abs((d["rect"].y0 + d["rect"].y1) / 2 - cy) <= h * 1.2
            ]
            # 見本は数個の図形でできている。何十本もあるのは表の飾りか図そのもの。
            if 1 <= len(cell) <= 12:
                out.append((text, cell))
    return out


def glyph_items(
    page, page_no: int, *, inside=None, is_frame=None, min_count: int = 3
) -> tuple[list[TakeoffItem], list[GlyphCount]]:
    """繰り返し記号を数えて、名前が引けたものだけを拾い出しの行にする。

    Returns: (名前が引けた行, 名前が引けなかった繰り返し図形)

    名前が引けないものを勝手に名づけない。数と大きさと場所だけを渡して人に見てもらう。
    実測では通り芯の丸や柱のような、拾い出しと関係ない形も繰り返し出てくる。
    """
    drs = page.get_drawings()
    if not drs:
        return [], []
    cells = legend_cells(page, inside=inside, is_frame=is_frame)
    glyphs = count_glyphs(drs, inside=inside, min_count=min_count)
    frame_counts: dict[str, int] = defaultdict(int)
    if inside is not None:
        for dr in drs:
            if _is_glyph(dr) and not inside(dr["rect"].x0, dr["rect"].y0):
                frame_counts[parts_of([dr])[0][0]] += 1
    name_from_legend(glyphs, cells, frame_counts=dict(frame_counts))

    items: list[TakeoffItem] = []
    unnamed: list[GlyphCount] = []
    for g in glyphs:
        if not g.names:
            unnamed.append(g)
            continue
        items.append(TakeoffItem(
            page=page_no,
            name=g.named,
            spec=f"{g.width:.0f}×{g.height:.0f}pt の記号",
            quantity=float(g.count),
            unit="個",
            category="記号カウント",
            # 数えること自体は決定的だが、同じ形を使う別記号を巻き込みうる。
            # 候補が複数あるときは、そのぶん確からしさを落とす。
            confidence=0.75 if len(g.names) == 1 else 0.55,
            source="glyph_count",
            qty_basis="count",
            raw_name=g.key,
        ))
    return items, unnamed


def count_from_pdf(path: str, drawing, report) -> tuple[list[TakeoffItem], dict[int, list[GlyphCount]]]:
    """PDF全ページの繰り返し記号を数える。図枠の居場所は report から決める。

    Returns: (名前が引けた行, {ページ: 名前が引けなかった繰り返し図形})
    """
    import fitz

    from .frame_filter import sheet_kind

    items: list[TakeoffItem] = []
    unnamed: dict[int, list[GlyphCount]] = {}
    by_page = {p.page: p for p in drawing.pages}
    doc = fitz.open(path)
    try:
        for i, page in enumerate(doc, start=1):
            dp = by_page.get(i)
            if dp is None or not dp.text_lines:
                continue
            kind = sheet_kind(dp)
            w = page.rect.width or 1.0
            h = page.rect.height or 1.0

            def inside(x: float, y: float, _k=kind, _w=w, _h=h) -> bool:
                return not report.in_frame_zone(_k, x / _w, y / _h)

            def is_frame(text: str, x: float, y: float, _k=kind, _w=w, _h=h) -> bool:
                return any(
                    key[1] == text
                    and abs(key[2] - x / _w) < 0.002
                    and abs(key[3] - y / _h) < 0.002
                    for key in report.keys if key[0] == _k
                )

            it, un = glyph_items(page, i, inside=inside, is_frame=is_frame)
            items += it
            if un:
                unnamed[i] = un
    finally:
        doc.close()
    return items, unnamed
