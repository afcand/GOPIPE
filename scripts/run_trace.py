#!/usr/bin/env python3
"""図面の線を追跡して区間と延長を出す（実験・ローカル専用）。

🔴**拾い出しのパイプラインには繋いでいない。** 理由:
  - 実測(2026-08-20 資料⑤)で、人が定規で測った 62.7m に対して 46.2m ＝ **74%** しか届かない
  - 1枚あたり 30〜50秒かかる（サーバレスの予算に載らない）
  この段階で見積へ流すと「もっともらしいが足りない数字」になる。
  0 と正直に言うほうが、7割の数字より安全（この製品の一貫した方針）。

使い方:
    python scripts/run_trace.py --input 図面.pdf --mm-per-px 6.0
    python scripts/run_trace.py --input 図面.pdf --calib-box 858,1155,908,1205 --calib-mm 300
      → 図面に印刷された既知寸法の記号から mm/px を自己校正する

なぜ縮尺を自己校正するか: 図枠の「S=1/50」を信じてはいけない。実測で
1/50 表記の図面が実効 1/72.6（-31%）だった（コピー機の fit-to-page 等で一様に伸縮する）。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

# 色相の窓（color_sample と同じ定義）
BANDS = {
    "赤": (340.0, 375.0), "橙茶": (13.0, 45.0), "黄": (45.0, 78.0), "緑": (78.0, 175.0),
    "水": (175.0, 196.0), "青": (196.0, 230.0), "藤紫": (230.0, 262.0),
    "紫": (262.0, 290.0), "赤紫": (290.0, 340.0),
}


def _setup_path() -> None:
    root = Path(__file__).resolve().parents[1]
    for p in ("src", "shared"):
        sys.path.insert(0, str(root / p))


def _native(pdf: str) -> bytes:
    import fitz

    doc = fitz.open(pdf)
    page = doc[0]
    imgs = page.get_images(full=True)
    if not imgs:
        raise SystemExit("この図面には埋め込み画像がありません（ベクターPDFは未対応）")
    raw = doc.extract_image(imgs[0][0])["image"]
    doc.close()
    return raw


def _calibrate(raw: bytes, box: str, real_mm: float) -> float:
    """図面に印刷された既知寸法の記号から mm/px を出す。"""
    import numpy as np
    from PIL import Image

    x0, y0, x1, y1 = (int(v) for v in box.split(","))
    a = np.asarray(Image.open(io.BytesIO(raw)).convert("L"), dtype=np.float64)[y0:y1, x0:x1]
    ink = a < 170
    cols = np.nonzero(ink.any(axis=0))[0]
    rows = np.nonzero(ink.any(axis=1))[0]
    if not cols.size or not rows.size:
        raise SystemExit("校正用の枠にインクが見つかりません")
    side = ((cols[-1] - cols[0] + 1) + (rows[-1] - rows[0] + 1)) / 2
    return real_mm / side


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--mm-per-px", type=float, default=None)
    ap.add_argument("--calib-box", default="", help="x0,y0,x1,y1（既知寸法の記号の枠）")
    ap.add_argument("--calib-mm", type=float, default=0.0, help="その記号の実寸(mm)")
    ap.add_argument("--colors", default="青,橙茶,赤,緑")
    ap.add_argument("--min-m", type=float, default=0.5, help="この長さ未満は継手・記号の内部とみなす")
    a = ap.parse_args()

    _setup_path()
    from gopipe_takeoff.trace import trace_color

    raw = _native(a.input)
    mm = a.mm_per_px
    if a.calib_box and a.calib_mm:
        mm = _calibrate(raw, a.calib_box, a.calib_mm)
        print(f"自己校正: {mm:.3f} mm/px（縮尺 1/{mm / 0.127:.0f} 相当・200dpi前提）")
    if not mm:
        print("⚠ 縮尺が未指定です。長さは px のまま出します（--mm-per-px か --calib-box を指定）")

    result = {}
    total = 0.0
    for name in [c.strip() for c in a.colors.split(",") if c.strip()]:
        if name not in BANDS:
            print(f"⚠ 知らない色: {name}（{'/'.join(BANDS)}）")
            continue
        lo, hi = BANDS[name]
        runs = trace_color(raw, lo, hi, mm_per_px=mm)
        keep = [r for r in runs if (r["length_m"] or 0) >= a.min_m] if mm else runs
        sub = sum(r["length_m"] or 0 for r in keep)
        total += sub
        result[name] = {"runs": len(runs), "kept": len(keep), "length_m": round(sub, 2),
                        "paths": keep}
        print(f"■ {name}: {len(runs)}区間（{a.min_m}m以上 {len(keep)}本）"
              f"{f' / 合計 {sub:.1f}m' if mm else ''}")
    if mm:
        print(f"\n合計 {total:.1f}m")
        print("⚠ この数字は下限です。平面図に写らない立ち上がり・立下りは含みません"
              "（実測で人の答えの74%）。見積へそのまま入れないこと。")
    if a.out:
        Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
