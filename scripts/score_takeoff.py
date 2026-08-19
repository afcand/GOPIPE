#!/usr/bin/env python3
"""拾い出し結果を「図面に印刷された表」の正解と突き合わせて採点する。

なぜ要るか:
  「精度が上がった」を口で言わないため。拾い出しの精度は**拾うものの種類ごとに
  全く違う**（表の員数は100%取れても、図面を数える個数は不安定、延長は推定）。
  ひとまとめの「精度◯%」は必ず嘘になるので、正解が確定している
  「図面に印刷された表」だけを物差しにする。

使い方:
    python scripts/score_takeoff.py --truth 正解表_資料④.json --items out/items.json

正解 JSON の形は tables[].rows[] に {name, spec, qty, unit}。
items JSON は TakeoffItem の配列（name/spec/quantity/unit/confidence/qty_basis）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path


def norm(s: str | None) -> str:
    """比較用の正規化。全角/半角・×/x/*・空白・大小文字の違いを潰す。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.replace("×", "x").replace("*", "x").replace("Φ", "φ").replace("φ", "φ")
    s = re.sub(r"[\s　\-_/]+", "", s)
    return s.lower()


_Q_RE = re.compile(r"q=?(\d+)m3")


def _q_value(*texts: str | None) -> str | None:
    """風量 Q=200m3/h の 200。同じ寸法の行を見分ける決め手になる。"""
    for t in texts:
        m = _Q_RE.search(norm(t))
        if m:
            return m.group(1)
    return None


def spec_hit(truth_spec: str, item_spec: str | None, item_name: str | None) -> bool:
    """仕様が一致するか（型番は spec でなく name に混ざることもある）。

    🔴 単純な部分一致にすると "HS-200×200" が "VHS-200×200" にも当たる。
    直前が英数字でないこと（語の途中でないこと）を必ず確かめる。
    """
    t = norm(truth_spec)
    if not t:
        return False
    for hay in (norm(item_spec), norm(item_name)):
        for m in re.finditer(re.escape(t), hay):
            if m.start() == 0 or not hay[m.start() - 1].isalnum():
                return True
    return False


def score(truth: dict, items: list[dict]) -> dict:
    """正解行と抽出行を **1対1** で割り当てて採点する。

    同じ寸法の行が表に何度も出る（吸込口①④⑥ はどれも HS-200×200）ため、
    1つの抽出行を複数の正解行で使い回すと数量が水増しされて見える。
    決め手のある行（風量 Q が一致）から先に取り、使った行は消費する。
    """
    out = {"tables": [], "extracted_rows": len(items)}
    for tbl in truth.get("tables", []):
        rows = tbl.get("rows", [])
        scorable = [r for r in rows if r.get("qty", 0) > 0]
        used: set[int] = set()
        details: dict[str, dict] = {}

        def assign(rows_subset, *, require_q: bool):
            for r in rows_subset:
                if r.get("no") in details:
                    continue
                tq = _q_value(r.get("extra"), r.get("spec"))
                for i, it in enumerate(items):
                    if i in used or not spec_hit(r["spec"], it.get("spec"), it.get("name")):
                        continue
                    iq = _q_value(it.get("spec"), it.get("name"))
                    if require_q and not (tq and iq and tq == iq):
                        continue
                    used.add(i)
                    details[r["no"]] = {
                        "no": r.get("no"), "spec": r["spec"], "truth_qty": r.get("qty"),
                        "got_qty": float(it.get("quantity") or 0), "row_found": True,
                        "qty_correct": abs(float(it.get("quantity") or 0) - r.get("qty", 0)) < 1e-6
                                       and r.get("qty", 0) > 0,
                        "basis": [it.get("qty_basis")],
                    }
                    break

        assign(rows, require_q=True)   # 風量まで一致する行を優先で確定
        assign(rows, require_q=False)  # 残りを寸法一致だけで埋める
        for r in rows:
            details.setdefault(r["no"], {
                "no": r.get("no"), "spec": r["spec"], "truth_qty": r.get("qty"),
                "got_qty": None, "row_found": False, "qty_correct": False, "basis": None,
            })
        det = [details[r["no"]] for r in rows]
        out["tables"].append({
            "table": tbl.get("table"),
            "rows_total": len(rows),
            "rows_found": sum(1 for d in det if d["row_found"]),
            "rows_scorable": len(scorable),
            "qty_correct": sum(1 for d in det if d["qty_correct"]),
            "truth_qty_total": sum(r.get("qty", 0) for r in rows),
            "got_qty_total": sum(d["got_qty"] or 0 for d in det if d["row_found"]),
            "details": det,
        })
        out.setdefault("matched_item_rows", 0)
        out["matched_item_rows"] += len(used)
    out["unmatched_item_rows"] = len(items) - out.get("matched_item_rows", 0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", required=True)
    ap.add_argument("--items", required=True)
    ap.add_argument("--json", action="store_true", help="機械可読で出す")
    a = ap.parse_args()

    truth = json.loads(Path(a.truth).expanduser().read_text(encoding="utf-8"))
    items = json.loads(Path(a.items).expanduser().read_text(encoding="utf-8"))
    if isinstance(items, dict):
        items = items.get("items", [])
    res = score(truth, items)

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    print(f"図面: {truth.get('drawing')}   抽出行数: {res['extracted_rows']}"
          f"（うち表に対応 {res.get('matched_item_rows', 0)} / 表以外 {res['unmatched_item_rows']}）")
    for t in res["tables"]:
        print(f"\n■ {t['table']}")
        print(f"  行の検出   : {t['rows_found']}/{t['rows_total']}")
        if t["rows_scorable"]:
            print(f"  数量の一致 : {t['qty_correct']}/{t['rows_scorable']}"
                  f"   （員数合計 {t['got_qty_total']:g} / 正解 {t['truth_qty_total']:g}）")
        for d in t["details"]:
            mark = "✅" if d["qty_correct"] else ("△" if d["row_found"] else "✗")
            got = "-" if d["got_qty"] is None else f"{d['got_qty']:g}"
            basis = ",".join(x or "-" for x in (d["basis"] or [])) or "-"
            print(f"   {mark} {d['no']:>2} {d['spec']:<14} 正解{d['truth_qty']:>3} → {got:>4}  出所={basis}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
