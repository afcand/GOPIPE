"use client";

import { useEffect, useState } from "react";

type Seen = { color: string; count: number; hue: number | null };
type Meaning = { meaning: string; note?: string | null };

/** 色見本。実測した色相をそのまま画面の色に使う（図面の見え方と揃える）。 */
const SWATCH: Record<string, string> = {
  赤: "#d0342c",
  橙茶: "#b5763f",
  黄: "#c9a227",
  緑: "#3f8f4f",
  水: "#2ea3a8",
  青: "#1f5b96",
  藤紫: "#8a86c9",
  紫: "#7a3fa0",
  赤紫: "#b23a80",
};

/** よくある区分。打たせるより選ばせる（現場は打つのを嫌う）。 */
const PRESETS = ["新設", "既存再利用", "移設", "撤去", "既存", "他工事", "対象外"];

/**
 * 図面の色が何を意味するかを、会社に一度だけ聞く口。
 *
 * 🔴 色の意味は会社ごと・図面ごとにしか決まらない。
 * 実測(2026-08-20 ハルキ5枚): 「ダクト」が5枚で5通りの色。凡例があったのは1枚だけで、
 * しかも色見本でなく「色つき文字＋引出線」。その凡例の緑「(移設)」に至っては
 * 対応する作図要素が本体に0px＝凡例にあっても実体が無い。
 * だから凡例の自動発見に頼らず、人に一度だけ聞いて覚える。
 * 聞くのは **実際に図面へ出てきた色だけ**（出ない色を聞いても答えられない）。
 */
export default function ColorMeanings() {
  const [seen, setSeen] = useState<Seen[] | null>(null);
  const [meanings, setMeanings] = useState<Record<string, Meaning>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/color-meanings")
      .then((r) => r.json())
      .then((d) => {
        if (d?.error) throw new Error(d.error);
        setSeen((d.seen ?? []) as Seen[]);
        setMeanings((d.meanings ?? {}) as Record<string, Meaning>);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function save(color: string, meaning: string) {
    setBusy(color);
    setError("");
    setMsg("");
    try {
      const res = await fetch("/api/color-meanings", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ color, meaning }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d?.error ?? "保存できませんでした");
      setMeanings((cur) => {
        const n = { ...cur };
        if (meaning) n[color] = { meaning };
        else delete n[color];
        return n;
      });
      setMsg(meaning ? `${color} = ${meaning} を覚えました` : `${color} の意味を消しました`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  if (error && !seen) return <p className="text-[14px] text-[var(--red)]">{error}</p>;
  if (!seen) return <p className="text-[14px] text-[var(--mut)]">読み込んでいます…</p>;

  // まだ意味を決めていない色を先に出す（人に出す質問は少ないほどよい）
  const rows = [...seen].sort((a, b) => {
    const am = meanings[a.color] ? 1 : 0;
    const bm = meanings[b.color] ? 1 : 0;
    return am - bm || b.count - a.count;
  });

  if (rows.length === 0) {
    return (
      <p className="text-[14px] text-[var(--mut)]">
        まだ色が出ていません。図面を拾い出すと、その図面で使われていた色がここに並びます。
      </p>
    );
  }

  return (
    <>
      <ul className="m-0 list-none space-y-2 p-0">
        {rows.map((s) => {
          const cur = meanings[s.color]?.meaning ?? "";
          const val = draft[s.color] ?? cur;
          return (
            <li
              key={s.color}
              className={
                "flex flex-wrap items-center gap-3 rounded-[11px] border px-4 py-3 " +
                (cur
                  ? "border-[var(--line)] bg-[var(--panel)]"
                  : "border-[rgba(255,171,51,0.5)] bg-[rgba(255,171,51,0.06)]")
              }
            >
              <span
                className="h-6 w-6 shrink-0 rounded-md border border-[var(--line)]"
                style={{ background: SWATCH[s.color] ?? "#888" }}
                title={s.hue !== null ? `色相 ${s.hue}°` : undefined}
              />
              <span className="min-w-[86px] text-[15px] font-bold">{s.color}</span>
              <span className="min-w-[92px] text-[12.5px] text-[var(--mut)]">
                {s.count} 行で出現
              </span>
              <select
                value={PRESETS.includes(val) || val === "" ? val : "__other"}
                onChange={(e) => {
                  const v = e.target.value;
                  setDraft((d) => ({ ...d, [s.color]: v === "__other" ? "" : v }));
                  if (v !== "__other") void save(s.color, v);
                }}
                disabled={busy === s.color}
                className="min-w-[150px] rounded-[10px] border border-[var(--line)] bg-[var(--navy2)] px-3 py-2 text-[14px] focus:border-[var(--cyan)] focus:outline-none"
              >
                <option value="">（未設定）</option>
                {PRESETS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
                <option value="__other">自分で書く…</option>
              </select>
              {!PRESETS.includes(val) && (
                <span className="flex items-center gap-2">
                  <input
                    value={draft[s.color] ?? cur}
                    onChange={(e) => setDraft((d) => ({ ...d, [s.color]: e.target.value }))}
                    placeholder="この会社の言葉で"
                    className="w-[170px] rounded-[10px] border border-[var(--line)] bg-[var(--navy2)] px-3 py-2 text-[14px] focus:border-[var(--cyan)] focus:outline-none"
                  />
                  <button
                    onClick={() => void save(s.color, (draft[s.color] ?? "").trim())}
                    disabled={busy === s.color}
                    className="rounded-[9px] border border-[var(--cyan)] px-3 py-1.5 text-[13px] font-bold text-[var(--cyan)] hover:bg-[rgba(86,204,242,0.10)] disabled:opacity-60"
                  >
                    {busy === s.color ? "保存中…" : "覚える"}
                  </button>
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {msg && <p className="mt-3 mb-0 text-[13.5px] text-[var(--cyan)]">{msg}</p>}
      {error && <p className="mt-3 mb-0 text-[13.5px] text-[var(--red)]">{error}</p>}
    </>
  );
}
