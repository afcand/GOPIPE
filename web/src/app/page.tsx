"use client";

import { useMemo, useState } from "react";
import { needsReview, type TakeoffItem } from "@/lib/gopipe";
import { ReviewTable, StatCards, UndoBar, sortForReview, type Row } from "@/components/ReviewTable";

export default function Home() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [busy, setBusy] = useState<"" | "takeoff" | "excel">("");
  const [error, setError] = useState("");

  const warnCount = useMemo(
    () => (rows ?? []).filter((r) => needsReview(r)).length,
    [rows],
  );
  const editedCount = useMemo(() => (rows ?? []).filter((r) => r.edited).length, [rows]);

  async function runTakeoff() {
    setBusy("takeoff");
    setError("");
    try {
      const res = await fetch("/api/takeoff", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error ?? "失敗しました");
      setRows(sortForReview((data.items ?? []) as TakeoffItem[]));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  function edit(id: number, patch: Partial<Row>) {
    setRows((cur) => (cur ?? []).map((r) => (r.id === id ? { ...r, ...patch, edited: true } : r)));
  }

  /** 消した行の控え。取り消せないと、怖くて誰も消さない。 */
  const [trash, setTrash] = useState<{ row: Row; at: number }[]>([]);

  function removeRow(id: number) {
    setRows((cur) => {
      const target = (cur ?? []).find((r) => r.id === id);
      if (target) setTrash((t) => [...t, { row: target, at: (cur ?? []).indexOf(target) }]);
      return (cur ?? []).filter((r) => r.id !== id);
    });
  }

  function undoRemove() {
    setTrash((t) => {
      const last = t[t.length - 1];
      if (!last) return t;
      setRows((cur) => {
        const next = [...(cur ?? [])];
        next.splice(Math.min(last.at, next.length), 0, last.row);
        return next;
      });
      return t.slice(0, -1);
    });
  }

  function revertRow(id: number) {
    setRows((cur) =>
      (cur ?? []).map((r) => (r.id === id ? { ...r, ...r.base, id: r.id, base: r.base, edited: false } : r)),
    );
  }

  async function downloadExcel() {
    if (!rows?.length) return;
    setBusy("excel");
    setError("");
    try {
      const res = await fetch("/api/export", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          items: rows.map((r) => ({
            name: r.name,
            spec: r.spec,
            quantity: r.quantity,
            unit: r.unit,
            location: r.location,
            category: r.category,
            // 人が直した行は確定扱い（Streamlit 版と同じ考え方）
            confidence: r.edited ? 1 : r.confidence,
          })),
        }),
      });
      if (!res.ok) throw new Error("Excel の生成に失敗しました");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "GOPIPE_拾い出し.xlsx";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  return (
    <main className="mx-auto w-full max-w-[1100px] px-5 pb-24">
      <header className="border-b border-[var(--line)] pt-14 pb-9">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <p className="m-0 text-[12px] font-bold tracking-[0.34em] text-[var(--cyan)]">
            PIPING · HVAC · INSULATION — AI TAKEOFF
          </p>
          <a
            href="/login"
            className="text-[13.5px] font-bold text-[var(--cyan)] hover:underline"
          >
            社内の方はログイン →
          </a>
        </div>
        <h1 className="my-2 bg-gradient-to-b from-[#eaf6ff] to-[#8fd3f4] bg-clip-text text-[clamp(40px,9vw,76px)] font-black leading-[0.95] text-transparent">
          GOPIPE
        </h1>
        <p className="m-0 text-[clamp(18px,3vw,26px)] font-extrabold">
          未来へ、<span className="text-[var(--cyan)]">PIPE</span>を架ける。
          <span className="text-[var(--red)]"> AIは提案、確定は人。</span>
        </p>
        <p className="mt-3 mb-0 max-w-[62ch] text-[15px] text-[var(--mut)]">
          設備図から拾い出した明細を、その場で直して確定できます。ログイン不要のお試しでは、
          サンプル図面の結果が出ます。
        </p>
      </header>

      <section className="py-10">
        <div className="flex flex-wrap items-center gap-4">
          <button
            onClick={runTakeoff}
            disabled={busy !== ""}
            className="inline-flex items-center gap-3 rounded-[13px] bg-gradient-to-b from-[#ffab33] to-[var(--orange)] px-7 py-4 text-[18px] font-black text-[#241200] shadow-[0_10px_28px_rgba(247,148,30,0.28)] transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy === "takeoff" ? "拾い出し中…" : "拾い出しを実行"}
            <span className="text-[20px] leading-none">▶</span>
          </button>
          <a
            href="https://gopipe.vercel.app/share"
            className="text-[14px] font-bold text-[var(--cyan)] hover:underline"
          >
            動画・マンガはこちら →
          </a>
        </div>

        {error && (
          <p className="mt-5 rounded-[11px] border border-[rgba(215,38,30,0.35)] bg-[rgba(215,38,30,0.08)] px-4 py-3 text-[14px] text-[#f2c7c4]">
            {error}
          </p>
        )}

        {rows && (
          <>
            <div className="mt-8">
              <StatCards
                items={[
                  { label: "抽出した明細", value: `${rows.length} 件`, color: "var(--ink)" },
                  { label: "要確認（🔴）", value: `${warnCount} 件`, color: "var(--red)" },
                  { label: "あなたが直した行", value: `${editedCount} 件`, color: "var(--cyan)" },
                ]}
              />
            </div>

            <p className="mt-5 mb-4 text-[14px] text-[var(--mut)]">
              AIの下書きです。🔴の行を上にまとめてあります。数量・名称はその場で直せます。
              直した行は確定扱いになります。
            </p>

            <UndoBar
              count={trash.length}
              label={`消した行が ${trash.length} 件あります`}
              onUndo={undoRemove}
              onDismiss={() => setTrash([])}
            />
            <ReviewTable rows={rows} onEdit={edit} onRemove={removeRow} onRevert={revertRow} />

            <div className="mt-6 flex flex-wrap items-center gap-4">
              <button
                onClick={downloadExcel}
                disabled={busy !== ""}
                className="rounded-[11px] border border-[var(--cyan)] px-6 py-3 text-[15px] font-bold text-[var(--cyan)] transition hover:bg-[rgba(86,204,242,0.08)] disabled:opacity-60"
              >
                {busy === "excel" ? "作成中…" : "Excel で書き出す"}
              </button>
              <p className="m-0 max-w-[46ch] text-[13px] text-[var(--mut)]">
                実際の設備図を読ませたり、直した内容を会社の辞書に覚えさせるのは、
                ログインしてからの画面です。
              </p>
            </div>
          </>
        )}
      </section>

      <footer className="border-t border-[var(--line)] pt-7 text-center text-[12.5px] text-[var(--mut)]">
        GOPIPE — 株式会社and° / 設備積算AI
      </footer>
    </main>
  );
}
