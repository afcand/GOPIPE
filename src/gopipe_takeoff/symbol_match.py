"""記号テンプレートマッチング（CV・段階2）— numpy NCC ベースのローカル専用ツール。

凡例の記号図形（テンプレート）を図面画像上で正規化相互相関(NCC)により検出して
数える。テキスト層が無いスキャン図でも個数モノを数える狙い（段階1=legend_count
のテキスト版に対する、画像版）。

依存は numpy + Pillow + PyMuPDF のみ（opencv 不要）。
  ・opencv は重く Vercel サーバレスのサイズ上限に響くため API には載せない。
  ・本モジュールはローカル/バッチ前処理として使う想定。

実測（2026-08-19 ハルキ実図面 資料③・A3 200dpi スキャン 3310x2340）:
  ・FFT 版で全面走査が **約6秒**（API 呼び出し 0 回）。
  ・E2#20（□の中に⊗）… 9個検出 / 実際 11個。取りこぼした2個はダクトの線に
    重なって描かれており NCC スコアが落ちる。
  ・排煙口（□の中に斜線）… 2個検出 / 実際 6個。300角と350角で作図が違い、
    単一テンプレートでは足りない（多スケールでも不足）。
  → **まだパイプラインには載せない。** 数え落としたまま「数えました」と出すのが
    一番危ないため、テンプレートの採り方（凡例から採る）と受け入れ条件を
    詰めてから接続する。現状は scripts/run_symbol_count.py の手動検証用。

なぜ LLM でなく CV で数えるのか:
  同じ図面を2回かけると LLM の計数は動く（実測: 排煙口 1個→2個、VD 2個→1個、
  吹出口 1個→2個）。数えるという仕事は決定的アルゴリズムの領分で、
  LLM は「その記号が何か」を読む側に置くのが正しい。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:  # opencv は任意。あれば高速・大判図面対応、無ければ numpy フォールバック。
    import cv2  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None


@dataclass
class Match:
    """検出位置（テンプレート左上の座標）とスコア(NCC, -1..1)。"""

    x: int
    y: int
    score: float


def _to_gray(img) -> np.ndarray:
    """PIL.Image / ndarray → 2D float グレースケール。"""
    arr = np.asarray(img, dtype=np.float64)
    if arr.ndim == 3:
        arr = arr[..., :3].mean(axis=2)
    return arr


def _ncc_fft(img: np.ndarray, tpl: np.ndarray) -> np.ndarray:
    """FFT + 積分画像による NCC。opencv 無しで A3 全面を扱えるようにするため。

    素朴なスライディングウィンドウ版は、A3 200dpi (3310x2340) に 55x55 の記号を
    当てると中間配列が 7.7M x 3025 要素になり、実図面ではメモリで落ちる
    （＝この関数が無いと、このモジュールは実務では一度も動かせない）。
    相関項を FFT、局所平均・分散を積分画像で出せば数秒で終わる。
    """
    th, tw = tpl.shape
    H, W = img.shape
    t0 = tpl - tpl.mean()
    tss = float((t0 ** 2).sum())
    oh, ow = H - th + 1, W - tw + 1
    if tss <= 0:
        return np.zeros((oh, ow))
    fh, fw = H + th - 1, W + tw - 1
    spec = np.fft.rfft2(img, s=(fh, fw)) * np.fft.rfft2(t0[::-1, ::-1], s=(fh, fw))
    corr = np.fft.irfft2(spec, s=(fh, fw))[th - 1:th - 1 + oh, tw - 1:tw - 1 + ow]
    ii = np.pad(img, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    ii2 = np.pad(img ** 2, ((1, 0), (1, 0))).cumsum(0).cumsum(1)

    def _win(a):
        return a[th:, tw:] - a[:-th, tw:] - a[th:, :-tw] + a[:-th, :-tw]

    s1, s2 = _win(ii), _win(ii2)
    var = s2 - s1 * s1 / (th * tw)
    den = np.sqrt(np.maximum(var, 0.0) * tss)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 1e-9, corr / den, 0.0)


def ncc_map(image, template) -> np.ndarray:
    """全位置の正規化相互相関(-1..1)マップ。image/template は2D相当。"""
    img = _to_gray(image)
    tpl = _to_gray(template)
    th, tw = tpl.shape
    if img.shape[0] < th or img.shape[1] < tw:
        return np.empty((0, 0))
    if cv2 is not None:  # あれば最速（TM_CCOEFF_NORMED = NCC 相当）
        res = cv2.matchTemplate(img.astype(np.float32), tpl.astype(np.float32), cv2.TM_CCOEFF_NORMED)
        return np.nan_to_num(res.astype(np.float64), nan=0.0)
    return _ncc_fft(img, tpl)


def find_matches(image, template, *, threshold: float = 0.8, min_distance: int | None = None) -> list[Match]:
    """NCC>=threshold のピークを非極大抑制(NMS)して返す（重複検出を1つに）。"""
    tpl = _to_gray(template)
    th, tw = tpl.shape
    ncc = ncc_map(image, tpl)
    if ncc.size == 0:
        return []
    md = min_distance if min_distance is not None else max(1, max(th, tw) // 2)
    cand = np.argwhere(ncc >= threshold)
    if cand.size == 0:
        return []
    scores = ncc[cand[:, 0], cand[:, 1]]
    occupied = np.zeros(ncc.shape, dtype=bool)
    matches: list[Match] = []
    for idx in np.argsort(-scores):
        y, x = int(cand[idx, 0]), int(cand[idx, 1])
        if occupied[y, x]:
            continue
        matches.append(Match(x=x, y=y, score=float(ncc[y, x])))
        occupied[max(0, y - md):y + md + 1, max(0, x - md):x + md + 1] = True
    return matches


def _scaled(template: np.ndarray, s: float) -> np.ndarray:
    if s == 1.0:
        return template
    from PIL import Image

    im = Image.fromarray(template.astype(np.uint8))
    return _to_gray(im.resize((max(4, int(im.width * s)), max(4, int(im.height * s)))))


def find_matches_multiscale(
    image, template, *, threshold: float = 0.75,
    scales: tuple[float, ...] = (0.8, 0.9, 1.0, 1.1, 1.2),
) -> list[Match]:
    """複数の大きさで探し、重なる検出を1つに寄せる（300角/350角の作図差を吸収）。"""
    base = _to_gray(template)
    found: list[tuple[float, float, float, int, int]] = []
    for s in scales:
        tpl = _scaled(base, s)
        h, w = tpl.shape
        for m in find_matches(image, tpl, threshold=threshold):
            found.append((m.x + w / 2, m.y + h / 2, m.score, h, w))
    found.sort(key=lambda p: -p[2])
    keep: list[tuple[float, float, float, int, int]] = []
    for p in found:
        r = 0.6 * max(p[3], p[4])
        if all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 > r * r for q in keep):
            keep.append(p)
    return [Match(x=int(p[0] - p[4] / 2), y=int(p[1] - p[3] / 2), score=p[2]) for p in keep]


def count_matches(image, template, *, threshold: float = 0.8, scales: tuple[float, ...] = (1.0,)) -> int:
    """記号の個数。複数スケールを試し、最も多く一致したスケールの件数を返す。"""
    best = 0
    base = _to_gray(template)
    for s in scales:
        best = max(best, len(find_matches(image, _scaled(base, s), threshold=threshold)))
    return best


def count_stable_fast(
    image, template, *, thresholds: tuple[float, ...] = (0.80, 0.75, 0.70, 0.65),
    scales: tuple[float, ...] = (0.8, 0.9, 1.0, 1.1, 1.2),
) -> tuple[int | None, dict[float, int]]:
    """count_stable と同じ「踊り場」判定を、NCC計算1周で済ませる高速版。

    count_stable はしきい値ごとに全スケールを回す（4×5=20回のNCC全面照合）ため、
    A3全面では1テンプレート20秒級になりサーバレスの実行時間を食い潰す。
    ここでは最小しきい値で一度だけピークを取り、しきい値は取ったピークの
    スコア足切りとして適用する。NMSが1回になる分だけ厳密には近似だが、
    離れて配置される記号スタンプの用途では踊り場判定は変わらない。
    """
    peaks = find_matches_multiscale(image, template, threshold=min(thresholds), scales=scales)
    counts = {t: sum(1 for p in peaks if p.score >= t) for t in thresholds}
    vals = list(counts.values())
    for i in range(len(vals) - 1):
        if vals[i] == vals[i + 1] and vals[i] > 0:
            return vals[i], counts
    return None, counts


def count_stable(
    image, template, *, thresholds: tuple[float, ...] = (0.80, 0.75, 0.70, 0.65),
    scales: tuple[float, ...] = (0.8, 0.9, 1.0, 1.1, 1.2),
) -> tuple[int | None, dict[float, int]]:
    """しきい値を動かしても件数が変わらない「踊り場」があるときだけ個数を返す。

    しきい値を少し動かすだけで件数が動く記号は、数えられていない合図。
    そこで数を出すと、**もっともらしいが再現しない数字**になる（積算で一番危ない）。
    踊り場が無ければ None を返し、「数えられなかった」と正直に伝える。

    Returns: (確定した個数 or None, しきい値ごとの件数)
    """
    counts = {
        t: len(find_matches_multiscale(image, template, threshold=t, scales=scales))
        for t in thresholds
    }
    vals = list(counts.values())
    for i in range(len(vals) - 1):
        if vals[i] == vals[i + 1] and vals[i] > 0:
            return vals[i], counts
    return None, counts


def page_image(pdf_path: str, *, page_index: int = 0, dpi: int = 150) -> np.ndarray:
    """PDFの1ページを画像(ndarray, H×W×C)にレンダリング（スキャン図のCV入力用）。"""
    import fitz

    doc = fitz.open(pdf_path)
    pix = doc[page_index].get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    doc.close()
    return arr
