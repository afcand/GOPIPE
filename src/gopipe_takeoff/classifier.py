from __future__ import annotations

from .dictionary import TakeoffDictionary
from .models import TakeoffItem


def classify(items: list[TakeoffItem], dictionary: TakeoffDictionary) -> list[TakeoffItem]:
    """辞書ベースで category / unit を補い、表記ゆれだけを正規名にそろえる。

    名称を正規名へ書き換えるのは **完全一致のとき（＝表記ゆれと確定できるとき）だけ**。
    部分一致で書き換えると、AIが正しく読んだ「逆止弁 DN20」が別部材の名前に
    化けて表が嘘になり、同じ名前が並んで重複チェックまで誤爆する。
    raw_name には AI が実際に読んだ名前を必ず残す（学習の鍵になる）。

    Side-effect ではなく新しいリストを返す。
    """
    out: list[TakeoffItem] = []
    for it in items:
        entry, exact = dictionary.resolve(it.name)
        update: dict = {"raw_name": it.raw_name or it.name}
        if entry is None:
            # 辞書に無くても、既に分かっているカテゴリは捨てない。
            # ベクター図の印字から拾った項目は図面の凡例どおりの分類を持っている。
            # それを「その他」で塗り潰すと、せっかく図面から読んだ区分が消える。
            update["category"] = it.category or "その他"
        else:
            update["category"] = entry.category
            update["unit"] = it.unit or entry.unit
            if exact:
                update["name"] = entry.canonical
        out.append(it.model_copy(update=update))
    return out
