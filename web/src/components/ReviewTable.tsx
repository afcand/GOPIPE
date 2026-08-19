"use client";

import { confidenceLevel, needsReview, type TakeoffItem } from "@/lib/gopipe";

/** base = AIが出した直後の姿。学習に「直す前 → 直した後」を渡すために手放さない。 */
export type Row = TakeoffItem & {
  id: number;
  edited: boolean;
  base: TakeoffItem;
  /** 台帳(takeoff_items)の行ID。保存済みの案件を開いたときに入る */
  dbId?: string;
  /** 保存の状態。画面に「残った」ことを見せるため */
  saveState?: "idle" | "saving" | "saved" | "error";
};

const BADGE: Record<string, { mark: string; label: string }> = {
  low: { mark: "🔴", label: "要確認" },
  mid: { mark: "🟡", label: "念のため確認" },
  high: { mark: "🟢", label: "そのままでOK" },
};

/** 🔴要確認を上へ、その中は確度の低い順。直すべき行から目に入るようにする。 */
export function sortForReview(items: TakeoffItem[]): Row[] {
  return [...items]
    .sort((a, b) => {
      const aw = needsReview(a) ? 0 : 1;
      const bw = needsReview(b) ? 0 : 1;
      return aw - bw || a.confidence - b.confidence;
    })
    .map((it, i) => ({ ...it, id: i, edited: false, base: it }));
}

const cell =
  "w-full rounded-md border border-transparent bg-transparent px-2 py-1 hover:border-[var(--line)] focus:border-[var(--cyan)] focus:outline-none";

