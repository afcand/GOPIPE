"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { supabaseBrowser } from "@/lib/supabase/client";
import type { TakeoffItem } from "@/lib/gopipe";

/**
 * 指して拾う画面。
 *
 * 1枚を丸ごと読ませると、大判ほど実効解像度が落ち、断面図や別階の図が混ざり、
 * 割り方ひとつで数量が変わる（同じA3スキャンで冷水管が 17m/12m/4.5m/13m になった実測がある）。
 * 人が範囲を囲めば、その3つが同時に消える。囲んだ範囲は、そのまま「どこを拾ったか」の記録になる。
 */

type Gap = { item: string; reason: string; action: string; pages: number[] };
type Tool = "range" | "color" | "duct" | "pipe" | "symbol";
/** 覚えている指示。箇所（前回どこを拾ったか）と色（この色はこう拾う）。 */
type Saved = {
  kind: "region" | "color";
  sheet_key: string;
  ref: string;
  payload: Record<string, string | number>;
};
/** 指した1点の結果。色・ダクト・記号で中身が変わる。 */
type Point = {
  id: number;
  tool: Tool;
  page: number;
  at: { x: number; y: number };
  label: string;
  note: string;
  detail: Record<string, unknown>;
  items: TakeoffItem[];
};
type Pick = {
  id: number;
  label: string;
  page: number;
  box: { x0: number; y0: number; x1: number; y1: number };
  items: TakeoffItem[];
  gaps: Gap[];
  llmCalls: number;
  /** その範囲にある線の長さ（色別）。印字が拾えない図面でも寸法だけは出る。 */
  measures?: { total_m: number; by_color: { hex: string; length_m: number; shapes: number }[];
               scale?: { value: number; how: string } };
  /** 0件だったときの理由（図面が悪いのか、指す場所か、読ませ方かを言い分ける） */
  reason?: string;
};

