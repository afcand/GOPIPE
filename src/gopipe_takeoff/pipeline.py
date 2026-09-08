from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from utils import get_logger

from .classifier import classify
from .dictionary import TakeoffDictionary
from .excel_writer import write_excel
from .extractor import extract
from .frame_filter import detect as detect_frame
from .gap_report import Gap, find as find_gaps
from .legend_symbols import count_from_pdf as count_glyph_symbols
from .refrigerant import parse_size_table
from .locale import resolve as resolve_knowledge
from .marker import write_marker_pdf
from .models import TakeoffItem
from .pdf_loader import load_pdf
from .vector_takeoff import extract as extract_vector

logger = get_logger("gopipe.track_a")

DICTIONARY_PATH = Path(__file__).resolve().parents[2] / "prompts" / "dictionary.yaml"


@dataclass
class TakeoffResult:
    items: list[TakeoffItem]
    excel_path: Path
    marker_pdf_path: Path | None
    # 実際に走った LLM 呼び出し回数。自動タイル分割で毎回変わるため、
    # 呼び出し側が grid から逆算すると必ずズレる（＝料金の説明が嘘になる）。
    llm_calls: int = 0
    # 読み取れなかったページ・タイル。0件と「読めていない」を混同させないため、
    # 空リストでない限り必ず画面まで運ぶ。
    failures: list[str] = field(default_factory=list)
    # ベクター図の印字のうち、ラベルらしいのに型に載せられなかった行 (ページ, 原文)。
    # これも「読めていない」の一種なので黙って捨てない。
    unread_labels: list[tuple[int, str]] = field(default_factory=list)
    # 図枠・凡例・参照表として除外した文字の種類数。除外しすぎ／しなさすぎの検知用。
    frame_dropped: int = 0
    # この図面にあるのに、この経路では数えられなかったもの（記号もの・延長 等）。
    # 表に出ない部材は現場から見れば0個に見える。必ず画面・帳票まで運ぶ。
    gaps: list = field(default_factory=list)


