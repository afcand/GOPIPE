/**
 * GOPIPE エンジン API（Vercel 上の FastAPI）への窓口。
 *
 * GOPIPE_API_KEY は「自社の Claude キーを消費する操作」と「顧客DBへの書き込み」を
 * 開けるための鍵。ブラウザには絶対に出さず、この Next.js のサーバ側だけが持つ。
 * 公開デモ（provider=mock）は鍵なしで通るので、鍵を付けずに転送する。
 */
export const API_BASE = process.env.GOPIPE_API_BASE ?? "https://gopipe.vercel.app";

/** 鍵が要る操作かどうか。要るのにサーバに鍵が無ければ呼ばない（黙って落とさない）。 */
export function serverKey(): string | null {
  return process.env.GOPIPE_API_KEY || null;
}

export type TakeoffItem = {
  category: string | null;
  name: string;
  spec: string | null;
  location: string | null;
  quantity: number;
  unit: string;
  confidence: number;
  /** 整合チェックの指摘（二重計上の疑い・数量0・単位×カテゴリ不一致など） */
  checks?: string[];
  /** AIが図面から実際に読んだ生の名称。学習の鍵はこちら（表示名ではない） */
  raw_name?: string | null;
  /** 機器表を採用した行の「図面側の読み」。食い違ったときだけ入る */
  qty_vision?: number | null;
  /** 何ページ目か。🔴の行を図面で確認するときに要る */
  page?: number;
  /** 数量の出所（table/count/measure/estimate/none）。備考の書き分けに要る */
  qty_basis?: string | null;
  /** 記号テンプレート照合が数えた個数 */
  qty_cv?: number | null;
  /** 抽出の由来（reconciled / cv_count / suppressed_hit など） */
  source?: string | null;
};

/** 🔴＝要確認。Streamlit 版と同じ 0.7 / 0.85 の線を守る。 */
export function confidenceLevel(c: number): "low" | "mid" | "high" {
  if (c < 0.7) return "low";
  if (c < 0.85) return "mid";
  return "high";
}

/**
 * 要確認かどうか。確度が高くても、二重計上のような指摘が付いた行は必ず人に見せる。
 * 数量が倍になる見積を黙って通さないための線引き。
 */
export function needsReview(it: TakeoffItem): boolean {
  return confidenceLevel(it.confidence) === "low" || (it.checks?.length ?? 0) > 0;
}
