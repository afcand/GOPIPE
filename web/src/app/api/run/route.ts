import { API_BASE, serverKey } from "@/lib/gopipe";
import { currentMembership } from "@/lib/supabase/server";

export const maxDuration = 300; // 実図面の解析は時間がかかる（Vercel Pro の上限まで待つ）

/**
 * 実図面の拾い出し（ログイン必須）。
 *
 * 組織は「クライアントが名乗った値」ではなく、必ずサーバ側でセッションから引く。
 * 図面のパスも自組織のフォルダ配下しか許可しない（他社の図面を読ませない）。
 */
export async function POST(req: Request) {
  const me = await currentMembership();
  if (!me) return Response.json({ error: "ログインしてください" }, { status: 401 });
  if (!me.org) {
    return Response.json(
      { error: "組織に所属していません。管理者に招待コードを確認してください。" },
      { status: 403 },
    );
  }
  const key = serverKey();
  if (!key) {
    return Response.json({ error: "サーバの設定が未完了です" }, { status: 503 });
  }

  const { storagePath, projectSlug, title, fileName, noLlm } = await req.json();
  if (typeof storagePath !== "string" || !storagePath.startsWith(`${me.org.slug}/`)) {
    return Response.json({ error: "図面の指定が不正です" }, { status: 400 });
  }

  const form = new FormData();
  form.set("provider", "claude");
  form.set("persist", "true");
  form.set("org_slug", me.org.slug);
  form.set("project_slug", String(projectSlug || "").trim() || "untitled");
  form.set("title", String(title || "").trim());
  form.set("storage_path", storagePath);
  // 置き場所は半角英数だが、人が見る名前は元のまま残す
  if (fileName) form.set("file_name", String(fileName).slice(0, 200));
  // ベクター(CAD)図は印字だけで拾える。画像認識を使わない＝費用0・同じ図面なら毎回同じ数。
  if (noLlm) form.set("no_llm", "true");

  const res = await fetch(`${API_BASE}/takeoff`, {
    method: "POST",
    headers: { "x-gopipe-key": key },
    body: form,
  });
  const text = await res.text();
  if (!res.ok) {
    return Response.json(
      { error: `拾い出しに失敗しました (${res.status})`, detail: text.slice(0, 400) },
      { status: 502 },
    );
  }
  return new Response(text, { headers: { "content-type": "application/json" } });
}
