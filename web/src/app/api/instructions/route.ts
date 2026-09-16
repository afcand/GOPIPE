import { API_BASE, serverKey } from "@/lib/gopipe";
import { currentMembership } from "@/lib/supabase/server";

/**
 * 人が指した指示（箇所・色）の読み書き。
 *
 * 組織はクライアントが名乗った値でなく、必ずセッションから引く。
 * 色の意味は会社ごとにしか決まらないので、ここを緩めると他社の決め事が混ざる。
 */
export async function GET(req: Request) {
  const me = await currentMembership();
  if (!me?.org) return Response.json({ error: "ログインしてください" }, { status: 401 });
  const key = serverKey();
  if (!key) return Response.json({ error: "サーバの設定が未完了です" }, { status: 503 });

  const url = new URL(req.url);
  const q = new URLSearchParams({ org_slug: me.org.slug });
  const kind = url.searchParams.get("kind");
  const sheetKey = url.searchParams.get("sheetKey");
  if (kind) q.set("kind", kind);
  if (sheetKey !== null) q.set("sheet_key", sheetKey);

  const res = await fetch(`${API_BASE}/instructions?${q}`, {
    headers: { "x-gopipe-key": key },
    cache: "no-store",
  });
  if (!res.ok) return Response.json({ error: "読み込めませんでした" }, { status: 502 });
  return new Response(await res.text(), { headers: { "content-type": "application/json" } });
}

export async function POST(req: Request) {
  const me = await currentMembership();
  if (!me?.org) return Response.json({ error: "ログインしてください" }, { status: 401 });
  const key = serverKey();
  if (!key) return Response.json({ error: "サーバの設定が未完了です" }, { status: 503 });

  const body = await req.json();
  const res = await fetch(`${API_BASE}/instructions`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-gopipe-key": key },
    body: JSON.stringify({ ...body, org_slug: me.org.slug }),
  });
  const text = await res.text();
  if (!res.ok) {
    return Response.json({ error: `覚えられませんでした (${res.status})`, detail: text.slice(0, 200) },
                         { status: 502 });
  }
  return new Response(text, { headers: { "content-type": "application/json" } });
}