export default function Picker({
  orgSlug,
  orgName,
  project,
}: {
  orgSlug: string;
  orgName: string;
  /** 物件を指定して開いたとき。図面はそのまま開き、拾った行はこの物件へ足す。 */
  project?: { id: string; title: string; storagePath: string; fileName: string } | null;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [path, setPath] = useState<string>(project?.storagePath ?? "");
  const [added, setAdded] = useState(0);
  const [img, setImg] = useState<string>("");
  const [page, setPage] = useState(1);
  const [pageCount, setPageCount] = useState(1);
  const [busy, setBusy] = useState<"" | "upload" | "page" | "run">("");
  const [error, setError] = useState("");
  const [noLlm, setNoLlm] = useState(true);
  const [picks, setPicks] = useState<Pick[]>([]);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [tool, setTool] = useState<Tool>("range");
  const [points, setPoints] = useState<Point[]>([]);
  const [meaning, setMeaning] = useState<Record<string, string>>({});
  const [sheetKey, setSheetKey] = useState("");
  const [pageHasText, setPageHasText] = useState(true);
  // 縮尺の分母（1/50 なら 50）。空なら図面から自分で測る。
  // 図枠の表記が実物と違う図面があり、範囲を切ると通り芯ごと落ちて測れないこともある。
  const [scaleDenom, setScaleDenom] = useState("");
  const [saved, setSaved] = useState<Saved[]>([]);
  // 色をどう拾うか（名前・単位・数える/長さ/拾わない）。名前だけでは数量にならない。
  const [rule, setRule] = useState<Record<string, { name: string; action: string; unit: string }>>({});
  const boxRef = useRef<HTMLDivElement>(null);

  // 物件を指定して来たら、アップロードを挟まずそのまま図面を開く
  useEffect(() => {
    if (project?.storagePath) loadPage(project.storagePath, 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.storagePath]);

  const busyLabel =
    busy === "upload" ? "図面を送っています…" : busy === "page" ? "図面を開いています…" : "この範囲を拾っています…";

  /** 画像の中での位置を 0〜1 で取る。表示倍率が変わっても同じ値になる。 */
  const rel = useCallback((e: React.MouseEvent) => {
    const r = boxRef.current?.getBoundingClientRect();
    if (!r) return { x: 0, y: 0 };
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    };
  }, []);

  async function loadPage(storagePath: string, p: number) {
    setBusy("page");
    setError("");
    try {
      const res = await fetch("/api/page", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ storagePath, page: p, dpi: 120 }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({})))?.error ?? "図面を開けませんでした");
      setPageCount(Number(res.headers.get("x-page-count") ?? 1));
      const sk = res.headers.get("x-sheet-key") ?? "";
      setSheetKey(sk);
      loadSaved(sk);
      // 🔴 紙のスキャンには文字が無い。「画像認識を使わない」のままだと必ず0件になり、
      // 画面からは理由が分からない（実際にそうなった）。開いた時点で倒しておく。
      const hasText = (res.headers.get("x-has-text") ?? "1") === "1";
      setPageHasText(hasText);
      if (!hasText) setNoLlm(false);
      const blob = await res.blob();
      setImg((old) => {
        if (old) URL.revokeObjectURL(old);
        return URL.createObjectURL(blob);
      });
      setPage(p);
      setDrag(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  /** この図面の型に覚えさせてある指示を読む。 */
  async function loadSaved(sk: string) {
    try {
      const res = await fetch(`/api/instructions?sheetKey=${encodeURIComponent(sk)}`, {
        cache: "no-store",
      });
      if (!res.ok) return;
      const data = await res.json();
      setSaved(Array.isArray(data?.instructions) ? data.instructions : []);
    } catch {
      /* 覚えた指示が読めなくても拾い出しはできる */
    }
  }

  /** 指示を覚えさせる（箇所／色）。 */
  async function remember(kind: "region" | "color", payload: Record<string, unknown>) {
    setError("");
    const res = await fetch("/api/instructions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ kind, sheet_key: sheetKey, payload }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      setError(d?.error ?? "覚えられませんでした");
      return false;
    }
    await loadSaved(sheetKey);
    return true;
  }

  async function upload() {
    if (!file) {
      setError("図面のPDFを選んでください");
      return;
    }
    setBusy("upload");
    setError("");
    try {
      const stamp = new Date().toISOString().slice(0, 10);
      const ext = (file.name.match(/\.[A-Za-z0-9]+$/)?.[0] ?? ".pdf").toLowerCase();
      const stem = file.name.replace(/\.[^.]+$/, "").replace(/[^\w.\-]/g, "").slice(0, 24);
      const asciiStem = stem.replace(/^[_\-.]+|[_\-.]+$/g, "") || "drawing";
      const p = `${orgSlug}/${stamp}_${Date.now()}_${asciiStem}${ext}`;
      const { error: upErr } = await supabaseBrowser()
        .storage.from("drawings")
        .upload(p, file, { contentType: file.type || "application/pdf", upsert: false });
      if (upErr) throw new Error(`図面のアップロードに失敗しました: ${upErr.message}`);
      setPath(p);
      setPicks([]);
      await loadPage(p, 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy("");
    }
  }

  async function runRegion() {
    if (!path || !drag) return;
    const box = {
      x0: Math.min(drag.x0, drag.x1), y0: Math.min(drag.y0, drag.y1),
      x1: Math.max(drag.x0, drag.x1), y1: Math.max(drag.y0, drag.y1),
    };
    if (box.x1 - box.x0 < 0.02 || box.y1 - box.y0 < 0.02) {
      setError("範囲が小さすぎます。もう少し広く囲んでください");
      return;
    }
    setBusy("run");
    setError("");
    try {
      const res = await fetch("/api/run", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          storagePath: path,
          projectSlug: "pick",
          title: file?.name ?? "",
          fileName: file?.name ?? "",
          noLlm,
          region: { page, ...box },
          scaleDenom: scaleDenom.trim() ? Number(scaleDenom) : 0,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error ?? "拾い出しに失敗しました");
      const gotItems: TakeoffItem[] = data.items ?? [];
      const regionLabel: string = data?.region?.label ?? `p${page}`;
      addToProject(gotItems, regionLabel);
      setPicks((cur) => [
        ...cur,
        {
          id: Date.now(),
          label: regionLabel,
          page,
          box,
          items: data.items ?? [],
          gaps: Array.isArray(data.gaps) ? data.gaps : [],
          llmCalls: typeof data.llm_calls === "number" ? data.llm_calls : 0,
          reason: typeof data.reason === "string" ? data.reason : "",
          measures: data.measures,
        },
      ]);
      setDrag(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  /** 図面の1点を指す（色・ダクト・記号）。囲まずにクリックするだけ。 */
  async function pickPoint(at: { x: number; y: number }) {
    if (!path || tool === "range") return;
    setBusy("run");
    setError("");
    try {
      const res = await fetch("/api/pick", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          storagePath: path, page, mode: tool, x: at.x, y: at.y,
          scaleDenom: scaleDenom.trim() ? Number(scaleDenom) : 0,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error ?? "指した場所を拾えませんでした");
      const picked: TakeoffItem[] = data.items ?? [];
      addToProject(picked, `p${page} 指した点`);
      setPoints((cur) => [
        ...cur,
        {
          id: Date.now(), tool, page, at,
          label: data.label ?? "", note: data.note ?? "",
          detail: data.detail ?? {}, items: data.items ?? [],
        },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  /** 拾った行を、その物件の明細に足す（1回まるごと拾ったものの続きになる）。 */
  async function addToProject(items: TakeoffItem[], where: string) {
    if (!project?.id || items.length === 0) return;
    let ok = 0;
    for (const it of items) {
      try {
        const res = await fetch("/api/items/rows", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            projectId: project.id,
            item: {
              name: it.name,
              spec: it.spec ?? null,
              quantity: it.quantity,
              unit: it.unit,
              // どこを指して出た行かを残す。あとから二重を見分ける手がかりになる。
              location: it.location ? `${it.location}／${where}` : where,
              category: it.category ?? null,
            },
          }),
        });
        if (res.ok) ok += 1;
      } catch {
        /* 1行ずつなので、失敗した行だけが落ちる */
      }
    }
    if (ok) setAdded((n) => n + ok);
    if (ok < items.length) setError(`${items.length - ok} 行は物件に足せませんでした`);
  }

  /** 色の意味を会社の辞書へ覚えさせる。意味を当てるのは人。 */
  async function rememberColor(hex: string) {
    const m = (meaning[hex] ?? "").trim();
    if (!m) return;
    try {
      const res = await fetch("/api/color-meanings", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ color: hex, meaning: m }),
      });
      if (!res.ok) throw new Error("覚えられませんでした");
      setMeaning((cur) => ({ ...cur, [hex]: "" }));
      setPoints((cur) =>
        cur.map((p) => (p.detail?.hex === hex ? { ...p, note: `${m} として覚えました` } : p)),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const allItems = [...picks.flatMap((p) => p.items), ...points.flatMap((p) => p.items)];
  const allGaps = picks.flatMap((p) => p.gaps);
  const calls = picks.reduce((n, p) => n + p.llmCalls, 0);

  async function downloadExcel() {
    if (allItems.length === 0) return;
    const res = await fetch("/api/export", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        items: picks.flatMap((p) =>
          p.items.map((it) => ({
            ...it,
            // どの範囲から出た行かを残す。積み上げたあとで二重を見分ける手がかりになる。
            location: it.location ? `${it.location}／${p.label}` : p.label,
          })),
        ),
        gaps: allGaps,
      }),
    });
    if (!res.ok) {
      setError("Excel の生成に失敗しました");
      return;
    }
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(file?.name ?? "GOPIPE").replace(/\.pdf$/i, "")}_指して拾う.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <main className="mx-auto w-full max-w-[1200px] px-5 pb-24">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] py-6">
        <div>
          <p className="m-0 text-[12px] font-bold tracking-[0.3em] text-[var(--cyan)]">GOPIPE</p>
          <p className="m-0 text-[20px] font-black">
            {project ? `指して拾い足す — ${project.title || project.fileName}` : `指して拾う — ${orgName}`}
          </p>
        </div>
        <div className="text-right text-[12.5px]">
          {project && (
            <a
              href={`/app/projects/${project.id}`}
              className="mr-3 font-bold text-[var(--cyan)] hover:underline"
            >
              物件の明細へ戻る
            </a>
          )}
          <a href="/app" className="font-bold text-[var(--cyan)] hover:underline">
            1枚まるごとの拾い出しへ
          </a>
          {added > 0 && (
            <span className="ml-3 rounded bg-[rgba(31,157,85,0.18)] px-2 py-1 font-bold text-[#3ddc84]">
              この物件に {added} 行 足しました
            </span>
          )}
        </div>
      </header>

      <section className="py-6">
        <p className="mt-0 mb-5 text-[14px] text-[var(--mut)]">
          {project
            ? "1回まるごと拾ったあとの、拾い足しです。囲んだ範囲・指した色や物から出た行は、この物件の明細にそのまま足されます。"
            : ""}
          図面を開いて、拾いたいところを<b className="text-[var(--ink)]">囲んで</b>ください。囲んだ範囲だけを拾います。
          1枚を丸ごと読ませると、大判ほど字が潰れ、断面図や別の階が混ざります。範囲を指すと、その両方が消えます。
        </p>
        {/* 境目をまたぐダクトは、塗りが切れて長さが解けなくなる（実測: 同じ範囲を左右に
            割ると、ダクトの行が 23行→7行 に落ちた）。囲み方の注意として先に出す。 */}
        <p className="mt-0 mb-5 text-[13px] text-[var(--mut)]">
          ダクトの延長も測りますが、<b className="text-[var(--ink)]">囲みの線でダクトを切らないでください</b>。
          途中で切れたダクトは長さが出せません。系統のまとまりごとに、少し広めに囲むのが確実です。
        </p>

        <div className="flex flex-wrap items-center gap-3 rounded-[13px] border border-[var(--line)] bg-[var(--panel)] p-4">
          {project ? (
            <span className="text-[14px] font-bold">📄 {project.fileName || "この物件の図面"}</span>
          ) : (
          <label className="cursor-pointer rounded-[10px] border border-dashed border-[var(--cyan)] px-4 py-2 text-[14px] font-bold whitespace-nowrap text-[var(--cyan)] hover:bg-[rgba(86,204,242,0.08)]">
            {file ? `📄 ${file.name}` : "図面を選ぶ"}
            <input
              type="file"
              accept="application/pdf"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="hidden"
            />
          </label>
          )}
          {!project && (
            <button
              onClick={upload}
              disabled={!file || busy !== ""}
              className="rounded-[10px] bg-[var(--cyan)] px-5 py-2 text-[14px] font-black text-[#04121f] disabled:opacity-40"
            >
              開く
            </button>
          )}
          {pageCount > 1 && img && (
            <span className="flex items-center gap-2 text-[13px]">
              <button
                onClick={() => loadPage(path, Math.max(1, page - 1))}
                disabled={page <= 1 || busy !== ""}
                className="rounded-[8px] border border-[var(--line)] px-3 py-1 font-bold disabled:opacity-30"
              >
                ←
              </button>
              {page} / {pageCount} ページ
              <button
                onClick={() => loadPage(path, Math.min(pageCount, page + 1))}
                disabled={page >= pageCount || busy !== ""}
                className="rounded-[8px] border border-[var(--line)] px-3 py-1 font-bold disabled:opacity-30"
              >
                →
              </button>
            </span>
          )}
          <label className="flex items-center gap-2 text-[13px] text-[var(--mut)]">
            縮尺 1/
            <input
              value={scaleDenom}
              onChange={(e) => setScaleDenom(e.target.value.replace(/[^0-9.]/g, ""))}
              placeholder="図面から測る"
              className="w-[92px] rounded-[8px] border border-[var(--line)] bg-[var(--navy2)] px-2 py-1.5 text-[13px] focus:border-[var(--cyan)] focus:outline-none"
            />
          </label>
          <label className="ml-auto flex cursor-pointer items-center gap-2 text-[13px] text-[var(--mut)]">
            <input
              type="checkbox"
              checked={noLlm}
              onChange={(e) => setNoLlm(e.target.checked)}
              className="h-4 w-4 accent-[var(--cyan)]"
            />
            画像認識を使わない（CADのPDF向け・毎回同じ数が出ます）
          </label>
          {!pageHasText && (
            <p className="m-0 basis-full text-[12.5px] text-[#ffab33]">
              この図面には文字が入っていません（紙をスキャンした図面です）。印字だけでは拾えないので、
              AIが画像から読み取ります。CADから書き出したPDFに比べて精度が落ち、読ませ方によって
              数量が変わることがあります。同じ図面のCAD版があれば、そちらをお使いください。
            </p>
          )}
        </div>

        {/* 道具。範囲は「囲む」、ほかは「1回クリックする」。 */}
        <div className="mt-3 flex flex-wrap gap-2">
          {([
            ["range", "範囲を囲む", "囲んだ中だけを拾う"],
            ["color", "色を指す", "同じ色のものを集める"],
            ["duct", "ダクトを指す", "その1本の延長と幅を測る"],
            ["pipe", "配管を指す", "線をたどって延長・曲がり・分岐"],
            ["symbol", "記号を指す", "同じ形が何個あるか数える"],
          ] as [Tool, string, string][]).map(([t, name, hint]) => (
            <button
              key={t}
              onClick={() => { setTool(t); setDrag(null); }}
              className={
                "rounded-[10px] border px-4 py-2 text-left text-[13px] " +
                (tool === t
                  ? "border-[var(--cyan)] bg-[rgba(86,204,242,0.10)] text-[var(--ink)]"
                  : "border-[var(--line)] text-[var(--mut)] hover:border-[var(--cyan)]")
              }
            >
              <span className="block font-black">{name}</span>
              <span className="block text-[11.5px] opacity-80">{hint}</span>
            </button>
          ))}
        </div>

        {error && (
          <p className="mt-4 rounded-[11px] border border-[rgba(215,38,30,0.35)] bg-[rgba(215,38,30,0.08)] px-4 py-3 text-[14px] text-[#f2c7c4]">
            {error}
          </p>
        )}
        {busy && <p className="mt-4 text-[14px] text-[var(--cyan)]">{busyLabel}</p>}

        {img && saved.length > 0 && (
          <section className="mt-5 rounded-[12px] border border-[var(--cyan)] bg-[rgba(86,204,242,0.06)] p-4">
            <p className="m-0 mb-2 text-[14px] font-black text-[var(--cyan)]">
              この様式の図面で覚えていること（{saved.length} 件）
            </p>
            <div className="flex flex-wrap gap-2">
              {saved.filter((v) => v.kind === "region").map((v) => (
                <button
                  key={v.ref}
                  disabled={busy !== ""}
                  onClick={() => {
                    const b = v.payload as { page: number; x0: number; y0: number; x1: number; y1: number };
                    setTool("range");
                    setDrag({ x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1 });
                    if (Number(b.page) !== page) loadPage(path, Number(b.page) || 1);
                  }}
                  className="rounded-[9px] border border-[var(--cyan)] px-3 py-1.5 text-[12.5px] font-bold text-[var(--cyan)] disabled:opacity-40"
                >
                  前回の範囲を出す{v.payload?.label ? `（${v.payload.label}）` : ""}
                </button>
              ))}
              {saved.filter((v) => v.kind === "color").map((v) => (
                <span
                  key={v.ref}
                  className="inline-flex items-center gap-1.5 rounded-[9px] border border-[var(--line)] px-3 py-1.5 text-[12.5px]"
                >
                  <span
                    className="inline-block h-3 w-3 rounded-sm border border-[var(--line)]"
                    style={{ background: String(v.payload?.hex ?? "#fff") }}
                  />
                  <b>{String(v.payload?.name ?? "")}</b>
                  <span className="text-[var(--mut)]">
                    {v.payload?.action === "measure" ? "長さを測る"
                      : v.payload?.action === "skip" ? "拾わない" : "数える"}
                  </span>
                </span>
              ))}
            </div>
            <p className="mt-2 mb-0 text-[12px] text-[var(--mut)]">
              色の指示は、次からの拾い出しに自動で効きます（結果に「覚えた色の指示で◯行」と出ます）。
            </p>
          </section>
        )}

        {img && (
          <div className="mt-5">
            <div
              ref={boxRef}
              className="relative w-full cursor-crosshair select-none overflow-hidden rounded-[10px] border border-[var(--line)] bg-white"
              onMouseDown={(e) => {
                if (tool !== "range") return;      // 点を指す道具では囲まない
                const p = rel(e);
                setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
              }}
              onMouseMove={(e) => {
                if (tool !== "range" || !drag) return;
                if (e.buttons !== 1) return;
                const p = rel(e);
                setDrag((d) => (d ? { ...d, x1: p.x, y1: p.y } : d));
              }}
              onClick={(e) => {
                if (tool === "range" || busy) return;
                pickPoint(rel(e));
              }}
            >
              {/* 図面は画像で出す。ブラウザにPDFを描かせると、見えている絵と
                  切り出す元がずれて、囲んだ場所と拾う場所が食い違う。 */}
              <img src={img} alt="図面" className="block w-full" draggable={false} />
              {picks
                .filter((p) => p.page === page)
                .map((p) => (
                  <div
                    key={p.id}
                    className="pointer-events-none absolute border-2 border-[#1f9d55] bg-[rgba(31,157,85,0.10)]"
                    style={{
                      left: `${p.box.x0 * 100}%`, top: `${p.box.y0 * 100}%`,
                      width: `${(p.box.x1 - p.box.x0) * 100}%`,
                      height: `${(p.box.y1 - p.box.y0) * 100}%`,
                    }}
                  >
                    <span className="absolute -top-5 left-0 rounded bg-[#1f9d55] px-1.5 py-0.5 text-[10px] font-bold text-white">
                      {p.items.length}件
                    </span>
                  </div>
                ))}
              {points
                .filter((p) => p.page === page)
                .map((p) => (
                  <span
                    key={p.id}
                    className="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[#7b3fe4] bg-[rgba(123,63,228,0.18)]"
                    style={{ left: `${p.at.x * 100}%`, top: `${p.at.y * 100}%`, width: 16, height: 16 }}
                  />
                ))}
              {drag && (
                <div
                  className="pointer-events-none absolute border-2 border-dashed border-[var(--orange)] bg-[rgba(255,171,51,0.12)]"
                  style={{
                    left: `${Math.min(drag.x0, drag.x1) * 100}%`,
                    top: `${Math.min(drag.y0, drag.y1) * 100}%`,
                    width: `${Math.abs(drag.x1 - drag.x0) * 100}%`,
                    height: `${Math.abs(drag.y1 - drag.y0) * 100}%`,
                  }}
                />
              )}
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-3">
              {tool !== "range" && (
                <span className="text-[13.5px] font-bold text-[#a06bff]">
                  図面の
                  {tool === "color" ? "線" : tool === "duct" ? "ダクトの帯の内側"
                    : tool === "pipe" ? "配管の線の上" : "記号"}
                  をクリックしてください
                </span>
              )}
              <button
                onClick={runRegion}
                disabled={!drag || busy !== "" || tool !== "range"}
                className="rounded-[11px] bg-gradient-to-b from-[#ffab33] to-[var(--orange)] px-6 py-3 text-[15px] font-black text-[#241200] disabled:opacity-40"
              >
                この範囲を拾う
              </button>
              {drag && (
                <button onClick={() => setDrag(null)} className="text-[13px] text-[var(--mut)] underline">
                  囲みを消す
                </button>
              )}
              <span className="text-[13px] text-[var(--mut)]">
                拾った範囲 {picks.length} か所／明細 {allItems.length} 件／画像認識{" "}
                {calls === 0 ? "使っていません" : `${calls} 回`}
              </span>
              {allItems.length > 0 && (
                <button
                  onClick={downloadExcel}
                  className="ml-auto rounded-[11px] border border-[var(--cyan)] px-5 py-2.5 text-[14px] font-bold text-[var(--cyan)] hover:bg-[rgba(86,204,242,0.08)]"
                >
                  Excel で書き出す
                </button>
              )}
            </div>
          </div>
        )}

        {points.length > 0 && (
          <section className="mt-6 rounded-[12px] border border-[#7b3fe4] bg-[rgba(123,63,228,0.06)] p-4">
            <p className="m-0 mb-3 text-[14px] font-black text-[#a06bff]">
              指して拾ったもの（{points.length} 件）
            </p>
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {points.map((p) => {
                const hex = typeof p.detail?.hex === "string" ? p.detail.hex : "";
                return (
                  <li key={p.id} className="border-t border-[var(--line)] pt-3 first:border-t-0 first:pt-0">
                    <p className="m-0 text-[14px]">
                      <span className="mr-2 rounded bg-[rgba(123,63,228,0.18)] px-2 py-0.5 text-[11.5px] font-bold text-[#a06bff]">
                        {p.tool === "color" ? "色" : p.tool === "duct" ? "ダクト"
                          : p.tool === "pipe" ? "配管" : "記号"}
                      </span>
                      {hex && (
                        <span
                          className="mr-2 inline-block h-3 w-3 rounded-sm border border-[var(--line)] align-middle"
                          style={{ background: hex }}
                        />
                      )}
                      <b>{p.label || "—"}</b>
                      <span className="ml-2 text-[12.5px] text-[var(--mut)]">p{p.page}</span>
                    </p>
                    {p.note && <p className="m-0 mt-0.5 text-[12.5px] text-[var(--mut)]">{p.note}</p>}
                    {p.tool === "color" && hex && (
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        {/* 色の意味は会社ごと図面ごとにしか決まらない。当てずに聞く。
                            名前だけでは数量にならないので、拾い方も一緒に決める。 */}
                        <input
                          value={rule[hex]?.name ?? meaning[hex] ?? ""}
                          onChange={(e) =>
                            setRule((c) => ({
                              ...c,
                              [hex]: { action: c[hex]?.action ?? "count", unit: c[hex]?.unit ?? "", name: e.target.value },
                            }))
                          }
                          placeholder="この色は何ですか（例: 還気ダクト・既存流用）"
                          className="min-w-0 flex-1 rounded-[8px] border border-[var(--line)] bg-[var(--navy2)] px-3 py-1.5 text-[13px] focus:border-[var(--cyan)] focus:outline-none"
                        />
                        <select
                          value={rule[hex]?.action ?? "count"}
                          onChange={(e) =>
                            setRule((c) => ({
                              ...c,
                              [hex]: { name: c[hex]?.name ?? (meaning[hex] ?? ""), unit: c[hex]?.unit ?? "", action: e.target.value },
                            }))
                          }
                          className="rounded-[8px] border border-[var(--line)] bg-[var(--navy2)] px-2 py-1.5 text-[12.5px]"
                        >
                          <option value="count">数える（個）</option>
                          <option value="measure">長さを測る（m）</option>
                          <option value="skip">拾わない</option>
                        </select>
                        <button
                          onClick={async () => {
                            const r = rule[hex] ?? { name: meaning[hex] ?? "", action: "count", unit: "" };
                            if (!r.name.trim() && r.action !== "skip") return;
                            const ok = await remember("color", {
                              hex, name: r.name.trim() || "拾わない色", action: r.action, unit: r.unit,
                            });
                            if (ok) {
                              // 色の呼び名は既存の辞書にも残す（拾い出しの備考に出る）
                              if (r.name.trim()) {
                                fetch("/api/color-meanings", {
                                  method: "POST",
                                  headers: { "content-type": "application/json" },
                                  body: JSON.stringify({ color: hex, meaning: r.name.trim() }),
                                }).catch(() => {});
                              }
                              setPoints((cur) =>
                                cur.map((q) =>
                                  q.id === p.id ? { ...q, note: "覚えました。次の図面から効きます" } : q,
                                ),
                              );
                            }
                          }}
                          className="rounded-[8px] border border-[var(--cyan)] px-3 py-1.5 text-[12.5px] font-bold text-[var(--cyan)]"
                        >
                          覚えさせる
                        </button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        {picks.map((p) => (
          <section key={p.id} className="mt-6 rounded-[12px] border border-[var(--line)] bg-[var(--panel)] p-4">
            <p className="m-0 mb-2 text-[14px] font-black">
              {p.label} — {p.items.length} 件
              {p.llmCalls === 0 && <span className="ml-2 text-[12px] font-bold text-[var(--cyan)]">画像認識なし</span>}
            </p>
            {p.items.length === 0 ? (
              <p className="m-0 text-[13px] text-[#ffab33]">
                {p.reason || "この範囲からは拾えませんでした"}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[620px] border-collapse text-[13px]">
                  <thead>
                    <tr className="text-left text-[var(--mut)]">
                      <th className="py-1 pr-3 font-bold">名称</th>
                      <th className="py-1 pr-3 font-bold">仕様</th>
                      <th className="py-1 pr-3 text-right font-bold">数量</th>
                      <th className="py-1 pr-3 font-bold">単位</th>
                      <th className="py-1 font-bold">出所</th>
                    </tr>
                  </thead>
                  <tbody>
                    {p.items.slice(0, 40).map((it, i) => (
                      <tr key={i} className="border-t border-[var(--line)]">
                        <td className="py-1 pr-3">{it.name}</td>
                        <td className="py-1 pr-3 text-[var(--mut)]">{it.spec ?? ""}</td>
                        <td className="py-1 pr-3 text-right">{it.quantity}</td>
                        <td className="py-1 pr-3">{it.unit}</td>
                        <td className="py-1 text-[var(--mut)]">{it.qty_basis ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {p.items.length > 40 && (
                  <p className="mt-2 mb-0 text-[12px] text-[var(--mut)]">
                    ほか {p.items.length - 40} 件（Excel には全部入ります）
                  </p>
                )}
              </div>
            )}
            {p.measures && p.measures.by_color.length > 0 && (
              <div className="mt-3 rounded-[10px] border border-[var(--line)] p-3">
                <p className="m-0 mb-1 text-[13px] font-bold">
                  この範囲の寸法（線の長さ 合計 {p.measures.total_m} m）
                  {p.measures.scale && (
                    <span className="ml-2 text-[11.5px] font-normal text-[var(--mut)]">
                      {p.measures.scale.how}
                    </span>
                  )}
                </p>
                <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-[12.5px]">
                  {p.measures.by_color.map((c) => (
                    <li key={c.hex} className="flex items-center gap-1.5">
                      <span
                        className="inline-block h-3 w-3 rounded-sm border border-[var(--line)]"
                        style={{ background: c.hex }}
                      />
                      <b>{c.length_m} m</b>
                      <span className="text-[var(--mut)]">（{c.shapes}本 {c.hex}）</span>
                    </li>
                  ))}
                </ul>
                <p className="m-0 mt-1 text-[11.5px] text-[var(--mut)]">
                  何の線かは機械には分かりません。色を指して意味を覚えさせると、次から名前が付きます。
                </p>
              </div>
            )}
            <div className="mt-2">
              <button
                onClick={() =>
                  remember("region", { page: p.page, ...p.box, label: p.label })
                }
                className="rounded-[8px] border border-[var(--cyan)] px-3 py-1.5 text-[12.5px] font-bold text-[var(--cyan)]"
              >
                この範囲を覚えさせる
              </button>
            </div>
            {p.gaps.length > 0 && (
              <ul className="mt-3 mb-0 list-none space-y-1 p-0 text-[12.5px] text-[#ffab33]">
                {p.gaps.map((g, i) => (
                  <li key={i}>
                    拾えていない：{g.item}（{g.reason}）
                  </li>
                ))}
              </ul>
            )}
          </section>
        ))}
      </section>
    </main>
  );
}
