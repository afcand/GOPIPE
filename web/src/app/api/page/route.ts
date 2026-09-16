import { API_BASE, serverKey } from "@/lib/gopipe";
import { currentMembership } from "@/lib/supabase/server";

export const maxDuration = 60;

/**
 * 図面を画像にして返す中継（範囲を指す画面のため）。
 *
 * 図面のパスは必ずサーバ側でセッションから引いた組織の配下に限る。
 * ここを緩めると、パスを書き換えるだけで他社の図面が見えてしまう。
 */
export async function POST(req: Request) {
  const me = await currentMembership();
  if (!me?.org) return Response.json({ error: "ログインしてください" }, { status: 401 });
  const key = serverKey();
  if (!key) return Response.json({ error: "サーバの設定が未完了です" }, { status: 503 });

  const { storagePath, page, dpi } = await req.json();
  if (typeof storagePath !== "string" || !storagePath.startsWith(`${me.org.slug}/`)) {
    return Response.json({ error: "図面の指定が不正です" }, { status: 400 });
  }
  const form = new FormData();
  form.set("storage_path", storagePath);
  form.set("page", String(Math.max(1, Number(page) || 1)));
  form.set("dpi", String(Math.min(200, Math.max(40, Number(dpi) || 110))));

  const res = await fetch(`${API_BASE}/page_png`, {
    method: "POST",
    headers: { "x-gopipe-key": key },
    body: form,
  });
  if (!res.ok) {
    return Response.json(
      { error: `図面を開けませんでした (${res.status})` },
      { status: 502 },
    );
  }
  return new Response(await res.arrayBuffer(), {
    headers: {
      "content-type": "image/png",
      "x-page-count": res.headers.get("x-page-count") ?? "1",
      "cache-control": "no-store",
    },
  });
}
