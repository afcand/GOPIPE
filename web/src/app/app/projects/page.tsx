import { redirect } from "next/navigation";
import Link from "next/link";
import { currentMembership, supabaseServer } from "@/lib/supabase/server";
import { stepIndex, stepLabel, PROJECT_STEPS } from "@/lib/projectStatus";

export const dynamic = "force-dynamic";

type ProjectRow = {
  id: string;
  title: string | null;
  slug: string;
  item_count: number | null;
  status: string | null;
  updated_at: string | null;
  created_at: string | null;
};

function when(v: string | null) {
  if (!v) return "";
  const d = new Date(v);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(
    d.getMinutes(),
  ).padStart(2, "0")}`;
}

export default async function ProjectsPage() {
  const me = await currentMembership();
  if (!me) redirect("/login");
  if (!me.org) redirect("/app");

  const sb = await supabaseServer();
  const { data } = await sb
    .from("projects")
    .select("id, title, slug, item_count, status, updated_at, created_at")
    .order("updated_at", { ascending: false })
    .limit(200);
  const projects = (data ?? []) as ProjectRow[];

  return (
    <main className="mx-auto w-full max-w-[900px] px-5 pb-24">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] py-6">
        <div>
          <p className="m-0 text-[12px] font-bold tracking-[0.3em] text-[var(--cyan)]">GOPIPE</p>
          <p className="m-0 text-[20px] font-black">{me.org.name}の物件</p>
        </div>
        <Link
          href="/app/guide"
          className="text-[13.5px] font-bold text-[var(--cyan)] hover:underline"
        >
          使い方
        </Link>
        <Link
          href="/app"
          className="rounded-[11px] bg-gradient-to-b from-[#ffab33] to-[var(--orange)] px-5 py-2.5 text-[15px] font-black text-[#241200]"
        >
          新しい図面を拾う
        </Link>
      </header>

      <section className="py-8">
        {projects.length === 0 ? (
          <p className="text-[15px] text-[var(--mut)]">
            まだ物件がありません。「新しい図面を拾う」から設備図を入れてください。
          </p>
        ) : (
          <ul className="m-0 list-none space-y-2 p-0">
            {projects.map((p) => (
              <li key={p.id}>
                <Link
                  href={`/app/projects/${p.id}`}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-[12px] border border-[var(--line)] bg-[var(--panel)] px-5 py-4 transition hover:border-[var(--cyan)]"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[16px] font-bold">
                      {p.title?.trim() || p.slug}
                    </span>
                    <span className="flex flex-wrap items-center gap-2 text-[12.5px] text-[var(--mut)]">
                      <span
                        className={
                          "rounded-full px-2.5 py-0.5 text-[11.5px] font-bold " +
                          (stepIndex(p.status) >= PROJECT_STEPS.length - 1
                            ? "bg-[rgba(86,204,242,0.18)] text-[var(--cyan)]"
                            : "bg-[rgba(255,255,255,0.07)] text-[var(--ink)]")
                        }
                      >
                        {stepIndex(p.status) + 1}/{PROJECT_STEPS.length} {stepLabel(p.status)}
                      </span>
                      最終更新 {when(p.updated_at ?? p.created_at)}
                    </span>
                  </span>
                  <span className="tabular text-[14px] whitespace-nowrap text-[var(--cyan)]">
                    {p.item_count ?? 0} 件 →
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
