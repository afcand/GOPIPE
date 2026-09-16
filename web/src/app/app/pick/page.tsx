import { redirect } from "next/navigation";
import { currentMembership, supabaseServer } from "@/lib/supabase/server";
import Picker from "./Picker";

export const dynamic = "force-dynamic";

/**
 * 指して拾う画面。
 *
 * 物件を指定して開くと（?project=<id>）、その物件に登録されている図面をそのまま開き、
 * 拾った行はその物件の明細に足される。1回まるごと拾う → 保存 → 足りないところを
 * 範囲と色で拾い足す、という順で使うため。
 */
export default async function PickPage({
  searchParams,
}: {
  searchParams: Promise<{ project?: string }>;
}) {
  const { project } = await searchParams;
  const me = await currentMembership();
  if (!me) redirect("/login");
  if (!me.org) redirect("/app");

  let bound: { id: string; title: string; storagePath: string; fileName: string } | null = null;
  if (project) {
    const sb = await supabaseServer();
    // RLS が組織で絞るので、他社の物件はここに来ない
    const { data: pj } = await sb
      .from("projects")
      .select("id, title")
      .eq("id", project)
      .maybeSingle();
    const { data: dr } = await sb
      .from("drawings")
      .select("storage_path, file_name")
      .eq("project_id", project)
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (pj && dr?.storage_path) {
      bound = {
        id: pj.id,
        title: pj.title ?? "",
        storagePath: dr.storage_path,
        fileName: dr.file_name ?? "",
      };
    }
  }

  return (
    <Picker orgSlug={me.org.slug} orgName={me.org.name} project={bound} />
  );
}
