from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from utils import get_logger

from .classifier import classify
from .dictionary import TakeoffDictionary
from .excel_writer import write_excel
from .extractor import extract
from .locale import resolve as resolve_knowledge
from .marker import write_marker_pdf
from .models import TakeoffItem
from .pdf_loader import load_pdf

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

        logger.info("loading PDF: %s (grid=%d, two_pass=%s)", input_pdf, grid, two_pass)
        drawing = load_pdf(input_pdf, grid=grid)
        llm_calls = sum(len(p.tiles) or 1 for p in drawing.pages) + (
            len(drawing.pages) if two_pass else 0
        )
        logger.info("pages=%d / LLM呼び出し予定=%d回", len(drawing.pages), llm_calls)

        logger.info("extracting items via LLM (use_text_table=%s) ...", use_text_table)
        failures: list[str] = []
        raw_items = extract(
            drawing, two_pass=two_pass, use_text_table=use_text_table, failures=failures
        )
        logger.info("extracted=%d items (failures=%d)", len(raw_items), len(failures))

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

        excel_path = out_dir / "拾い出し表.xlsx"
        logger.info("writing excel: %s", excel_path)
        write_excel(items, excel_path)

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
        )


def run_takeoff(
    input_pdf: str | Path,
    out_dir: str | Path,
    *,
    grid: int = 1,
    two_pass: bool = False,
    use_text_table: bool = True,
) -> TakeoffResult:
    return TakeoffPipeline().run(
        input_pdf, out_dir, grid=grid, two_pass=two_pass, use_text_table=use_text_table
    )
