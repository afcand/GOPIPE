"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { needsReview, type TakeoffItem } from "@/lib/gopipe";
import { ReviewTable, StatCards, sortForReview, type Row } from "@/components/ReviewTable";
import { supabaseBrowser } from "@/lib/supabase/client";

type Phase = "" | "upload" | "run" | "learn" | "excel";

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
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error ?? "拾い出しに失敗しました");

      const items: TakeoffItem[] = data.items ?? [];
      setRows(sortForReview(items));
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
          })),
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

        {rows && (
          <>
            <div className="mt-8">
              <StatCards
                items={[
                  { label: "拾い出した明細", value: `${rows.length} 件`, color: "var(--ink)" },
                  { label: "要確認（🔴）", value: `${warnCount} 件`, color: "var(--red)" },
                  { label: "直した行", value: `${edited.length} 件`, color: "var(--cyan)" },
                ]}
              />
            </div>

            <p className="mt-5 mb-4 text-[14px] text-[var(--mut)]">
              🔴の行を上にまとめてあります。名称・数量・単位・カテゴリはその場で直せます。
            </p>

            <ReviewTable rows={rows} onEdit={edit} />

            <div className="mt-6 flex flex-wrap items-center gap-3">
              {projectId && (
                <a
                  href={`/app/projects/${projectId}`}
                  className="rounded-[11px] border border-[var(--line)] px-5 py-3 text-[15px] font-bold text-[var(--ink)] hover:border-[var(--cyan)]"
                >
                  この物件を開く（続きから直せます）
                </a>
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
