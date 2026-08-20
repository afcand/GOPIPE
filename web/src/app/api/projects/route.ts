import { currentMembership, supabaseServer } from "@/lib/supabase/server";
import { PROJECT_STEPS } from "@/lib/projectStatus";

const KEYS = new Set(PROJECT_STEPS.map((s) => s.key as string));

/**
 * 物件の進み具合と詳細設定を直す。
 *
 * 組織はセッションから引く（クライアントが名乗った値を信じない）。
 * 書き込みは RLS 越しの通常キーで行う＝他社の物件は policy が弾く。
 */
export async function PATCH(req: Request) {
  const me = await currentMembership();
  if (!me) return Response.json({ error: "ログインしてください" }, { status: 401 });
  if (!me.org) return Response.json({ error: "組織に所属していません" }, { status: 403 });

  const body = (await req.json()) as {
    id?: string;
    status?: string;
    title?: string;
    municipality?: string | null;
    installedYear?: number | null;
  };
  if (!body.id) return Response.json({ error: "物件が指定されていません" }, { status: 400 });

  const patch: Record<string, unknown> = { updated_at: new Date().toISOString() };
  if (body.status !== undefined) {
    if (!KEYS.has(body.status)) {
      return Response.json({ error: "その進み具合は選べません" }, { status: 400 });
    }
    patch.status = body.status;
  }
  if (body.title !== undefined) {
    const t = String(body.title).trim();
    if (!t) return Response.json({ error: "物件名を入れてください" }, { status: 400 });
    patch.title = t;
  }
  if (body.municipality !== undefined) {
    patch.municipality = String(body.municipality || "").trim() || null;
  }
  if (body.installedYear !== undefined) {
    const y = Number(body.installedYear);
    // 布設年は更新提案の計算に効く。桁を打ち間違えた年で「今すぐ更新」が出ると信用を失う。
    if (body.installedYear === null || Number.isNaN(y)) patch.installed_year = null;
    else if (y < 1900 || y > new Date().getFullYear() + 1) {
      return Response.json({ error: "布設年が範囲外です（1900〜今年）" }, { status: 400 });
    } else patch.installed_year = Math.round(y);
  }

  const sb = await supabaseServer();
  const { error } = await sb.from("projects").update(patch).eq("id", body.id);
  if (error) return Response.json({ error: "保存できませんでした" }, { status: 500 });
  return Response.json({ ok: true });
}
