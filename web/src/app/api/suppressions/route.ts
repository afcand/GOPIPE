import { API_BASE, serverKey } from "@/lib/gopipe";
import { currentMembership } from "@/lib/supabase/server";

/** この会社が「要らない」と繰り返し消してきた品目（拾わないことの学習）を読む。 */
export async function GET() {
  const me = await currentMembership();
  if (!me) return Response.json({ error: "ログインしてください" }, { status: 401 });
  if (!me.org) return Response.json({ error: "組織に所属していません" }, { status: 403 });
  const key = serverKey();
  if (!key) return Response.json({ error: "サーバの設定が未完了です" }, { status: 503 });

  const res = await fetch(
    `${API_BASE}/suppressions?org_slug=${encodeURIComponent(me.org.slug)}`,
    { headers: { "x-gopipe-key": key }, cache: "no-store" },
  );
  const text = await res.text();
  if (!res.ok) {
    return Response.json({ error: "読み込めませんでした" }, { status: 502 });
  }
  return new Response(text, { headers: { "content-type": "application/json" } });
}
