from __future__ import annotations

import logging
import math
import os
from pathlib import Path

from .models import Drawing, DrawingPage, TextLine, Tile

logger = logging.getLogger("gopipe.pdf_loader")

DEFAULT_DPI = 200
# Claude の image size 上限 (5 MB) に収めるための画像 byte 上限。
MAX_IMAGE_BYTES = 4_500_000
MIN_DPI = 72
# ベクター(CAD)図をタイル化するときの既定 DPI。埋め込み画像の実解像度が
# 分かる場合はそちらを使う（下の native_dpi）。
DEFAULT_TILE_DPI = 300

# 🔴 ここが「図面が読めない」の真因だった（2026-08-19 実測）。
# Anthropic の画像仕様では、長辺がこの画素数を超える画像は API 側で自動的に
# 縮小されてからモデルに渡る。つまりモデルが実際に見る解像度は
#   実効DPI = 送信DPI × min(1, VISION_MAX_EDGE / 長辺画素)
# で決まり、**何 dpi でレンダリングしたか**ではない。
# A3(420mm) を丸ごと送ると、何 dpi で送っても実効 1568px/16.54inch ≒ 95dpi にしかならず、
# 表の小さい文字は原理的に潰れる。紙を分割して送る以外に手が無い。
VISION_MAX_EDGE = 1568

# 表・記号の小さい文字が読める実効解像度の下限。
# 実測(2026-08-19 ハルキ実図面 資料④): 実効112dpi では図面に印刷された
# 吹出口/吸込口表(12行22個)を 1 個も拾えず、存在しない記号まで出した。
# 同じ図面をタイル化して実効200dpi にすると 12行22個が完全一致・確度0.9 になった。
AUTO_TILE_MIN_DPI = 150
# 1 リクエストあたりの LLM 呼び出し数の上限（サーバレスの実行時間 300 秒を守るため）。
AUTO_TILE_MAX_CALLS = 9


def max_llm_calls() -> int:
    """1 リクエストで許す LLM 呼び出し数。

    🔴 既定の 9 は **サーバレスの 300 秒**を守るための数であって、読める解像度の
    ための数ではない。この予算をページ数で割るため、多ページの大判図では
    1ページ1タイル＝丸ごと送りに落ちる。実測(2026-09-08 A1×23枚)では
    **実効47dpi**まで落ちていた（2026-08-19 に表が読めなくなった 112dpi より更に低い）。
    ローカル実行やバッチには 300 秒の縛りが無いので、環境変数で上げられるようにする。
    """
    try:
        v = int(os.environ.get("GOPIPE_MAX_LLM_CALLS", "") or AUTO_TILE_MAX_CALLS)
    except ValueError:
        return AUTO_TILE_MAX_CALLS
    return max(1, min(v, 400))

# タイル同士の重なり（中心領域の外側に付ける「文脈用の余白」の割合）。
# 0 だと境界にまたがる記号が両側で半分になり、どちらのタイルからも数え落とす。
# 余白は見せるが数えさせない（下の _apply_core_focus で薄くし、プロンプトで
# 「はっきり見えている中心部分だけを出す」と指示する）。二重計上を防ぐため。
TILE_MARGIN = 0.08


def text_lines_of(page) -> list[TextLine]:
    """ページのテキスト層を、位置つきの行として取り出す（ベクターPDF向け）。

    位置はページ幅・高さを 1 とした比率で持つ。紙のサイズ（A1/A3）や
    レンダリング解像度が変わっても、同じ物差しで図枠を見分けられるようにするため。
    """
    out: list[TextLine] = []
    try:
        w, h = page.rect.width or 1.0, page.rect.height or 1.0
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                t = "".join(sp.get("text", "") for sp in line.get("spans", [])).strip()
                if not t:
                    continue
                b = line.get("bbox") or (0, 0, 0, 0)
                out.append(TextLine(text=t, x=b[0] / w, y=b[1] / h, x1=b[2] / w, y1=b[3] / h))
    except Exception:  # noqa: BLE001  位置が取れなくても拾い出し自体は続ける
        return []
    return out


def _encode(pix, *, jpeg: bool) -> bytes:
    """Pixmap をバイト列にする。

    🔴 スキャン図を PNG で送ると、A3 200dpi で 7.6MB になり byte 上限に当たって
    DPI が 112 まで自動降格していた（＝表が読めなくなっていた）。同じ画を JPEG に
    すると 2.05MB で収まり、降格が起きない。写真的な画（スキャン）は JPEG、
    線画（ベクター CAD）は PNG が正しい。
    """
    if jpeg:
        try:
            return pix.tobytes("jpeg", jpg_quality=92)
        except Exception:  # noqa: BLE001  古い PyMuPDF は jpeg 非対応のことがある
            pass
    return pix.tobytes("png")


