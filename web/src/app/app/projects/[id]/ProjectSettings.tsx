"use client";

import { useState } from "react";
import { PROJECT_STEPS, stepIndex } from "@/lib/projectStatus";

/**
 * 物件の進み具合（クリックで変える帯）と、詳細設定。
 *
 * 設定は「何に使うか」を必ず添える。municipality と installed_year は
 * DBには前からあったのに画面に無く、誰も入れられないまま
 * 申請書と更新提案がその値を待っていた（＝機能が置物になっていた）。
 */
export default function ProjectSettings({
  projectId,
  status,
  title,
  municipality,
  installedYear,
}: {
  projectId: string;
  status: string | null;
  title: string;
  municipality: string | null;
  installedYear: number | null;
}) {
  const [cur, setCur] = useState(status ?? "draft");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    title,
    municipality: municipality ?? "",
    installedYear: installedYear ? String(installedYear) : "",
  });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const idx = stepIndex(cur);

  async function patch(body: Record<string, unknown>, ok: string) {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const res = await fetch("/api/projects", {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: projectId, ...body }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error ?? "保存できませんでした");
      setMsg(ok);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
    return true;
  }

  async function pick(key: string) {
    const before = cur;
    setCur(key); // 先に画面を動かす。押した手応えが無いと二度押しされる
    if (!(await patch({ status: key }, "進み具合を変えました"))) setCur(before);
  }

  return (
    <section className="pt-6">
      <p className="m-0 mb-2 text-[12px] font-bold tracking-wider text-[var(--mut)]">
        この物件の進み具合（押して変えられます）
      </p>
      <div className="flex flex-wrap items-stretch gap-2">
        {PROJECT_STEPS.map((s, i) => {
          const done = i < idx;
          const now = i === idx;
          return (
            <button
              key={s.key}
              onClick={() => pick(s.key)}
              disabled={busy}
              title={s.hint}
              className={
                "flex-1 min-w-[104px] rounded-[11px] border px-3 py-2.5 text-left transition disabled:opacity-60 " +
                (now
                  ? "border-[var(--cyan)] bg-[rgba(86,204,242,0.14)]"
                  : done
                    ? "border-[var(--line)] bg-[rgba(86,204,242,0.05)]"
                    : "border-[var(--line)] bg-[var(--panel)] hover:border-[var(--cyan)]")
              }
            >
              <span
                className={
                  "block text-[14px] font-black " +
                  (now ? "text-[var(--cyan)]" : done ? "text-[var(--ink)]" : "text-[var(--mut)]")
                }
              >
                {done ? "✓ " : now ? "● " : "○ "}
                {s.label}
              </span>
              <span className="block text-[11.5px] text-[var(--mut)]">{s.hint}</span>
            </button>
          );
        })}
      </div>

      <button
        onClick={() => setOpen((v) => !v)}
        className="mt-3 text-[13.5px] font-bold text-[var(--cyan)] hover:underline"
      >
        {open ? "▲ 詳細設定を閉じる" : "▼ 詳細設定（物件名・申請先・布設年）"}
      </button>

      {open && (
        <div className="mt-3 grid gap-4 rounded-[13px] border border-[var(--line)] bg-[var(--panel)] p-5 sm:grid-cols-2">
          <label className="block">
            <span className="block text-[13px] font-bold">物件名</span>
            <span className="mb-1.5 block text-[11.5px] text-[var(--mut)]">
              一覧とExcelの見出しになります
            </span>
            <input
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              className="w-full rounded-[10px] border border-[var(--line)] bg-[var(--navy2)] px-3 py-2 text-[15px] focus:border-[var(--cyan)] focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="block text-[13px] font-bold">申請の提出先（自治体）</span>
            <span className="mb-1.5 block text-[11.5px] text-[var(--mut)]">
              給水装置工事の申込書は自治体ごとに様式が違います
            </span>
            <input
              value={form.municipality}
              onChange={(e) => setForm({ ...form, municipality: e.target.value })}
              placeholder="例: 文京区"
              className="w-full rounded-[10px] border border-[var(--line)] bg-[var(--navy2)] px-3 py-2 text-[15px] focus:border-[var(--cyan)] focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="block text-[13px] font-bold">配管の布設年</span>
            <span className="mb-1.5 block text-[11.5px] text-[var(--mut)]">
              「そろそろ更新どき」の提案を出すのに使います
            </span>
            <input
              type="number"
              value={form.installedYear}
              onChange={(e) => setForm({ ...form, installedYear: e.target.value })}
              placeholder="例: 2008"
              className="w-full rounded-[10px] border border-[var(--line)] bg-[var(--navy2)] px-3 py-2 text-[15px] focus:border-[var(--cyan)] focus:outline-none"
            />
          </label>
          <div className="flex items-end">
            <button
              onClick={() =>
                patch(
                  {
                    title: form.title,
                    municipality: form.municipality,
                    installedYear: form.installedYear === "" ? null : Number(form.installedYear),
                  },
                  "設定を保存しました",
                )
              }
              disabled={busy}
              className="rounded-[11px] border border-[var(--cyan)] px-5 py-2.5 text-[14px] font-bold text-[var(--cyan)] hover:bg-[rgba(86,204,242,0.10)] disabled:opacity-60"
            >
              {busy ? "保存中…" : "設定を保存"}
            </button>
          </div>
        </div>
      )}

      {msg && <p className="mt-3 mb-0 text-[13.5px] text-[var(--cyan)]">{msg}</p>}
      {err && <p className="mt-3 mb-0 text-[13.5px] text-[var(--red)]">{err}</p>}
    </section>
  );
}