class TakeoffPipeline:
    def __init__(self, *, dictionary_path: str | Path | None = None) -> None:
        self.dictionary = TakeoffDictionary.from_yaml(
            dictionary_path or resolve_knowledge("dictionary.yaml")
        )

    def run(
        self,
        input_pdf: str | Path,
        out_dir: str | Path,
        *,
        grid: int = 1,
        two_pass: bool = False,
        use_text_table: bool = True,
        use_vector_text: bool = True,
        use_llm: bool = True,
    ) -> TakeoffResult:
        """PDF → 拾い出し Excel + マーカー PDF を出力する。

        grid > 1 のときは PDF 各ページを grid×grid タイルに分割して LLM 抽出
        （API call 数 grid^2 倍、精度向上を狙う）。bbox 情報はタイル化時には
        棄てるため、マーカー PDF は出力されない。

        two_pass=True のとき:
          Pass1: 通常の拾い出し
          Pass2: 漏れ確認 (verification) パス — Pass1 の結果を見せた上で追加項目を抽出
          API call 数は +1/ページ（grid=1 時は計 2 call/ページ）。
        """
        input_pdf = Path(input_pdf)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        logger.info("loading PDF: %s (grid=%d, two_pass=%s, use_llm=%s)",
                    input_pdf, grid, two_pass, use_llm)
        drawing = load_pdf(input_pdf, grid=grid, render=use_llm)
        llm_calls = 0 if not use_llm else (
            sum(len(p.tiles) or 1 for p in drawing.pages)
            + (len(drawing.pages) if two_pass else 0)
        )
        logger.info("pages=%d / LLM呼び出し予定=%d回", len(drawing.pages), llm_calls)

        failures: list[str] = []
        # 🔴 タイル分割を見送って実効解像度が落ちたページは「読めていない」。
        # ログにだけ出しても誰も気づかない（実測2026-09-08: A1×23枚が実効47dpiで
        # 送られていた）。結果に載せて必ず画面まで運ぶ。
        for pg in drawing.pages:
            if pg.low_res_dpi and use_llm:
                failures.append(
                    f"ページ{pg.page}: 実効{pg.low_res_dpi:.0f}dpi でしか送れていません"
                    f"（大判・多ページのため分割を見送り）。図面の表や小さい記号は"
                    f"読めていない可能性が高いので、この画像由来の数量は信用しないでください。"
                    f"分割したい場合は環境変数 GOPIPE_MAX_LLM_CALLS を上げて実行します。"
                )
        # ベクター図は印字だけで拾えるので、画像認識を使わない選択ができる。
        # 大判が何枚もあると1枚あたりのタイル予算が足りず実効解像度が落ちるうえ、
        # 呼び出し回数ぶんの費用もかかる。読めない画から出た数量は足しても害になる。
        if use_llm:
            logger.info("extracting items via LLM (use_text_table=%s) ...", use_text_table)
            raw_items = extract(
                drawing, two_pass=two_pass, use_text_table=use_text_table, failures=failures
            )
        else:
            logger.info("画像認識は使いません（印字だけで拾います）")
            raw_items = []
            llm_calls = 0
        logger.info("extracted=%d items (failures=%d)", len(raw_items), len(failures))

        # ベクター(CAD)PDFなら、印字から推定ゼロで拾える分をここで足す。
        # 実測(2026-09-08 NEC府中 A1 23枚): 印字だけで 1,627 箇所・カバー率99.8%。
        # 画像認識の計数は同じ図面を2回かけると動くが、印字を数えるのは決定的。
        unread: list[tuple[int, str]] = []
        frame_dropped = 0
        vec: list[TakeoffItem] = []
        gunnamed: dict = {}
        if use_vector_text and any(p.text_lines for p in drawing.pages):
            frame = detect_frame(drawing)
            frame_dropped = len(frame)
            vec, unread = extract_vector(drawing, report=frame)
            logger.info(
                "ベクター印字から %d 行（図枠として除外 %d 種 / 型に載らず %d 行）",
                len(vec), frame_dropped, len(unread),
            )
            # 記号（文字ラベルの無い部材）を図形として数える。凡例の見本と同じ形が
            # 図の中に何個あるかを数えるだけなので、何度かけても同じ数になる。
            try:
                gitems, gunnamed = count_glyph_symbols(str(input_pdf), drawing, frame)
                if gitems:
                    logger.info(
                        "凡例と一致する記号を %d 個数えました（%d 種）",
                        sum(i.quantity for i in gitems), len(gitems),
                    )
                vec += gitems
            except Exception as e:  # noqa: BLE001  記号が数えられなくても拾い出しは続ける
                logger.warning("記号の計数に失敗: %s", e)
                gunnamed = {}
            raw_items = list(raw_items) + vec

        # その会社が育てた別名を辞書に混ぜてから分類する。これを忘れると、
        # 現場がいくら直しても次回の結果が変わらない（＝堀が効かない）。
        from .learned import current_org, load_aliases

        try:
            learned = load_aliases()
            if learned:
                n = self.dictionary.add_learned(learned)
                logger.info("learned aliases merged: %d (org=%s)", n, current_org())
        except Exception as e:  # noqa: BLE001  堀が引けなくても拾い出し自体は続ける
            logger.warning("learned aliases unavailable: %s", e)

        logger.info("classifying ...")
        items = classify(raw_items, self.dictionary)

        # 🔴 分類(classify)は辞書でカテゴリを塗り替えるため、拾えていないものの判定には
        # **分類前のベクター項目**を渡す。分類後を渡すと『配管の延長が要る』が黙って消える。
        has_text = any(p.text_lines for p in drawing.pages)
        gaps = find_gaps(drawing, vec) if has_text else []
        # 図の中で繰り返し出てくるのに、凡例から名前が引けなかった記号。
        # 勝手に名づけない。ページごとに並べると読む気を削ぐので、形ごとに1行へまとめる。
        if gunnamed:
            by_shape: dict[str, list] = {}
            for pg, gl in gunnamed.items():
                for g in gl:
                    if g.count >= 4:
                        by_shape.setdefault(g.key, []).append((pg, g))
            ranked = sorted(
                by_shape.values(), key=lambda v: -sum(g.count for _, g in v)
            )[:8]
            if ranked:
                desc = "／".join(
                    f"{sum(g.count for _, g in v)}個 "
                    f"{v[0][1].width:.0f}×{v[0][1].height:.0f}pt" for v in ranked
                )
                pages_all = sorted({pg for v in ranked for pg, _ in v})
                gaps.append(Gap(
                    item="名前が引けない繰り返し記号",
                    reason=f"図の中に同じ形が何度も出てきますが、凡例に一致する見本が"
                           f"ありません（{desc}）。通り芯の丸や柱のような、拾い出しと"
                           f"関係ない形も混じります",
                    action="拾い出しの対象かどうか、図で見て判断してください",
                    pages=pages_all,
                ))
        refrig = parse_size_table(drawing) if has_text else []
        if refrig:
            logger.info("冷媒配管サイズ表を %d 行読みました（記号→口径の読み替え表）", len(refrig))
        if gaps:
            logger.info("拾えていないもの %d 件を申告します", len(gaps))
            for g in gaps:
                logger.info("  %s", g.line())

        excel_path = out_dir / "拾い出し表.xlsx"
        logger.info("writing excel: %s", excel_path)
        write_excel(items, excel_path, gaps=gaps, unread=unread, failures=failures,
                    refrigerant=refrig)

        marker_path: Path | None = None
        if input_pdf.exists() and any(it.bbox for it in items):
            marker_path = out_dir / "AIマーカー付き図面.pdf"
            try:
                logger.info("writing marker pdf: %s", marker_path)
                write_marker_pdf(input_pdf, items, marker_path)
            except Exception as e:
                logger.warning("marker pdf failed: %s", e)
                marker_path = None

        return TakeoffResult(
            items=items, excel_path=excel_path, marker_pdf_path=marker_path,
            failures=failures, llm_calls=llm_calls,
            unread_labels=unread, frame_dropped=frame_dropped, gaps=gaps,
        )


def run_takeoff(
    input_pdf: str | Path,
    out_dir: str | Path,
    *,
    grid: int = 1,
    two_pass: bool = False,
    use_text_table: bool = True,
    use_llm: bool = True,
) -> TakeoffResult:
    return TakeoffPipeline().run(
        input_pdf, out_dir, grid=grid, two_pass=two_pass, use_text_table=use_text_table,
        use_llm=use_llm,
    )
