import { API_BASE, serverKey } from "@/lib/gopipe";
import { currentMembership } from "@/lib/supabase/server";

export const maxDuration = 120;

/** 図面の1点を指して拾う中継（色 / ダクト1本 / 記号1つ）。 */
export async function POST(req: Request) {
  const me = await currentMembership();
  if (!me?.org) return Response.json({ error: "ログインしてください" }, { status: 401 });
  const key = serverKey();
  if (!key) return Response.json({ error: "サーバの設定が未完了です" }, { status: 503 });

  const { storagePath, page, mode, x, y, scaleDenom } = await req.json();
  if (typeof storagePath !== "string" || !storagePath.startsWith(`${me.org.slug}/`)) {
    return Response.json({ error: "図面の指定が不正です" }, { status: 400 });
  }
  const form = new FormData();
  form.set("storage_path", storagePath);
  form.set("page", String(Math.max(1, Number(page) || 1)));
  form.set("mode", String(mode || "duct"));
  form.set("x", String(Math.min(1, Math.max(0, Number(x) || 0))));
  form.set("y", String(Math.min(1, Math.max(0, Number(y) || 0))));
  if (Number(scaleDenom) > 0) form.set("scale_denom", String(Number(scaleDenom)));

  const res = await fetch(`${API_BASE}/pick`, {
    method: "POST",
    headers: { "x-gopipe-key": key },
    body: form,
  });
  const text = await res.text();
  if (!res.ok) {
    return Response.json(
      { error: `指した場所を拾えませんでした (${res.status})`, detail: text.slice(0, 300) },
      { status: 502 },
    );
  }
  return new Response(text, { headers: { "content-type": "application/json" } });
}
