#!/usr/bin/env python3
"""GOPIPE 拾い出し CLI（配管・設備工事向け）。

使い方:
    # mock で一気通貫（API キー・実 PDF 不要、配管サンプルを返す）
    GOPIPE_LLM_PROVIDER=mock python scripts/run_takeoff.py --input samples/dummy_設備図.pdf --out out/

    # 本番（Claude）
    python scripts/run_takeoff.py --input path/to/setsubi_zumen.pdf --out out/

LLM プロバイダは GOPIPE_LLM_PROVIDER 環境変数で切替（claude | openai | mock）。

--grid N で 1 ページを N×N タイルに分割して個別抽出（API call は N^2 倍だが
A1 等の大判設備図で細部精度が上がる）。--two-pass で漏れ確認パスを追加。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _setup_sys_path() -> None:
    root = Path(__file__).resolve().parents[1]  # GOPIPE/
    sys.path.insert(0, str(root / "src"))
    sys.path.insert(0, str(root / "shared"))


def main() -> int:
    parser = argparse.ArgumentParser(description="GOPIPE 設備拾い出し")
    parser.add_argument("--input", required=True, help="入力 PDF パス（設備図）")
    parser.add_argument("--out", default="out/", help="出力ディレクトリ")
    parser.add_argument(
        "--grid", type=int, default=1, help="N×N タイル分割（API call は N^2 倍）。既定 1"
    )
    parser.add_argument(
        "--no-llm", action="store_true", default=False,
        help="画像認識を使わず、ベクター(CAD)PDFの印字だけで拾う。API呼び出し0回・費用0。"
             "大判が何枚もあると1枚あたりのタイル予算が足りず実効解像度が落ちるため、"
             "ベクター図ではこちらのほうが確かなことが多い",
    )
    parser.add_argument(
        "--two-pass", action="store_true", default=False,
        help="2 パス抽出。Pass1 通常 → Pass2 漏れ確認（+1 call/ページ）",
    )
    args = parser.parse_args()
    if args.grid < 1:
        parser.error("--grid は 1 以上の整数")

    _setup_sys_path()
    # .env をベストエフォートで読む（override=True：空の環境変数に上書きされないように）
    try:
        from dotenv import load_dotenv

        load_dotenv(override=True)
    except Exception:
        pass

    from gopipe_takeoff import run_takeoff

    result = run_takeoff(args.input, args.out, grid=args.grid, two_pass=args.two_pass,
                         use_llm=not args.no_llm)
    print(f"✓ {len(result.items)} 件の拾い出し項目を抽出しました "
          f"(LLM call {result.llm_calls} 回)")
    if result.gaps:
        print(f"  拾えていないもの {len(result.gaps)} 件（Excelの『拾えていないもの』シート）:")
        for g in result.gaps:
            print(f"    - {g.line()}")
    if result.unread_labels:
        print(f"  型に載らなかったラベル {len(result.unread_labels)} 件")
    for f in result.failures:
        print(f"  ⚠ {f}")
    print(f"  Excel: {result.excel_path}")
    if result.marker_pdf_path:
        print(f"  Marker PDF: {result.marker_pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
