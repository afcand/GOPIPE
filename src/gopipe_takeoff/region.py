"""図面の「指した範囲」だけを切り出す。

1枚を丸ごと読ませると、大判ほど実効解像度が落ち、断面図や別階の図が混ざり、
割り方ひとつで数量が変わる（2026-09-16 実測: 同じA3スキャンで 4/6/9/16分割の
それぞれで冷水管が 17m/12m/4.5m/13m になった）。人が範囲を指せば、その3つとも消える。

やり方は「範囲つきで拾う専用の経路を作る」のではなく、**範囲だけのPDFを作って
既存の経路へ流す**。こうすると印字の抽出・図枠の判定・記号の計数・ダクトの実測が
そのまま効く（実測: 切り出すと文字層も 3,865字→961字 に絞られる）。
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Region:
    """ページ内の範囲。左上を (0,0)、右下を (1,1) とした比率で持つ。

    比率にするのは、画面が何dpiで図面を表示していても同じ値を送れるようにするため
    （紙のサイズも表示倍率も、人が囲んだ位置には関係がない）。
    """

    page: int = 1
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 1.0
    y1: float = 1.0

    @classmethod
    def from_dict(cls, d: dict | None) -> "Region | None":
        if not d:
            return None
        try:
            r = cls(
                page=max(1, int(d.get("page") or 1)),
                x0=float(d.get("x0", 0.0)), y0=float(d.get("y0", 0.0)),
                x1=float(d.get("x1", 1.0)), y1=float(d.get("y1", 1.0)),
            )
        except (TypeError, ValueError):
            return None
        return r.normalized()

    def normalized(self) -> "Region":
        x0, x1 = sorted((self.x0, self.x1))      # 右上から左下へ引いても同じ範囲
        y0, y1 = sorted((self.y0, self.y1))
        cl = lambda v: min(1.0, max(0.0, v))     # noqa: E731
        return Region(self.page, cl(x0), cl(y0), cl(x1), cl(y1))

    @property
    def is_whole_page(self) -> bool:
        return (self.x0, self.y0, self.x1, self.y1) == (0.0, 0.0, 1.0, 1.0)

    def label(self) -> str:
        return (f"p{self.page} 範囲 "
                f"{self.x0*100:.0f},{self.y0*100:.0f}〜{self.x1*100:.0f},{self.y1*100:.0f}%")


MIN_SIDE = 0.01          # これより細い範囲は、指がすべっただけとみなす


def crop(pdf_path: str | Path, region: Region) -> Path:
    """範囲だけのPDFを作って返す。1ページだけの図面になる。"""
    import fitz

    src = Path(pdf_path)
    doc = fitz.open(src)
    if region.page > doc.page_count:
        doc.close()
        raise ValueError(f"{doc.page_count}ページの図面に {region.page}ページ目は指定できません")
    page = doc[region.page - 1]
    r = page.rect
    W, H = r.width, r.height
    box = fitz.Rect(
        r.x0 + W * region.x0, r.y0 + H * region.y0,
        r.x0 + W * region.x1, r.y0 + H * region.y1,
    )
    if box.width < W * MIN_SIDE or box.height < H * MIN_SIDE:
        doc.close()
        raise ValueError("範囲が小さすぎます。もう少し広く囲んでください")

    # 🔴 cropbox は「紙を切る」だけでなく、その外の文字も抽出から外れる。
    # 印字を数える経路がそのまま「範囲内だけ」になるのはこのおかげ。
    page.set_cropbox(box)
    out = Path(tempfile.gettempdir()) / f"gopipe_region_{src.stem[:24]}_{region.page}.pdf"
    doc.select([region.page - 1])
    doc.save(out)
    doc.close()
    return out
