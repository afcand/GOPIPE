#!/usr/bin/env bash
# 本番スモーク — デプロイ後に「本当に出せたか」を実測する。
#
# 2026-08-19、vercel --prod が READY を返し vercel inspect も Ready なのに
# 全エンドポイントが 404 になる本番障害を踏んだ（Vercel の rewrite 仕様変更）。
# CLI の成功表示は「出せた」の証拠にならない。主要パスの HTTP コードだけが証拠。
#
#   bash scripts/smoke.sh                      # 本番
#   BASE=https://xxx.vercel.app bash scripts/smoke.sh   # 任意のデプロイ
set -uo pipefail
BASE="${BASE:-https://gopipe.vercel.app}"
fail=0

check() { # method path 期待コード... 説明
  local method=$1 path=$2 desc=$3; shift 3
  local code
  code=$(curl -o /dev/null -sS -m 40 -X "$method" -w '%{http_code}' "$BASE$path" 2>/dev/null)
  for want in "$@"; do
    if [ "$code" = "$want" ]; then printf '  ✅ %-6s %-26s %s (%s)\n' "$method" "$path" "$code" "$desc"; return 0; fi
  done
  printf '  ❌ %-6s %-26s %s ← 期待 %s (%s)\n' "$method" "$path" "$code" "$*" "$desc"; fail=1
}

echo "スモーク: $BASE"
check GET  /health   "生存"                     200
check GET  /docs     "APIドキュメント"           200
check GET  /         "トップ(ログインへ)"        200 307 302
check POST /takeoff  "拾い出し(mock既定で通る)"  200
check POST /estimate "見積"                     200 422
check POST /learn    "学習(無認証は拒否)"        401 403 422 503

# 数量の出所が応答に乗っているか（エンジンにあっても API が落とすと画面に届かない）
if command -v python3 >/dev/null; then
  body=$(curl -sS -m 40 -X POST "$BASE/takeoff" 2>/dev/null)
  if printf '%s' "$body" | python3 -c "
import json,sys
d=json.load(sys.stdin); its=d.get('items') or []
sys.exit(0 if (not its or 'qty_basis' in its[0]) else 1)
" 2>/dev/null; then
    echo "  ✅ 応答に qty_basis 列がある（数量の出所が画面へ届く）"
  else
    echo "  ❌ 応答に qty_basis が無い＝確かな数と推定の区別が画面から消える"; fail=1
  fi
fi

[ $fail -eq 0 ] && echo "→ 全て期待どおり" || echo "→ 🔴 期待外れあり。デプロイを「出せた」と言わないこと"
exit $fail
