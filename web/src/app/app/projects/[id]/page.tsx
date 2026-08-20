import { redirect, notFound } from "next/navigation";
import Link from "next/link";
import { currentMembership, supabaseServer } from "@/lib/supabase/server";
import ProjectReview from "./ProjectReview";
import ProjectSettings from "./ProjectSettings";

export const dynamic = "force-dynamic";

export default async function ProjectDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const me = await currentMembership();
  if (!me) redirect("/login");
  if (!me.org) redirect("/app");

  const sb = await supabaseServer();
  const { data: project } = await sb
    .from("projects")
    .select("id, title, slug, item_count, status, municipality, installed_year")
    .eq("id", id)
    .maybeSingle();
  if (!project) notFound();

  // RLS が組織で絞るので、他社の明細はここに来ない
  const { data: items } = await sb
    .from("takeoff_items")
    .select(
      "id, category, name, spec, location, quantity, unit, confidence, page, raw_name, qty_vision, checks, status",
    )
    .eq("project_id", id)
    .order("created_at", { ascending: true })
    .limit(2000);

  const { data: drawings } = await sb
    .from("drawings")
    .select("id, file_name, page_count, status, warnings, created_at")
    .eq("project_id", id)
    .order("created_at", { ascending: true });

  return (
    <main className="mx-auto w-full max-w-[1100px] px-5 pb-24">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] py-6">
        <div className="min-w-0">
          <Link href="/app/projects" className="text-[13px] font-bold text-[var(--cyan)]">
            ← 物件一覧
          </Link>
          <p className="m-0 truncate text-[22px] font-black">
            {project.title?.trim() || project.slug}
          </p>
        </div>
        <Link
          href="/app"
          className="rounded-[11px] border border-[var(--cyan)] px-5 py-2.5 text-[14px] font-bold text-[var(--cyan)]"
        >
          この物件に図面を追加
        </Link>
      </header>

      {(drawings?.length ?? 0) > 0 && (
        <section className="pt-6">
          <p className="m-0 mb-2 text-[12px] font-bold tracking-wider text-[var(--mut)]">
            この物件の図面
          </p>
          <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
            {drawings!.map((d) => (
              <li
                key={d.id}
                className="rounded-[10px] border border-[var(--line)] bg-[var(--panel)] px-4 py-2 text-[13px]"
              >
                📄 {d.file_name}
                {d.page_count ? `（${d.page_count}ページ）` : ""}
                {Array.isArray(d.warnings) && d.warnings.length > 0 && (
                  <span className="ml-2 text-[var(--red)]">
                    読めなかったページあり
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <ProjectSettings
        projectId={id}
        status={project.status ?? "draft"}
        title={project.title?.trim() || project.slug}
        municipality={project.municipality ?? null}
        installedYear={project.installed_year ?? null}
      />

      <ProjectReview projectId={id} initialItems={items ?? []} />
    </main>
  );
}
