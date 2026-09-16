"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { needsReview, type TakeoffItem } from "@/lib/gopipe";
import { ReviewTable, StatCards, BulkBar, ExportSummary, UndoBar, sortForReview, type Row } from "@/components/ReviewTable";
import { supabaseBrowser } from "@/lib/supabase/client";

type Phase = "" | "upload" | "run" | "learn" | "excel";

/** 図面にあるのに、この経路では数えられなかったもの（記号もの・延長・冷媒 等）。 */
type Gap = { item: string; reason: string; action: string; pages: number[] };

export default function Workbench({
  orgSlug,
  orgName,
  email,
}: {
  orgSlug: string;
  orgName: string;
  email: string;
}) {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [phase, setPhase] = useState<Phase>("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [projectId, setProjectId] = useState<string | null>(null);
  // ベクター(CAD)図は印字だけで拾える。画像認識を使わない＝費用0・何度やっても同じ数。
  // 大判が何枚もあると1枚あたりの解像度が落ちるので、読めない画から出た数量が混ざる。
  const [noLlm, setNoLlm] = useState(false);
  // この図面にあるのに数えられなかったもの。表に出ない部材は現場から見れば0個に見える。
  const [gaps, setGaps] = useState<Gap[]>([]);
  const [unread, setUnread] = useState<{ page: number; text: string }[]>([]);
  const [llmCalls, setLlmCalls] = useState<number | null>(null);

  const warnCount = useMemo(
    () => (rows ?? []).filter((r) => needsReview(r)).length,
    [rows],
  );
  const edited = useMemo(() => (rows ?? []).filter((r) => r.edited), [rows]);
  const busy = phase !== "";
  const [dragging, setDragging] = useState(false);

  // ドラッグ＆ドロップで図面を受ける。パネルだけでなく画面のどこへ落としても
  // 受かるようにする。window 側で既定動作を止めるのは、狙いが数px外れたときに
  // ブラウザがPDFを開いて作業画面ごと消える事故を防ぐため。
  const acceptDropped = useCallback(
    (list: FileList | null | undefined) => {
      if (busy) return; // 実行中の差し替えは事故のもと（読んでいる図面と表示が食い違う）
      const dropped = Array.from(list ?? []).find(
        (f) =>
          /\.(pdf|jpe?g|png|heic)$/i.test(f.name) ||
          ["application/pdf", "image/jpeg", "image/png", "image/heic"].includes(f.type),
      );
      if (!dropped) {
        setError("PDFか写真（JPEG・PNG・HEIC）を落としてください");
        return;
      }
      setError("");
      setNotice((list?.length ?? 0) > 1 ? "図面は1枚ずつ読みます。最初の1枚を選びました" : "");
      setFile(dropped);
    },
    [busy],
  );

  useEffect(() => {
    const hasFiles = (e: DragEvent) =>
      Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const over = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      setDragging(true);
    };
    const leave = (e: DragEvent) => {
      // 画面の外へ出たときだけ消す（子要素間の移動では relatedTarget が入る）
      if (!e.relatedTarget) setDragging(false);
    };
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      setDragging(false);
      acceptDropped(e.dataTransfer?.files);
    };
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, [acceptDropped]);

  async function run() {
    if (!file) {
      setError("設備図のPDFを選んでください");
      return;
    }
    setError("");
    setNotice("");
    try {
      // 図面はブラウザから Storage へ直接送る（Vercel 経由だと 4.5MB で頭打ちになる）
      setPhase("upload");
      const stamp = new Date().toISOString().slice(0, 10);
      // 置き場所の名前は半角英数だけにする。Supabase Storage は日本語のキーを
      // 受け付けず「Invalid key」で弾く。設備図のファイル名は日本語が普通なので、
      // ここを日本語のままにすると実務の図面がほぼ全部アップロードできない。
      // 人が見る名前（file.name）は別に持っていって、画面にはそちらを出す。
      const ext = (file.name.match(/\.[A-Za-z0-9]+$/)?.[0] ?? ".pdf").toLowerCase();
      // 日本語だけの名前だと空になるので、短すぎたら drawing に落とす
      const stem = file.name.replace(/\.[^.]+$/, "").replace(/[^\w.\-]/g, "").slice(0, 24);
      const asciiStem = stem.replace(/^[_\-.]+|[_\-.]+$/g, "") || "drawing";
      const path = `${orgSlug}/${stamp}_${Date.now()}_${asciiStem}${ext}`;
      const sb = supabaseBrowser();
      const { error: upErr } = await sb.storage
        .from("drawings")
        // 実際の種類で上げる。PDF固定にすると写真が壊れる
        .upload(path, file, {
          contentType: file.type || "application/pdf",
          upsert: false,
        });
      if (upErr) throw new Error(`図面のアップロードに失敗しました: ${upErr.message}`);

      setPhase("run");
      const projectSlug =
        (title.trim() || file.name.replace(/\.pdf$/i, ""))
          .replace(/\s+/g, "-")
          .slice(0, 60) || "untitled";
      const res = await fetch("/api/run", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          storagePath: path,
          projectSlug,
          title: title.trim(),
          fileName: file.name, // 画面に出すのは元の日本語のファイル名
          noLlm,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error ?? "拾い出しに失敗しました");

      const items: TakeoffItem[] = data.items ?? [];
      setRows(sortForReview(items));
      // 🔴 拾えていないものは、明細と同じ画面に出す。別画面へ送ると誰も見ない。
      setGaps(Array.isArray(data?.gaps) ? data.gaps : []);
      setUnread(Array.isArray(data?.unread_labels) ? data.unread_labels : []);
      setLlmCalls(typeof data?.llm_calls === "number" ? data.llm_calls : null);
      const saved = data?.persisted?.error
        ? "（案件の保存には失敗しました）"
        : "物件として保存しました。あとから物件一覧で開き直せます。";
      const warn = Array.isArray(data?.warnings) && data.warnings.length
        ? ` ⚠️ ${data.warnings.join(" / ")}`
        : "";
      setProjectId(data?.persisted?.project_id ?? null);
      setNotice(`${items.length} 件を拾い出しました。${saved}${warn}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPhase("");
    }
  }

  function edit(id: number, patch: Partial<Row>) {
    setRows((cur) => (cur ?? []).map((r) => (r.id === id ? { ...r, ...patch, edited: true } : r)));
  }

  /** 消した行の控え。消す機能だけ付けて取り消しが無いと、怖くて誰も消さない。 */
  const [trash, setTrash] = useState<{ row: Row; at: number }[]>([]);

  /** 要らない拾い出しを消す（拾い過ぎ・二重計上・そもそも対象外）。 */
  function removeRow(id: number) {
    setRows((cur) => {
      const target = (cur ?? []).find((r) => r.id === id);
      if (target) setTrash((t) => [...t, { row: target, at: (cur ?? []).indexOf(target) }]);
      return (cur ?? []).filter((r) => r.id !== id);
    });
  }

  /** 消した行を全部、元の位置へ戻す（末尾に足すとどこにあった行か分からなくなる）。 */
  function undoRemove() {
    setTrash((t) => {
      if (t.length === 0) return t;
      setRows((cur) => {
        const next = [...(cur ?? [])];
        // 位置の小さい順に戻すと後続の添字がずれる。大きい順に差し込む。
        [...t].sort((a, b) => b.at - a.at).forEach((x) => {
          next.splice(Math.min(x.at, next.length), 0, x.row);
        });
        return next;
      });
      return [];
    });
  }

  /** まとめて消すための選択。要らない行は数十件まとめて出るので1件ずつでは続かない。 */
  const [picked, setPicked] = useState<Set<number>>(new Set());
  function toggleOne(id: number) {
    setPicked((cur) => {
      const n = new Set(cur);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }
  function toggleAll() {
    setPicked((cur) =>
      cur.size === (rows ?? []).length ? new Set() : new Set((rows ?? []).map((r) => r.id)),
    );
  }
  /** 選んだ行をまとめて消す（1回の取り消しで全部戻せるよう、まとめて控える）。 */
  function removePicked() {
    if (picked.size === 0) return;
    setRows((cur) => {
      const list = cur ?? [];
      const gone = list
        .map((r, at) => ({ row: r, at }))
        .filter((x) => picked.has(x.row.id));
      if (gone.length) setTrash((t) => [...t, ...gone]);
      return list.filter((r) => !picked.has(r.id));
    });
    setPicked(new Set());
  }

  /** 直した行をAIが出した最初の値へ戻す。base は行が抱えている。 */
  function revertRow(id: number) {
    setRows((cur) =>
      (cur ?? []).map((r) => (r.id === id ? { ...r, ...r.base, id: r.id, base: r.base, edited: false } : r)),
    );
  }

  /** 直した行を「直す前 → 直した後」の組にする。直す前は行が抱えている base。 */
  function corrections() {
    return edited
      .filter((r) => r.base.name)
      .map((r) => ({
        before: {
          // 学習の鍵はAIが読んだ生名称。表示名を送ると別部材まで巻き添えで化ける。
          raw_name: r.base.raw_name ?? r.base.name,
          name: r.base.name,
          spec: r.base.spec,
          quantity: r.base.quantity,
          unit: r.base.unit,
          category: r.base.category,
        },
        after: {
          name: r.name,
          spec: r.spec,
          quantity: r.quantity,
          unit: r.unit,
          category: r.category,
        },
      }));
  }

  async function learn() {
    if (edited.length === 0) return;
    setPhase("learn");
    setError("");
    try {
      const res = await fetch("/api/learn", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ corrections: corrections() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error ?? "記録に失敗しました");
      setNotice(
        data.learned > 0
          ? `${data.learned} 件の言い換えを覚えました。次からこの名前で出ます。`
          : `${data.captured} 件の修正を記録しました（名前の言い換えはありませんでした）。`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPhase("");
    }
  }

  async function downloadExcel() {
    if (!rows?.length) return;
    setPhase("excel");
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
            confidence: r.edited ? 1 : r.confidence,
            // 🔴これを送らないと、Excelの備考（出所）が丸ごと空になる。
            // 確かな数と推定が同じ顔で並ぶのを止めるための列なので落とせない。
            qty_basis: r.qty_basis ?? null,
            qty_cv: r.qty_cv ?? null,
            source: r.source ?? null,
            color: r.color ?? null,
            color_hue: r.color_hue ?? null,
            level_mm: r.level_mm ?? null,
            page: r.page ?? 1,
          })),
          // 🔴 これを送らないと、Excel から「拾えていないもの」シートが消える。
          gaps,
          unread_labels: unread,
        }),
      });
      if (!res.ok) throw new Error("Excel の生成に失敗しました");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${title.trim() || "GOPIPE_拾い出し"}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPhase("");
    }
  }

  async function logout() {
    await supabaseBrowser().auth.signOut();
    router.push("/login");
    router.refresh();
  }

  const label =
    phase === "upload"
      ? "図面を送っています…"
      : phase === "run"
        ? "AIが図面を読んでいます（1〜3分ほど）…"
        : "拾い出しを実行";

  return (
    <main className="mx-auto w-full max-w-[1100px] px-5 pb-24">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] py-6">
        <div>
          <p className="m-0 text-[12px] font-bold tracking-[0.3em] text-[var(--cyan)]">
            GOPIPE
          </p>
          <p className="m-0 text-[20px] font-black">{orgName}</p>
        </div>
        <div className="text-right text-[12.5px] text-[var(--mut)]">
          <p className="m-0">
            <a href="/app/pick" className="font-bold text-[var(--cyan)] hover:underline">
              指して拾う
            </a>
            <span className="mx-2 opacity-40">|</span>
            <a href="/app/measure" className="font-bold text-[var(--cyan)] hover:underline">
              現地実測
            </a>
            <span className="mx-2 opacity-40">|</span>
            <a href="/app/maintenance" className="font-bold text-[var(--cyan)] hover:underline">
              更新提案
            </a>
            <span className="mx-2 opacity-40">|</span>
            <a href="/app/projects" className="font-bold text-[var(--cyan)] hover:underline">
              物件一覧
            </a>
            <span className="mx-2 opacity-40">|</span>
            <a href="/app/dictionary" className="font-bold text-[var(--cyan)] hover:underline">
              覚えた言い換え
            </a>
            <span className="mx-2 opacity-40">|</span>
            <a href="/app/guide" className="font-bold text-[var(--cyan)] hover:underline">
              使い方
            </a>
            <span className="mx-2 opacity-40">|</span>
            {email}
          </p>
          <button onClick={logout} className="font-bold text-[var(--cyan)] hover:underline">
            ログアウト
          </button>
        </div>
      </header>

      <section className="py-9">
        <h1 className="mt-0 mb-1 text-[22px] font-black">設備図から拾い出す</h1>
        <p className="mt-0 mb-6 text-[14px] text-[var(--mut)]">
          設備図（PDF・写真）を選ぶか、この画面のどこかへドラッグして実行すると、AIが下書きを作ります。直した内容は会社の辞書に覚えさせられます。
          手書きの図面も読めますが、印字より精度は落ちます（数字は必ずご確認ください）。
        </p>

        <div
          className={
            "flex flex-col gap-3 rounded-[13px] border p-5 sm:flex-row sm:items-center " +
            (dragging
              ? "border-2 border-dashed border-[var(--cyan)] bg-[rgba(86,204,242,0.10)]"
              : "border-[var(--line)] bg-[var(--panel)]")
          }
        >
          <label className="cursor-pointer rounded-[10px] border border-dashed border-[var(--cyan)] px-5 py-2.5 text-[14px] font-bold whitespace-nowrap text-[var(--cyan)] hover:bg-[rgba(86,204,242,0.08)]">
            {dragging
              ? "ここに離すと読み込みます"
              : file
                ? `📄 ${file.name}`
                : "設備図を選ぶ／ここへドラッグ"}
            <input
              ref={fileRef}
              type="file"
              // 手書き図面をスマホで撮った写真も受ける。現場にPDFが無いことは普通にある。
              accept="application/pdf,image/jpeg,image/png,image/heic"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="hidden"
            />
          </label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="物件名（任意）"
            className="min-w-0 flex-1 rounded-[10px] border border-[var(--line)] bg-[var(--navy2)] px-4 py-2.5 text-[15px] focus:border-[var(--cyan)] focus:outline-none"
          />
          <button
            onClick={run}
            disabled={busy}
            className="rounded-[11px] bg-gradient-to-b from-[#ffab33] to-[var(--orange)] px-6 py-3 text-[16px] font-black whitespace-nowrap text-[#241200] disabled:opacity-60"
          >
            {busy ? label : "拾い出しを実行"}
          </button>
        </div>

        {/* CAD から書き出したPDFは、図面の中の文字をそのまま読める。画像認識を通さない分、
            費用が出ず、同じ図面なら毎回同じ数になる。スキャン・写真では文字が無いので効かない。 */}
        <label className="mt-3 flex cursor-pointer items-start gap-2.5 text-[13.5px] text-[var(--mut)]">
          <input
            type="checkbox"
            checked={noLlm}
            onChange={(e) => setNoLlm(e.target.checked)}
            disabled={busy}
            className="mt-0.5 h-4 w-4 accent-[var(--cyan)]"
          />
          <span>
            <b className="text-[var(--ink)]">画像認識を使わずに拾う（CADから書き出したPDF向け）</b>
            <br />
            図面に印字されている文字だけを数えます。AIの推測が入らないので、同じ図面なら毎回同じ数が出ます。
            スキャンした紙・写真では文字が無いため、こちらは外してください（AIが画像から読み取ります）。
          </span>
        </label>

        <div className="mt-3">
          {/* 初めての人が、自分の図面を探さずにその場で試せるようにする。
              練習で会社の台帳を汚さないよう、物件名に「練習」を入れて使う運用。 */}
          <a
            href="/app/guide"
            className="inline-flex items-center gap-2 rounded-[10px] border border-[var(--cyan)] px-4 py-2 text-[13.5px] font-bold text-[var(--cyan)] hover:bg-[rgba(86,204,242,0.08)]"
          >
            ▶ はじめての方へ：使い方を見る（3分）
          </a>
          <a
            href="/sample/practice-drawing.pdf"
            download="GoPipe練習用_中央ビル_1F給排水.pdf"
            className="ml-4 text-[13px] text-[var(--mut)] underline hover:text-[var(--cyan)]"
          >
            練習用の設備図をダウンロード
          </a>
        </div>

        {/* 図面から始まらない仕事の入口。現場にPDFが無い日と、過去の物件を売る日。 */}
        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          <a
            href="/app/measure"
            className="rounded-[12px] border border-[var(--line)] bg-[var(--panel)] px-5 py-4 transition hover:border-[var(--cyan)]"
          >
            <p className="m-0 text-[15px] font-black">📏 現地実測から拾い出す</p>
            <p className="m-0 mt-1 text-[13px] text-[var(--mut)]">
              図面が無い現場で、測った寸法をそのまま入れる
            </p>
          </a>
          <a
            href="/app/maintenance"
            className="rounded-[12px] border border-[var(--line)] bg-[var(--panel)] px-5 py-4 transition hover:border-[var(--cyan)]"
          >
            <p className="m-0 text-[15px] font-black">🔧 更新提案をつくる</p>
            <p className="m-0 mt-1 text-[13px] text-[var(--mut)]">
              前に拾った物件から、そろそろ点検どきの配管を出す
            </p>
          </a>
        </div>

        {phase === "run" && (
          <p className="mt-4 text-[14px] text-[var(--cyan)]">
            AIが図面を読んでいます。画面を閉じずにお待ちください（1〜3分ほど）。
          </p>
        )}
        {error && (
          <p className="mt-5 rounded-[11px] border border-[rgba(215,38,30,0.35)] bg-[rgba(215,38,30,0.08)] px-4 py-3 text-[14px] text-[#f2c7c4]">
            {error}
          </p>
        )}
        {notice && !error && (
          <p className="mt-5 rounded-[11px] border border-[rgba(86,204,242,0.35)] bg-[rgba(86,204,242,0.08)] px-4 py-3 text-[14px] text-[var(--cyan)]">
            {notice}
          </p>
        )}

        {rows && gaps.length > 0 && (
          <section className="mt-6 rounded-[13px] border border-[rgba(255,171,51,0.45)] bg-[rgba(255,171,51,0.07)] px-5 py-4">
            <p className="m-0 text-[15px] font-black text-[#ffab33]">
              この図面から拾えていないもの（{gaps.length}件）
            </p>
            <p className="mt-1 mb-3 text-[13px] text-[var(--mut)]">
              下の表に出てこない分です。「無い」のではなく「数えられなかった」ものなので、見積に入れる前にご確認ください。
            </p>
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {gaps.map((g, i) => (
                <li key={i} className="border-t border-[var(--line)] pt-3 first:border-t-0 first:pt-0">
                  <p className="m-0 text-[14px] font-bold text-[var(--ink)]">
                    {g.item}
                    {g.pages?.length > 0 && (
                      <span className="ml-2 text-[12px] font-normal text-[var(--mut)]">
                        {g.pages.length > 6
                          ? `ページ ${g.pages.slice(0, 6).join("・")} ほか${g.pages.length - 6}枚`
                          : `ページ ${g.pages.join("・")}`}
                      </span>
                    )}
                  </p>
                  <p className="m-0 mt-0.5 text-[13px] text-[var(--mut)]">{g.reason}</p>
                  <p className="m-0 mt-0.5 text-[13px] text-[var(--cyan)]">→ {g.action}</p>
                </li>
              ))}
            </ul>
            {unread.length > 0 && (
              <p className="mt-3 mb-0 text-[12.5px] text-[var(--mut)]">
                このほか、図面の文字 {unread.length} 件は品目の型に当てはめられませんでした（Excel の別シートに全文を出します）。
              </p>
            )}
          </section>
        )}

        {rows && (
          <>
            <div className="mt-8">
              <StatCards
                items={[
                  { label: "拾い出した明細", value: `${rows.length} 件`, color: "var(--ink)" },
                  { label: "要確認（🔴）", value: `${warnCount} 件`, color: "var(--red)" },
                  { label: "直した行", value: `${edited.length} 件`, color: "var(--cyan)" },
                  ...(llmCalls === null
                    ? []
                    : [{
                        label: "画像認識",
                        value: llmCalls === 0 ? "使っていません" : `${llmCalls} 回`,
                        color: llmCalls === 0 ? "var(--cyan)" : "var(--ink)",
                      }]),
                ]}
              />
            </div>

            <p className="mt-5 mb-4 text-[14px] text-[var(--mut)]">
              🔴の行を上にまとめてあります。名称・数量・単位・カテゴリはその場で直せます。
            </p>

            <UndoBar
              count={trash.length}
              label={`消した行が ${trash.length} 件あります`}
              onUndo={undoRemove}
              onDismiss={() => setTrash([])}
            />
            <ExportSummary rows={rows} removed={trash.length} />
            <BulkBar count={picked.size} onRemove={removePicked} onClear={() => setPicked(new Set())} />
            <ReviewTable
              rows={rows}
              onEdit={edit}
              onRemove={removeRow}
              onRevert={revertRow}
              selected={picked}
              onToggleSelect={toggleOne}
              onToggleAll={toggleAll}
            />

            <div className="mt-6 flex flex-wrap items-center gap-3">
              {projectId && (
                <>
                  <a
                    href={`/app/projects/${projectId}`}
                    className="rounded-[11px] border border-[var(--line)] px-5 py-3 text-[15px] font-bold text-[var(--ink)] hover:border-[var(--cyan)]"
                  >
                    この物件を開く（続きから直せます）
                  </a>
                  {/* 拾い切れなかったところを、その場で範囲・色・記号で足しに行く */}
                  <a
                    href={`/app/pick?project=${projectId}`}
                    className="rounded-[11px] border border-[var(--orange)] px-5 py-3 text-[15px] font-bold text-[var(--orange)] hover:bg-[rgba(255,171,51,0.08)]"
                  >
                    図面を見て拾い足す
                  </a>
                </>
              )}
              <button
                onClick={learn}
                disabled={busy || edited.length === 0}
                className="rounded-[11px] bg-[var(--cyan)] px-6 py-3 text-[15px] font-black text-[#04121f] disabled:opacity-40"
              >
                {phase === "learn"
                  ? "覚えています…"
                  : `この直しを覚えさせる（${edited.length}件）`}
              </button>
              <button
                onClick={downloadExcel}
                disabled={busy}
                className="rounded-[11px] border border-[var(--cyan)] px-6 py-3 text-[15px] font-bold text-[var(--cyan)] hover:bg-[rgba(86,204,242,0.08)] disabled:opacity-60"
              >
                {phase === "excel" ? "作成中…" : "Excel で書き出す"}
              </button>
            </div>
          </>
        )}
      </section>
    </main>
  );
}