export function ReviewTable({
  rows,
  onEdit,
  onRemove,
  onRevert,
}: {
  rows: Row[];
  onEdit: (id: number, patch: Partial<Row>) => void;
  /** 渡すと行を消せるようになる（拾い過ぎ・二重計上・そもそも要らない行の始末） */
  onRemove?: (id: number) => void;
  /** 渡すと、直した行をAIが出した最初の値へ戻せるようになる */
  onRevert?: (id: number) => void;
}) {
  const hasTools = Boolean(onRemove || onRevert);
  return (
    <div className="overflow-x-auto rounded-[13px] border border-[var(--line)] bg-[var(--navy2)]">
      <table className="w-full min-w-[980px] border-collapse text-[14px]">
        <thead>
          <tr className="border-b border-[var(--line)] text-left text-[12px] tracking-wider text-[var(--mut)]">
            <th className="px-3 py-3 font-bold">確度</th>
            <th className="px-3 py-3 font-bold">チェック</th>
            <th className="px-3 py-3 font-bold">名称</th>
            <th className="px-3 py-3 font-bold">仕様</th>
            <th className="px-3 py-3 text-right font-bold">数量</th>
            <th className="px-3 py-3 font-bold">単位</th>
            <th className="px-3 py-3 font-bold">カテゴリ</th>
            <th className="px-3 py-3 font-bold">場所</th>
            {hasTools && <th className="px-2 py-3" />}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const badge = BADGE[confidenceLevel(r.confidence)];
            const flagged = (r.checks?.length ?? 0) > 0;
            return (
              <tr
                key={r.id}
                className="border-b border-[var(--line)] last:border-0 hover:bg-[rgba(86,204,242,0.04)]"
              >
                <td className="px-3 py-2 whitespace-nowrap">
                  <span
                    title={
                      r.saveState === "saving"
                        ? "保存中"
                        : r.saveState === "error"
                          ? "保存できませんでした"
                          : r.edited
                            ? "あなたが直した行"
                            : flagged
                              ? "要確認"
                              : badge.label
                    }
                  >
                    {r.saveState === "saving"
                      ? "⏳"
                      : r.saveState === "error"
                        ? "⚠️"
                        : r.edited
                          ? "✅"
                          : flagged
                            ? "🔴"
                            : badge.mark}
                  </span>
                </td>
                <td className="px-3 py-2 text-[12.5px] leading-snug text-[var(--red)]">
                  {(r.checks ?? []).join(" / ")}
                </td>
                <td className="px-3 py-2">
                  <input
                    value={r.name}
                    onChange={(e) => onEdit(r.id, { name: e.target.value })}
                    className={`${cell} min-w-[180px]`}
                  />
                </td>
                <td className="px-3 py-2">
                  <input
                    value={r.spec ?? ""}
                    onChange={(e) => onEdit(r.id, { spec: e.target.value })}
                    className={`${cell} min-w-[120px] text-[var(--mut)] focus:text-[var(--ink)]`}
                  />
                </td>
                <td className="px-3 py-2">
                  <input
                    type="number"
                    step="0.1"
                    value={r.quantity}
                    onChange={(e) => onEdit(r.id, { quantity: Number(e.target.value) })}
                    className={`${cell} tabular w-[90px] text-right`}
                  />
                </td>
                <td className="px-3 py-2">
                  <input
                    value={r.unit}
                    onChange={(e) => onEdit(r.id, { unit: e.target.value })}
                    className={`${cell} w-[70px]`}
                  />
                </td>
                <td className="px-3 py-2">
                  <input
                    value={r.category ?? ""}
                    onChange={(e) => onEdit(r.id, { category: e.target.value })}
                    className={`${cell} w-[110px] text-[var(--mut)] focus:text-[var(--ink)]`}
                  />
                </td>
                <td className="px-3 py-2">
                  <input
                    value={r.location ?? ""}
                    onChange={(e) => onEdit(r.id, { location: e.target.value })}
                    className={`${cell} w-[130px] text-[var(--mut)] focus:text-[var(--ink)]`}
                  />
                </td>
                {hasTools && (
                  <td className="px-2 py-2 whitespace-nowrap">
                    {onRevert && r.edited && (
                      <button
                        onClick={() => onRevert(r.id)}
                        title="この行をAIが出した最初の値に戻す"
                        className="rounded px-2 py-1 text-[13px] text-[var(--mut)] hover:bg-[rgba(86,204,242,0.12)] hover:text-[var(--cyan)]"
                      >
                        ↩
                      </button>
                    )}
                    {onRemove && (
                      <button
                        onClick={() => onRemove(r.id)}
                        title="この行を消す（要らない拾い出し・二重計上のとき）"
                        className="rounded px-2 py-1 text-[13px] text-[var(--mut)] hover:bg-[rgba(215,38,30,0.12)] hover:text-[var(--red)]"
                      >
                        ✕
                      </button>
                    )}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function StatCards({
  items,
}: {
  items: { label: string; value: string; color: string }[];
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {items.map((m) => (
        <div
          key={m.label}
          className="rounded-[11px] border border-[var(--line)] bg-[var(--panel)] px-4 py-3"
        >
          <p className="m-0 text-[12px] font-bold tracking-wider text-[var(--mut)]">
            {m.label}
          </p>
          <p className="tabular m-0 text-[26px] font-black" style={{ color: m.color }}>
            {m.value}
          </p>
        </div>
      ))}
    </div>
  );
}


/**
 * 直前の操作を取り消す口。
 *
 * 消す機能だけを付けて取り消しを付けないと、人は怖くて消せない
 * （消し間違いが取り返せないと、結局「消さずに残す」＝表が汚れたまま使われる）。
 * 消した行・直した行はここから戻す。
 */
export function UndoBar({
  count,
  label,
  onUndo,
  onDismiss,
}: {
  count: number;
  label: string;
  onUndo: () => void;
  onDismiss?: () => void;
}) {
  if (count <= 0) return null;
  return (
    <div className="mb-3 flex flex-wrap items-center gap-3 rounded-[11px] border border-[var(--line)] bg-[var(--panel)] px-4 py-2.5">
      <span className="text-[13.5px] text-[var(--mut)]">{label}</span>
      <button
        onClick={onUndo}
        className="rounded-[9px] border border-[var(--cyan)] px-3 py-1 text-[13px] font-bold text-[var(--cyan)] hover:bg-[rgba(86,204,242,0.10)]"
      >
        ↩ 元に戻す（{count}）
      </button>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="text-[12.5px] text-[var(--mut)] underline hover:text-[var(--ink)]"
        >
          このまま進む
        </button>
      )}
    </div>
  );
}
