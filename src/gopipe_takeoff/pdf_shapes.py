"""紙に載っている図形だけを取り出す。

🔴 `page.get_drawings()` は **cropbox の外に描かれた線も返す**。画面にも印刷物にも
出ていないのに、数えると数量に入る。範囲を指して拾うときに実害が出た
（2026-09-16 実測: 右下4分の1を指したのに、ダクトは1枚目まるごとと同じ107区間・104.6m を
測っていた＝文字は範囲で絞れているのに線だけ絞れていない）。

BK平塚では、画像が紙の外の座標に置かれていた例もある（[[bk-hiratsuka-vector-takeoff]]）。
「紙の中だけを見る」は、範囲指定に限らず効く。
"""
from __future__ import annotations


def visible_drawings(page) -> list[dict]:
    """そのページの紙面と重なる図形だけ返す。"""
    R = page.rect
    out: list[dict] = []
    for d in page.get_drawings():
        r = d.get("rect")
        if r is None:
            out.append(d)          # 位置が分からないものは落とさない（判断材料が無い）
            continue
        # 水平・垂直の線は高さ0・幅0になるため、交差の面積では判定できない
        if r.x1 >= R.x0 and r.x0 <= R.x1 and r.y1 >= R.y0 and r.y0 <= R.y1:
            out.append(d)
    return out
