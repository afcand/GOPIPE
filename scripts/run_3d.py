#!/usr/bin/env python3
"""図面を3Dに組み立てる（実験・ローカル専用）。

平面図の経路(trace) × 取付高さ(FL+) → 3Dの区間。
**平面図では点にしか見えない立上り・立下りが、高さの差として長さになる。**

🔴 拾い出しのパイプラインには繋いでいない。この層は推測を含む:
  - 高さの注記がどの区間に効くかは図面に書かれていない（近さと繋がりで推している）
  - 資料② 396区間のうち、注記から付いたのは84本・伝播で179本・**133本は高さ不明**
  - 高さ不明ぶんは合計に入れず別勘定で返す
  - 縮尺は図面ごとに自己校正が要る（--calib-box）
数字は必ず内訳（水平／垂直／高さ不明）で見ること。1つにまとめると推測が実測の顔をする。

    python scripts/run_3d.py --input 図面.pdf --mm-per-px 6.35 --iso out.jpg
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

BANDS = {"赤": (340.0, 375.0), "橙茶": (13.0, 45.0), "緑": (78.0, 175.0),
         "水": (175.0, 196.0), "青": (196.0, 230.0), "藤紫": (230.0, 262.0)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--mm-per-px", type=float, required=True, help="原寸1pxの実寸(mm)")
    ap.add_argument("--colors", default="青,橙茶,赤,緑")
    ap.add_argument("--out", default="")
    ap.add_argument("--iso", default="", help="等角投影の検算画像を書き出す")
    a = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    for p in ("src", "shared"):
        sys.path.insert(0, str(root / p))
    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env")
    except Exception:  # noqa: BLE001
        pass
    os.environ.setdefault("GOPIPE_LLM_PROVIDER", "claude")

    import fitz
    from PIL import Image

    from gopipe_takeoff.extractor import extract
    from gopipe_takeoff.pdf_loader import load_pdf
    from gopipe_takeoff.space3d import assemble
    from gopipe_takeoff.trace import trace_color

    # 高さの注記（AIが読む。bbox はページ座標）
    dw = load_pdf(a.input)
    items = extract(dw, two_pass=False, use_text_table=True)
    anchors = [{"x": (it.bbox.x0 + it.bbox.x1) / 2, "y": (it.bbox.y0 + it.bbox.y1) / 2,
                "level_mm": it.level_mm}
               for it in items if it.level_mm is not None and it.bbox is not None]
    print(f"高さの注記: {len(anchors)}点")
    if len(anchors) < 2:
        print("⚠ 高さの注記が足りません。この図面は3Dに組めません"
              "（実測: 5枚中3枚は FL+ の記載がゼロだった）")
        return 1

    doc = fitz.open(a.input)
    raw = doc.extract_image(doc[0].get_images(full=True)[0][0])["image"]
    doc.close()
    k = dw.pages[0].width / Image.open(io.BytesIO(raw)).width  # 原寸→ページ座標

    runs = []
    for name in [c.strip() for c in a.colors.split(",") if c.strip()]:
        if name not in BANDS:
            continue
        lo, hi = BANDS[name]
        got = trace_color(raw, lo, hi)
        for r in got:
            r["color"] = name
            r["points"] = [(x * k, y * k) for x, y in r["points"]]
        runs += got
        print(f"  経路 {name}: {len(got)}区間")

    res = assemble(runs, anchors, mm_per_px=a.mm_per_px / k)
    lv = res["runs_total"] - res["runs_unleveled"] - res["runs_propagated"]
    print(f"\n区間 {res['runs_total']}本"
          f"（注記から{lv}本・繋がりで{res['runs_propagated']}本・高さ不明{res['runs_unleveled']}本）")
    print(f"  水平          {res['horizontal_m']:>8.1f} m")
    print(f"  立上り・立下り  {res['vertical_m']:>8.1f} m  ({len(res['risers'])}本)")
    print(f"  高さ不明       {res['unassigned_m']:>8.1f} m  ← 合計に入れない")
    print(f"  高さの分かった合計 {res['total_m']:.1f} m")
    print("\n⚠ この数字を見積へそのまま入れないこと。高さの割り当ては推測を含む。")

    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        print(f"→ {a.out}")
    if a.iso:
        _iso(res, a.iso)
        print(f"→ {a.iso}")
    return 0


def _iso(data: dict, out: str) -> None:
    """等角投影で描く。**ビューアでなく検算の道具**＝宙に浮いた線や二重を目で捕まえる。"""
    from PIL import Image, ImageDraw

    segs = data["segments"]
    if not segs:
        return

    def iso(x, y, z):
        return (x - y) * 0.7071, (x + y) * 0.4082 - z * 0.8

    pts = [iso(*s["a"]) for s in segs] + [iso(*s["b"]) for s in segs]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    W, H, pad = 1500, 1000, 60
    k = min((W - 2 * pad) / max(max(xs) - min(xs), 1), (H - 2 * pad) / max(max(ys) - min(ys), 1))

    def T(p):
        return (pad + (p[0] - min(xs)) * k, H - pad - (p[1] - min(ys)) * k)

    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    zs = sorted({s["a"][2] for s in segs})
    pal = [(30, 110, 200), (220, 120, 20), (40, 160, 90), (170, 60, 170),
           (200, 40, 60), (90, 90, 90), (20, 170, 190)]
    zc = {z: pal[i % len(pal)] for i, z in enumerate(zs)}
    for s in segs:
        if s["kind"] == "h":
            d.line([T(iso(*s["a"])), T(iso(*s["b"]))], fill=zc.get(s["a"][2], (120,) * 3), width=3)
    for s in segs:
        if s["kind"] == "v":
            d.line([T(iso(*s["a"])), T(iso(*s["b"]))], fill=(255, 0, 0), width=7)
    d.text((16, 16), f"水平 {data['horizontal_m']:.1f}m / "
                     f"立上り立下り {data['vertical_m']:.1f}m（赤・{len(data['risers'])}本）", fill=(0,) * 3)
    for i, z in enumerate(zs):
        d.rectangle([16, 38 + i * 16, 40, 48 + i * 16], fill=zc[z])
        d.text((48, 36 + i * 16), f"FL+{z:.0f}", fill=(0,) * 3)
    im.save(out, "JPEG", quality=92)


if __name__ == "__main__":
    raise SystemExit(main())