def _enhance(data: bytes, *, jpeg: bool) -> bytes:
    """スキャン画像向けの軽い前処理（自動コントラスト＋鮮鋭化）。

    小さな数字・記号をAIが読みやすくする。失敗時や非対応環境では原画像を返す
    （PIL のみ・依存追加なし）。ベクター描画には適用しない（呼び出し側で判定）。
    """
    try:
        import io

        from PIL import Image, ImageEnhance, ImageFilter, ImageOps

        im = Image.open(io.BytesIO(data)).convert("RGB")
        im = ImageOps.autocontrast(im, cutoff=1)
        im = ImageEnhance.Contrast(im).enhance(1.3)
        im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=130, threshold=2))
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=92) if jpeg else im.save(out, format="PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return data


def _apply_core_focus(data: bytes, core: list[float], *, jpeg: bool, fade: float = 0.55) -> bytes:
    """タイルの「担当領域(core)」の外側＝重なり分を白へ寄せて薄くする。

    重なりは境界の部材を切らないために要る。しかし薄くしないと、隣のタイルと
    同じものを両方が出して二重計上になる。**言葉で「外は数えるな」と書くより、
    見た目で分かるほうが確実**なので、外周を薄くして中心をはっきりさせる。
    塗り潰さない（文脈は読める）。
    """
    if core == [0.0, 0.0, 1.0, 1.0]:
        return data
    try:
        import io

        from PIL import Image, ImageChops

        im = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = im.size
        white = Image.new("RGB", (w, h), (255, 255, 255))
        faded = ImageChops.blend(im, white, fade)
        x0, y0, x1, y1 = (
            int(core[0] * w), int(core[1] * h), int(core[2] * w), int(core[3] * h),
        )
        faded.paste(im.crop((x0, y0, x1, y1)), (x0, y0))
        out = io.BytesIO()
        faded.save(out, format="JPEG", quality=92) if jpeg else faded.save(out, format="PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return data


def _render_within_limit(
    page, *, dpi: int, clip=None, jpeg: bool = False
) -> tuple[bytes, int, int, int]:
    """指定 DPI でレンダリング（clip 指定で領域限定可）し、画像サイズ上限を
    超えたら DPI を下げて再試行する。

    Returns: (image_bytes, width, height, used_dpi)
    """
    import fitz  # PyMuPDF

    cur_dpi = dpi
    last_data: bytes | None = None
    last_w = last_h = 0
    while cur_dpi >= MIN_DPI:
        zoom = cur_dpi / 72.0
        kwargs = {"matrix": fitz.Matrix(zoom, zoom), "alpha": False}
        if clip is not None:
            kwargs["clip"] = clip
        pix = page.get_pixmap(**kwargs)
        data = _encode(pix, jpeg=jpeg)
        last_data, last_w, last_h = data, pix.width, pix.height
        if len(data) <= MAX_IMAGE_BYTES:
            return data, pix.width, pix.height, cur_dpi
        logger.warning(
            "page %d image %.1f MB > %.1f MB at %ddpi, lowering",
            page.number + 1, len(data) / 1_048_576, MAX_IMAGE_BYTES / 1_048_576, cur_dpi,
        )
        new_dpi = max(MIN_DPI, int(cur_dpi * 0.75))
        if new_dpi == cur_dpi:  # 既に MIN_DPI なら抜ける
            break
        cur_dpi = new_dpi
    return last_data or b"", last_w, last_h, cur_dpi


def effective_dpi(width_px: int, height_px: int, used_dpi: int) -> float:
    """モデルに届く時点の実効 DPI。API 側の長辺縮小（VISION_MAX_EDGE）を織り込む。

    「300dpi でレンダリングしたから読めるはず」は誤り。長辺が 1568px を超えた
    時点で縮小され、紙が大きいほど実効解像度は落ちる。
    """
    long_edge = max(width_px, height_px)
    if long_edge <= 0:
        return 0.0
    return used_dpi * min(1.0, VISION_MAX_EDGE / long_edge)


def native_dpi(page) -> float | None:
    """ページに貼られたスキャン画像の実解像度(dpi)。ベクター図なら None。

    スキャン原本より高い dpi でレンダリングしても情報は増えず、
    拡大→API側で縮小 の二重リサンプルでかえって字がぼやける。原寸を知って
    そこに合わせるのが一番くっきりする。
    """
    try:
        imgs = page.get_images(full=True)
        if not imgs:
            return None
        w_in = page.rect.width / 72.0
        if w_in <= 0:
            return None
        best = max(img[2] for img in imgs)  # img[2] = 画像の画素幅
        return best / w_in if best else None
    except Exception:  # noqa: BLE001
        return None


def auto_grid(used_dpi: int, *, tile_dpi: int = DEFAULT_TILE_DPI, page_count: int = 1) -> int:
    """（正方分割版・後方互換）縮小されたページを何分割すれば読めるかを返す。

    ページ形状が取れない場合のフォールバック。通常は plan_tiles を使う。
    """
    if used_dpi >= AUTO_TILE_MIN_DPI:
        return 1
    need = math.ceil(tile_dpi / max(used_dpi, 1))
    budget = math.isqrt(max(max_llm_calls() // max(page_count, 1), 1))
    return max(1, min(need, budget))


def plan_tiles(
    width_px: float,
    height_px: float,
    *,
    max_calls: int | None = None,
    page_count: int = 1,
) -> tuple[int, int]:
    """紙の縦横比に沿って (行数, 列数) を決める。正方分割にしない。

    各タイルの長辺が VISION_MAX_EDGE 以下になれば、API 側の再縮小がゼロになり
    レンダリング解像度がそのままモデルに届く。
    A3 横 200dpi (3310x2340px) なら 2行3列＝6回。3x3=9回より 3 回少なく、
    かつ各タイル 1103x1170px は 1568px 以下なので縮小されない
    （従来の 300dpi 正方タイルは 1655px あり、API 側で縮んでいた＝
    200dpi から 300dpi へ拡大した分が捨てられ、二重リサンプルでボケていた）。
    """
    max_calls = max_llm_calls() if max_calls is None else max_calls
    cols = max(1, math.ceil(width_px / VISION_MAX_EDGE))
    rows = max(1, math.ceil(height_px / VISION_MAX_EDGE))
    budget = max(1, max_calls // max(page_count, 1))
    while rows * cols > budget and (rows > 1 or cols > 1):
        if cols >= rows and cols > 1:
            cols -= 1
        elif rows > 1:
            rows -= 1
        else:
            break
    return rows, cols


def _render_tiles(
    page, *, rows: int, cols: int, dpi: int, enhance: bool = False, jpeg: bool = False,
    margin: float = TILE_MARGIN, page_w_px: float = 0.0, page_h_px: float = 0.0,
) -> list[Tile]:
    """ページを rows×cols に切り、各タイルを個別レンダリングする。

    各タイルは担当領域(core)の外側に margin 分の重なりを付けて描画し、
    重なり部分は薄くする（_apply_core_focus）。境界の部材を切らずに、
    かつ二重計上もしないための作り。
    """
    import fitz  # PyMuPDF

    rect = page.rect  # pt
    tw = rect.width / cols
    th = rect.height / rows
    mx, my = tw * margin, th * margin
    tiles: list[Tile] = []
    for row in range(rows):
        for col in range(cols):
            cx0, cy0 = rect.x0 + col * tw, rect.y0 + row * th
            cx1, cy1 = cx0 + tw, cy0 + th
            ex0, ey0 = max(rect.x0, cx0 - mx), max(rect.y0, cy0 - my)
            ex1, ey1 = min(rect.x1, cx1 + mx), min(rect.y1, cy1 + my)
            clip = fitz.Rect(ex0, ey0, ex1, ey1)
            data, w, h, used = _render_within_limit(page, dpi=dpi, clip=clip, jpeg=jpeg)
            if enhance:
                _e = _enhance(data, jpeg=jpeg)
                if len(_e) <= MAX_IMAGE_BYTES:
                    data = _e
            ew, eh = (ex1 - ex0) or 1, (ey1 - ey0) or 1
            core = [
                (cx0 - ex0) / ew, (cy0 - ey0) / eh, (cx1 - ex0) / ew, (cy1 - ey0) / eh,
            ]
            _f = _apply_core_focus(data, core, jpeg=jpeg)
            if len(_f) <= MAX_IMAGE_BYTES:
                data = _f
            logger.info(
                "page %d tile (r=%d/%d, c=%d/%d) %ddpi %dx%dpx %.0fKB (実効%.0fdpi)",
                page.number + 1, row, rows, col, cols, used, w, h, len(data) / 1024,
                effective_dpi(w, h, used),
            )
            # このタイルがページ画像のどこを覆うか。ページ画像は rect 全体を
            # page_scale で描いたものなので、pt から画素へ直せる。
            sx = page_w_px / max(rect.width, 1e-6)
            sy = page_h_px / max(rect.height, 1e-6)
            tiles.append(
                Tile(
                    image_png=data, row=row, col=col, rows=rows, cols=cols,
                    grid=max(rows, cols), width=w, height=h, core=core,
                    page_rect=[
                        (ex0 - rect.x0) * sx, (ey0 - rect.y0) * sy,
                        (ex1 - rect.x0) * sx, (ey1 - rect.y0) * sy,
                    ],
                )
            )
    return tiles


def load_pdf(
    path: str | Path,
    *,
    dpi: int = DEFAULT_DPI,
    grid: int = 1,
    tile_dpi: int | None = None,
    render: bool = True,
) -> Drawing:
    """PDF をページ単位でレンダリング + テキスト抽出して返す。

    render=False なら画像を作らない。ベクター図を印字だけで拾うときは画像が要らず、
    A1 を何枚も描くのは時間の無駄になる（実測: 16枚で約20秒）。低解像度の警告も、
    画像を送らない以上そもそも当てはまらないので出さない。

    grid > 1 のときは呼び出し側の明示指定として grid×grid に分割する。
    grid <= 1 のときは、**モデルに届く実効解像度**を見て自動でタイル分割する
    （紙が大きいほど API 側で縮小されるため、A3 は丸ごと送ると必ず潰れる）。
    """
    path = Path(path)
    pages: list[DrawingPage] = []

    try:
        import fitz  # PyMuPDF
    except Exception:
        fitz = None

    if fitz is not None and path.exists():
        doc = fitz.open(path)
        page_count = doc.page_count
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            is_scan = len(text.strip()) < 50  # テキスト層が薄い=スキャン画像とみなす
            tlines = [] if is_scan else text_lines_of(page)
            nat = native_dpi(page)
            if not render:
                pages.append(DrawingPage(
                    page=i, width=page.rect.width, height=page.rect.height,
                    text=text, text_lines=tlines,
                ))
                continue
            # スキャンは JPEG。PNG だと byte 上限に当たって DPI が自動降格する。
            data, w, h, used_dpi = _render_within_limit(page, dpi=dpi, jpeg=is_scan)
            if used_dpi != dpi:
                logger.info("page %d rendered at %d dpi (downscaled from %d)", i, used_dpi, dpi)
            raw = data  # 色の実測用に、前処理を掛ける前の画を取っておく
            if is_scan:
                _enh = _enhance(data, jpeg=True)
                if len(_enh) <= MAX_IMAGE_BYTES:
                    data = _enh
                    logger.info("page %d: scan detected → image enhanced (contrast+sharpen)", i)

            tiles: list[Tile] = []
            low_res: float | None = None
            rows = cols = grid
            # スキャンは原寸(native)に合わせる。原寸より上げても情報は増えず、
            # 拡大→API側で縮小 の二重リサンプルで字がぼやけるだけ。
            t_dpi = tile_dpi or (int(round(nat)) if nat else DEFAULT_TILE_DPI)
            t_dpi = max(MIN_DPI, min(t_dpi, 600))
            if grid <= 1:
                eff = effective_dpi(w, h, used_dpi)
                if eff < AUTO_TILE_MIN_DPI:
                    rows, cols = plan_tiles(
                        page.rect.width / 72 * t_dpi,
                        page.rect.height / 72 * t_dpi,
                        page_count=page_count,
                    )
                    if rows * cols > 1:
                        logger.info(
                            "page %d: 実効%.0fdpi（%dpx を長辺%dpx へ縮小されるため）"
                            "→ 自動でタイル分割 %d行×%d列 @%ddpi",
                            i, eff, max(w, h), VISION_MAX_EDGE, rows, cols, t_dpi,
                        )
                    else:
                        logger.warning(
                            "page %d: 実効%.0fdpi だがページ数が多く分割を見送り"
                            "（表の数量が読めない可能性が高い）", i, eff,
                        )
                        low_res = eff
                else:
                    rows = cols = 1
            if rows * cols > 1:
                tiles = _render_tiles(
                    page, rows=rows, cols=cols, dpi=t_dpi, enhance=is_scan, jpeg=is_scan,
                    page_w_px=w, page_h_px=h,
                )
            pages.append(
                DrawingPage(
                    page=i, width=w, height=h, text=text, image_png=data, tiles=tiles,
                    image_raw=raw, text_lines=tlines, low_res_dpi=low_res,
                )
            )
        doc.close()
    else:
        pages.append(DrawingPage(page=1, width=1654, height=1169, text=""))

    return Drawing(source_path=str(path), pages=pages)
