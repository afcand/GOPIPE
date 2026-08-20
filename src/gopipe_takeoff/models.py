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
    # 記号テンプレート照合（CV）が数えた個数。LLMの計数は同じ図面を2回かけると
    # 6/11 しか一致しない（実測2026-08-20）ため、決定的アルゴリズムの数を併記する。
    # None = 照合していない/踊り場が無く数えられなかった（そのときは数字を出さない）。
    qty_cv: float | None = None
    # 図面上でその部材が描かれていた色（機械で測った実測値）。
    # 設備図は色で系統・用途を分ける（既存再利用/移設/新設、SA/RA/OA/EA）。
    # 取り違えると数量が合っていても見積が丸ごと狂うので、AIに聞かず画素から測る。
    color: str | None = None       # "青" / "橙茶" など
    color_hue: float | None = None  # 実測した色相（度）。会社ごとの辞書と突き合わせる鍵
    # その色が会社の辞書で何を意味するか（既存再利用/移設/新設 など）。
    # 辞書に無い色は None のまま＝推測で埋めない。埋めると見積が丸ごと狂う。
    color_meaning: str | None = None
    # 図面に書かれた取付高さ（FL+◯◯mm）。設備図はここに Z 軸が印刷されている。
    # 🔴平面図だけ見ていると、高さの違う区間をつなぐ**立ち上がり・立下り**が
    # 点にしか見えず、延長が丸ごと落ちる。実測(2026-08-20 資料②): 高さが
    # 2,850mm にわたって12段あり、16要素すべてが 0m だった＝垂直区間が全部未計上。
    level_mm: float | None = None


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
    # このタイルがページ画像上のどこを覆っているか [x0,y0,x1,y1]（ページ画像のピクセル）。
    # タイルで見つけた部材の位置をページへ戻すのに要る。これが無いと
    # 「その部材が何色か」をページ画像から測れない（＝色で系統を判定できない）。
    page_rect: list[float] = Field(default_factory=list)

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
    # 🔴前処理（自動コントラスト＋鮮鋭化）を掛ける前の原画。**色の実測はこちらで行う**。
    # 前処理は小さい字をAIに読ませるためのもので、彩度を削る。実測(2026-08-20 資料③):
    # 青ダクトの色画素が 4292→1031(-76%)、インクに占める色の割合が 0.47→0.17 まで落ち、
    # 判定の下限(0.18)を割って「色が付いていない」ことになっていた。
    image_raw: bytes | None = None
    # tiles が非空なら抽出は tile 単位で行う (split > 1 のとき)。
    # image_png はマーカー描画や fallback 用に残しておく。
    tiles: list[Tile] = []


class Drawing(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_path: str
    pages: list[DrawingPage] = []
