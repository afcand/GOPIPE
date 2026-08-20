"use client";

import { useEffect, useState } from "react";

type Sup = { name: string; hits: number; projects: number; specs?: string[] };

/**
 * 「拾わない」ことの学習の中身を見せる。
 *
 * 見えない学習は暴走する。何を学んだかを人が読めない状態で
 * 抽出に効かせると、ある日から特定の品目が理由も分からず出なくなる。
 */
export default function SuppressionList() {
  const [rows, setRows] = useState<Sup[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/suppressions")
      .then((r) => r.json())
      .then((d) => {
        if (d?.error) throw new Error(d.error);
        setRows((d.items ?? []) as Sup[]);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (error) return <p className="text-[14px] text-[var(--red)]">{error}</p>;
  if (!rows) return <p className="text-[14px] text-[var(--mut)]">読み込んでいます…</p>;
  if (rows.length === 0) {
    return (
      <p className="text-[14px] text-[var(--mut)]">
        まだありません。要らない行を <strong className="text-[var(--ink)]">別々の物件で2回以上</strong>
        消すと、ここに出ます（1回では覚えません。消し間違いで品目が消えないように）。
      </p>
    );
  }
  return (
    <ul className="m-0 list-none space-y-2 p-0">
      {rows.map((r) => (
        <li
          key={r.name}
          className="flex flex-wrap items-center justify-between gap-3 rounded-[11px] border border-[var(--line)] bg-[var(--panel)] px-4 py-3"
        >
          <span className="min-w-0">
            <span className="block text-[15px] font-bold">{r.name}</span>
            {r.specs && r.specs.length > 0 && (
              <span className="block text-[12px] text-[var(--mut)]">例: {r.specs.join(" / ")}</span>
            )}
          </span>
          <span className="text-[12.5px] whitespace-nowrap text-[var(--mut)]">
            {r.projects} 物件で {r.hits} 回削除
          </span>
        </li>
      ))}
    </ul>
  );
}
