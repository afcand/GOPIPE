import { API_BASE, serverKey } from "@/lib/gopipe";
import { currentMembership } from "@/lib/supabase/server";

/** 会社が決めた「色→意味」と、図面に実際に出てきた色を読む。 */
export async function GET() {
  const me = await currentMembership();
  if (!me) return Response.json({ error: "ログインしてください" }, { status: 401 });
  if (!me.org) return Response.json({ error: "組織に所属していません" }, { status: 403 });
  const key = serverKey();
  if (!key) return Response.json({ error: "サーバの設定が未完了です" }, { status: 503 });

  const res = await fetch(
    `${API_BASE}/color_meanings?org_slug=${encodeURIComponent(me.org.slug)}`,
    { headers: { "x-gopipe-key": key }, cache: "no-store" },
  );
  if (!res.ok) return Response.json({ error: "読み込めませんでした" }, { status: 502 });
  return new Response(await res.text(), { headers: { "content-type": "application/json" } });
}

/** 色の意味を覚える／消す（meaning が空なら消す）。組織はセッションから引く。 */
export async function POST(req: Request) {
  const me = await currentMembership();
  if (!me) return Response.json({ error: "ログインしてください" }, { status: 401 });
  if (!me.org) return Response.json({ error: "組織に所属していません" }, { status: 403 });
  const key = serverKey();
  if (!key) return Response.json({ error: "サーバの設定が未完了です" }, { status: 503 });

  const body = (await req.json()) as { color?: string; meaning?: string; note?: string };
  if (!body.color) return Response.json({ error: "色が指定されていません" }, { status: 400 });

  const res = await fetch(`${API_BASE}/color_meanings`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-gopipe-key": key },
    // 組織はクライアントが名乗った値でなくセッションから
    body: JSON.stringify({ ...body, org_slug: me.org.slug }),
  });
  if (!res.ok) return Response.json({ error: "保存できませんでした" }, { status: 502 });
  return new Response(await res.text(), { headers: { "content-type": "application/json" } });
}
