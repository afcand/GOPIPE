"""冷媒配管サイズ表を図枠から読む（平面の丸記号→口径の読み替え表）。

なぜ数量として出さないか:
  空調衛生の配管図では、冷媒のルートに丸記号（A〜K・①〜⑱）だけが振ってあり、
  口径は図枠の「冷媒配管サイズ表」で引く。実測(2026-09-08 NEC府中 配管図8枚)では、
  平面図側の記号は**図形で描かれていてテキスト層に入っていない**（文字で拾えたのは
  断面図の中の5件だけ）。だから記号と表を突き合わせる仕事は、記号を図形として
  数えられるようになるまで完結しない。

  そこで本モジュールは**表を読むところまでを確実にやる**。表は毎ページ同じ位置に
  刷られているので frame_filter が図枠と判定しており、拾い出しの数量には入らない。
  ここで読み直して参照表として人に渡す。人はこれを見ながら記号を拾える。

  🔴 表の呼び径を配管の数量として数えてはいけない。実際に一度そうなりかけた
  （8ページ×76件＝608件の架空計上）。数量は frame_filter が止めている。
"""
from __future__ import annotations

import re
import string
from collections import defaultdict
from dataclasses import dataclass

from .frame_filter import FrameReport, sheet_kind
from .frame_filter import detect as detect_frame
from .models import Drawing

_PHI = re.compile(r"^[\d.]+\s*[φΦ]$")
# 表の行を見分ける帯の高さ（ページ高さ比）。行間より狭く、字の高さより広く。
_BAND = 0.0022


@dataclass
class RefrigerantSize:
    """冷媒配管サイズ表の1行。記号は図面上の丸数字・丸英字。"""

    symbol: str
    liquid: str          # 液管
    gas: str             # ガス管
    high_low_gas: str = ""  # 高低圧ガス管（2列の表には無い）


def parse_size_table(
    drawing: Drawing, *, report: FrameReport | None = None
) -> list[RefrigerantSize]:
    """図枠に刷られた冷媒配管サイズ表を読む。見つからなければ空リスト。

    表は2つ並んでいることがある（左=2列の A〜K、右=3列の ①〜⑱）。記号そのものは
    丸で囲った図形として描かれていてテキストに出ない行があるため、**表の並び順から
    復元する**。並び順は図面の様式で決まっているので、これで足りる。
    """
    rep = report or detect_frame(drawing)
    left: list[list[str]] = []
    right: list[list[str]] = []

    for page in drawing.pages:
        if "冷媒配管" not in (page.text or ""):
            continue
        kind = sheet_kind(page)
        bands: dict[int, list] = defaultdict(list)
        for ln in page.text_lines:
            # 表は図枠にある。図の中の口径表記を巻き込まないよう図枠側だけを見る。
            if not rep.is_frame(kind, ln) or not _PHI.match(ln.text):
                continue
            bands[round(ln.y / _BAND)].append(ln)
        for _, row in sorted(bands.items()):
            row.sort(key=lambda l: l.x)
            vals = [l.text.replace(" ", "") for l in row]
            # 2つの表が同じ帯に並ぶ行は 2+3=5 個になる。左が2列・右が3列。
            if len(vals) == 5:
                pair, triple = vals[:2], vals[2:]
            elif len(vals) == 3:
                pair, triple = None, vals
            elif len(vals) == 2:
                pair, triple = vals, None
            else:
                continue
            if pair and pair not in left:
                left.append(pair)
            if triple and triple not in right:
                right.append(triple)
        if right:
            break  # 表は全ページ同じ。1枚読めば足りる

    out = [RefrigerantSize(f"({i})", v[0], v[1], v[2]) for i, v in enumerate(right, 1)]
    out += [
        RefrigerantSize(string.ascii_uppercase[i], v[0], v[1])
        for i, v in enumerate(left)
        if i < 26
    ]
    return out
