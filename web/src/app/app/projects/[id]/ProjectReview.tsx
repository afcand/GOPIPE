"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { needsReview, type TakeoffItem } from "@/lib/gopipe";
import { ReviewTable, StatCards, UndoBar, type Row } from "@/components/ReviewTable";

type DbItem = TakeoffItem & { id: string; status?: string };

/**
 * 保存済みの案件を開いて、続きから直す画面。
 *
 * 「覚えさせる」ボタンを押さなくても、直した瞬間に台帳へ残る（入力を増やさない）。
 * 押し忘れで消える設計は、忙しい日の夕方に必ず裏切る。
 */
export default function ProjectReview({
  projectId,
  initialItems,
}: {
  projectId: string;
  initialItems: DbItem[];
}) {
  const [rows, setRows] = useState<Row[]>(() =>
    // 保存済みの並びは崩さない（昨日見た順で開く）。🔴だけ先頭へ寄せる。
    [...initialItems]
      .sort((a, b) => (needsReview(a) ? 0 : 1) - (needsReview(b) ? 0 : 1))
      .map((it, i) => ({
        ...it,
        id: i,
        dbId: it.id,
        edited: it.status === "confirmed",
        base: it,
        saveState: "idle" as const,
      })),
  );
  const [error, setError] = useState("");
  const pending = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());
  const nextId = useRef(initialItems.length + 1000);

  const warnCount = useMemo(() => rows.filter((r) => needsReview(r)).length, [rows]);
  const confirmedCount = useMemo(() => rows.filter((r) => r.edited).length, [rows]);

  const save = useCallback(
    async (row: Row) => {
      if (!row.dbId) return;
      setRows((cur) =>
        cur.map((r) => (r.id === row.id ? { ...r, saveState: "saving" } : r)),
      );
      try {
        const res = await fetch("/api/items", {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            projectId,
            patches: [
              {
                id: row.dbId,
                before: {
                  raw_name: row.base.raw_name ?? row.base.name,
                  name: row.base.name,
                  spec: row.base.spec,
                  quantity: row.base.quantity,
                  unit: row.base.unit,
                  category: row.base.category,
                  location: row.base.location,
                },
                after: {
                  name: row.name,
                  spec: row.spec,
                  quantity: row.quantity,
                  unit: row.unit,
                  category: row.category,
                  location: row.location,
                },
              },
            ],
          }),
        });
        const data = await res.json();
        const ok = res.ok && data.saved === 1;
        setRows((cur) =>
          cur.map((r) =>
            r.id === row.id ? { ...r, saveState: ok ? "saved" : "error" } : r,
          ),
        );
        if (!ok) setError("保存できなかった行があります。通信を確認してください。");
      } catch {
        setRows((cur) =>
          cur.map((r) => (r.id === row.id ? { ...r, saveState: "error" } : r)),
        );
        setError("保存できませんでした。通信を確認してください。");
      }
    },
    [projectId],
  );

  function edit(id: number, patch: Partial<Row>) {
    setRows((cur) => {
      const next = cur.map((r) =>
        r.id === id ? { ...r, ...patch, edited: true, saveState: "idle" as const } : r,
      );
      // 打っている最中に毎文字保存しない。手が止まったら残す。
      const t = pending.current.get(id);
      if (t) clearTimeout(t);
      const row = next.find((r) => r.id === id)!;
      pending.current.set(
        id,
        setTimeout(() => {
          pending.current.delete(id);
          void save(row);
        }, 900),
      );
      return next;
    });
  }

  /** AIが拾い落とした行を足す。ここが無いと、漏れを見つけた瞬間にExcelへ落ちる。 */
  async function addRow() {
    setError("");
    const res = await fetch("/api/items/rows", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        projectId,
        item: { name: "（名称を入れてください）", quantity: 0, unit: "", page: 1 },
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      setError(data?.error ?? "追加できませんでした");
      return;
    }
    const blank: Row = {
      id: nextId.current++,
      dbId: data.id,
      name: "（名称を入れてください）",
      spec: null,
      location: null,
      quantity: 0,
      unit: "",
      category: null,
      confidence: 1,
      checks: [],
      edited: true,
      saveState: "saved",
      base: { name: "", spec: null, location: null, quantity: 0, unit: "", category: null, confidence: 1 },
    };
    setRows((cur) => [blank, ...cur]);
  }

  /** 消した行の控え。台帳から消えるので、取り消せないと怖くて消せない。 */
  const [trash, setTrash] = useState<{ row: Row; at: number }[]>([]);

  /** 要らない拾い出しを消す（拾い過ぎ・二重計上・そもそも対象外）。消したことも記録に残る。 */
  async function removeRow(id: number) {
    const row = rows.find((r) => r.id === id);
    if (!row?.dbId) return;
    setError("");
    const res = await fetch("/api/items/rows", {
      method: "DELETE",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        id: row.dbId,
        projectId,
        before: {
          name: row.name, spec: row.spec, quantity: row.quantity,
          unit: row.unit, category: row.category, location: row.location,
        },
      }),
    });
    if (!res.ok) {
      setError("消せませんでした。通信を確認してください。");
      return;
    }
    setTrash((t) => [...t, { row, at: rows.indexOf(row) }]);
    setRows((cur) => cur.filter((r) => r.id !== id));
  }

  /**
   * 消した行を台帳へ戻す。
   * 台帳の行は物理削除されているので、同じ内容で入れ直す（dbId は新しくなる）。
   * 末尾でなく元の位置へ戻す＝どこにあった行か分からなくならないように。
   */
  async function undoRemove() {
    const last = trash[trash.length - 1];
    if (!last) return;
    setError("");
    const r = last.row;
    const res = await fetch("/api/items/rows", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        projectId,
        item: {
          name: r.name, spec: r.spec, quantity: r.quantity, unit: r.unit,
          location: r.location, category: r.category, page: r.page ?? 1,
        },
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      setError(data?.error ?? "戻せませんでした。通信を確認してください。");
      return;
    }
    setTrash((t) => t.slice(0, -1));
    setRows((cur) => {
      const next = [...cur];
      next.splice(Math.min(last.at, next.length), 0, { ...r, dbId: data.id, saveState: "saved" });
      return next;
    });
  }

  /** 直した行をAIが出した最初の値へ戻す（台帳にも書き戻す）。 */
  function revertRow(id: number) {
    const row = rows.find((r) => r.id === id);
    if (!row) return;
    const restored: Row = { ...row, ...row.base, id: row.id, dbId: row.dbId, base: row.base, edited: false };
    setRows((cur) => cur.map((r) => (r.id === id ? restored : r)));
    const t = pending.current.get(id);
    if (t) clearTimeout(t);
    pending.current.delete(id);
    void save(restored);
  }

  // 画面を閉じる直前に、打ちかけの保存を取りこぼさない
  useEffect(() => {
    const map = pending.current;
    return () => map.forEach((t) => clearTimeout(t));
  }, []);

  return (
    <section className="py-8">
      <StatCards
        items={[
          { label: "明細", value: `${rows.length} 件`, color: "var(--ink)" },
          { label: "要確認（🔴）", value: `${warnCount} 件`, color: "var(--red)" },
          { label: "確定済み", value: `${confirmedCount} 件`, color: "var(--cyan)" },
        ]}
      />

      <div className="mt-5 mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="m-0 text-[14px] text-[var(--mut)]">
          直すとその場で台帳に残ります（ボタンは要りません）。⏳は保存中、✅は保存済みです。
        </p>
        <button
          onClick={addRow}
          className="rounded-[10px] border border-[var(--cyan)] px-4 py-2 text-[14px] font-bold whitespace-nowrap text-[var(--cyan)] hover:bg-[rgba(86,204,242,0.08)]"
        >
          ＋ 拾い漏れを足す
        </button>
      </div>

      {error && (
        <p className="mb-4 rounded-[11px] border border-[rgba(215,38,30,0.35)] bg-[rgba(215,38,30,0.08)] px-4 py-3 text-[14px] text-[#f2c7c4]">
          {error}
        </p>
      )}

      {rows.length === 0 ? (
        <p className="text-[15px] text-[var(--mut)]">この物件にはまだ明細がありません。</p>
      ) : (
        <>
          <UndoBar
            count={trash.length}
            label={`消した行が ${trash.length} 件あります`}
            onUndo={undoRemove}
            onDismiss={() => setTrash([])}
          />
          <ReviewTable rows={rows} onEdit={edit} onRemove={removeRow} onRevert={revertRow} />
        </>
      )}
    </section>
  );
}
