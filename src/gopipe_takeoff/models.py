from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BBox(BaseModel):
    """PDF 上の領域（左上原点・ピクセル単位、レンダリング解像度に依存）。"""

    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_list(cls, v: list[float]) -> BBox:
        return cls(x0=v[0], y0=v[1], x1=v[2], y1=v[3])


class TakeoffItem(BaseModel):
    """1 つの拾い出し項目。"""

    page: int = Field(..., ge=1)
    name: str  # 例: クロス張替
    spec: str | None = None  # 型番・仕様
    quantity: float
    unit: str  # m2 / m / 式 / 箇所 など
    location: str | None = None  # 部屋名など
    bbox: BBox | None = None
    category: str | None = None  # 内訳カテゴリ（分類後に埋まる）
    confidence: float = 1.0  # 0..1
    source: str | None = None  # 抽出由来: "text_table" | "reconciled"（vision 由来は None）
    # AI が図面から実際に読み取った生の名称。分類で name を正規名に寄せても消さない。
    # 学習（会社の辞書）の鍵はこちらを使う。表示名を鍵にすると、直すたびに
    # 別の部材まで巻き添えで化ける。
    raw_name: str | None = None
    # 機器表と図面で数量が食い違ったときの「図面側の読み」。人が検算する材料として残す。
    # 黙って上書きして 🟢 にしない（AIは提案・確定は人）。
    qty_vision: float | None = None
    # 🔴 数量の「出所」。表から読んだ確定値と、図面を見た推定値が同じ列に並ぶと
    # 人は見分けられない。実測(2026-08-19)では同じ図面を2回かけると
    # ダクトφ250 が 25.0m→20.0m、VD が 8個→0個 と動いた＝推定値だった。
    # 確定と推定を混ぜないために、どう出した数かを必ず持たせる。
    #   "table"    … 図面に印刷された表の員数欄を読んだ（最も確か）
    #   "count"    … 図面上の記号を数えた
    #   "measure"  … 図面に書かれた寸法から計算した
    #   "estimate" … 根拠が弱い推定（要検算。確度は 0.45 で頭打ちにする）
    #   "none"     … 数量を出せなかった（quantity=0）
    qty_basis: str | None = None


class Tile(BaseModel):
    """ページを rows×cols に分割した 1 タイル。Vision LLM が細部を読みやすくするため。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    image_png: bytes
    row: int  # 0..rows-1
    col: int  # 0..cols-1
    grid: int = 1  # 後方互換（正方分割時の N）。rows/cols を正とする。
    rows: int = 0  # 0 なら grid を使う
    cols: int = 0
    width: int  # px (タイル画像の画素サイズ)
    height: int
    # このタイルが「担当する」中心領域が、画像全体に占める割合 [x0,y0,x1,y1]（0..1）。
    # 外側は隣タイルとの重なり＝文脈用に見せるだけで、数えさせない領域。
    # これが無いと、重なりに写ったものを両方のタイルが出して二重計上になる。
    core: list[float] = Field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])

    @property
    def n_rows(self) -> int:
        return self.rows or self.grid

    @property
    def n_cols(self) -> int:
        return self.cols or self.grid


class DrawingPage(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    page: int
    width: float  # px @ render dpi
    height: float
    text: str = ""
    image_png: bytes | None = None  # 抽出に使うレンダリング画像 (フルページ)
    # tiles が非空なら抽出は tile 単位で行う (split > 1 のとき)。
    # image_png はマーカー描画や fallback 用に残しておく。
    tiles: list[Tile] = []


class Drawing(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_path: str
    pages: list[DrawingPage] = []
