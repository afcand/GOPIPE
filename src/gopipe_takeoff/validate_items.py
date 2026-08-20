"""ルールベース整合チェック — 拾い出し項目の怪しい行を機械的にフラグ（精度・信頼性）。

AI/分類の誤りを純ルールで検出してレビューを助ける（即時・無料）。
数量0 / 単位×カテゴリ不一致 / 外れ値 / 重複の疑い を各行の issue リストで返す。
"""
from __future__ import annotations

from .models import TakeoffItem

# 系統別配管（長さ m 想定）
_LEN_CATS = {"給水", "給湯", "排水", "通気", "消火", "ガス", "冷媒", "冷温水", "ドレン", "Piping"}
# 個数・台数で数えるもの
_COUNT_CATS = {"弁類", "継手", "計装", "衛生器具", "機器", "Valve", "Equipment", "HVAC"}
# 面積/長さ（ダクト・保温・断熱）
_AREA_CATS = {"ダクト", "保温", "断熱材", "気密防湿", "Duct", "Insulation"}

_COUNT_UNITS = {"個", "台", "ea", "本", "箇所", "枚", "面"}
_LEN_UNITS = {"m", "ft", "lf"}
_AREA_UNITS = {"m2", "ft2"}
_OUTLIER = 100000.0


def check_item(it: TakeoffItem) -> list[str]:
    """1項目の整合 issue リスト（無ければ空）。"""
    issues: list[str] = []
    cat = (it.category or "").strip()
    unit = (it.unit or "").strip()
    q = it.quantity or 0
    if q == 0:
        issues.append("数量0（未取得）")
    if it.spec and q == 0:
        issues.append("spec有り・数量0")
    if q < 0:
        issues.append("数量がマイナス")
    if q > _OUTLIER:
        issues.append("数量が異常に大きい")
    if cat in _LEN_CATS and unit and unit not in _LEN_UNITS:
        issues.append(f"配管系なのに単位が「{unit}」（m想定）")
    elif cat in _AREA_CATS and unit and unit not in (_AREA_UNITS | _LEN_UNITS):
        issues.append(f"面積/長さ系なのに単位が「{unit}」")
    elif cat in _COUNT_CATS and unit and unit not in _COUNT_UNITS:
        issues.append(f"個数系なのに単位が「{unit}」")
    if not cat or cat == "その他":
        issues.append("未分類（その他）")
    if it.qty_vision is not None and it.qty_vision != q:
        # 機器表を採用した行。図面の読みと食い違ったことを必ず人に見せる。
        issues.append(f"図面と機器表で数量が違う（図面 {_fmt(it.qty_vision)} / 機器表 {_fmt(q)}）")
    return issues


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


# 立ち上がり・立下りとみなす最小の高低差(mm)。これ未満は施工誤差や表記ゆれの範囲。
_RISER_MIN_MM = 300.0


def find_missing_risers(items: list[TakeoffItem]) -> list[dict]:
    """高さの違う区間をつなぐ「立ち上がり・立下り」が未計上でないかを見る。

    🔴 平面図では垂直の区間が点にしか見えず、延長が丸ごと落ちる。
    実測(2026-08-20 資料②): 高さが 2,850mm にわたって12段あり、
    **16要素すべてが 0m** だった＝垂直の配管・ダクトが1本も数えられていない。

    ここでは数量を勝手に作らない。「この系統は FL+2700 と FL+5300 に跨っており、
    その間の垂直区間が表に無い」という**事実だけ**を出す。
    長さを埋めるのは人（または将来の3D経路追跡）の仕事。
    """
    groups: dict[tuple, list[TakeoffItem]] = {}
    for it in items:
        if it.level_mm is None:
            continue
        if (it.unit or "").strip() not in {"m", "m2"}:
            continue
        key = ((it.name or "").strip(), (it.category or "").strip())
        groups.setdefault(key, []).append(it)

    found: list[dict] = []
    for (name, cat), rows in groups.items():
        levels = sorted({float(r.level_mm) for r in rows})
        if len(levels) < 2:
            continue
        drop = levels[-1] - levels[0]
        if drop < _RISER_MIN_MM:
            continue
        # 縦の区間が既に行として在るなら二重に言わない
        if any(k in (r.name or "") for r in rows for k in ("立上", "立ち上", "立下", "立ち下")):
            continue
        found.append({
            "name": name, "category": cat,
            "levels": [int(v) for v in levels],
            "drop_mm": int(drop),
            "rows": len(rows),
        })
    found.sort(key=lambda x: -x["drop_mm"])
    return found


def check(items: list[TakeoffItem]) -> dict[int, list[str]]:
    """各 index → issue リスト（issue のある行のみ）。重複の疑いも検出。"""
    out: dict[int, list[str]] = {}
    seen: dict[tuple, int] = {}
    # 仕様欄の書き方だけが違う同一品目（例: 図面から「DN20」・機器表から「BV-1」）は
    # 上の key では別物に見えてしまう。数量と単位まで一致する行は二重計上の疑いとして
    # 必ず人に見せる。見積の数量が倍になる事故は、黙って通してはいけない。
    by_qty: dict[tuple, int] = {}
    for i, it in enumerate(items):
        issues = check_item(it)
        key = ((it.name or "").strip(), (it.spec or "").strip(), (it.location or "").strip())
        if any(key):
            if key in seen:
                issues.append(f"重複の疑い（行{seen[key] + 1}と同一）")
            else:
                seen[key] = i
        # 同じ品目・同じ数量でも、階や系統が違えば別物。場所とページまで一致した
        # ときだけ疑う。ここを緩くすると 1F と 2F の弁が真っ赤になり、
        # 本物の二重計上がその中に埋もれて見えなくなる。
        qty_key = (
            (it.name or "").strip(),
            float(it.quantity or 0),
            (it.unit or "").strip(),
            (it.location or "").strip(),
            it.page,
        )
        if qty_key[0] and qty_key[1] > 0:
            if qty_key in by_qty and by_qty[qty_key] != i and not any("重複" in s for s in issues):
                issues.append(f"二重計上の疑い（行{by_qty[qty_key] + 1}と同じ数量）")
            else:
                by_qty.setdefault(qty_key, i)

        # 名前の書き方が違っても同じ品物のことがある。
        #   図面から: 「給水管(VP)」 spec=DN20
        #   機器表から: 「GP-1 給水管」 spec=VP DN20 GP-1
        # 数量・単位・ページが同じで、仕様が包含関係なら二重計上を疑う。
        # 見積の数量が倍になる事故は、名前が違うという理由で見逃してはいけない。
        if not any("重複" in s or "二重計上" in s for s in issues) and (it.quantity or 0) > 0:
            for j in range(i):
                other = items[j]
                if (
                    other.page == it.page
                    and float(other.quantity or 0) == float(it.quantity or 0)
                    and (other.unit or "") == (it.unit or "")
                    and (
                        _spec_overlap(other.spec, it.spec)
                        or _name_overlap(other.name, it.name)
                    )
                ):
                    issues.append(f"二重計上の疑い（行{j + 1}と同じ品物の可能性）")
                    break
        if issues:
            out[i] = issues

    # 立ち上がり・立下りが未計上の系統は、その系統の行に印を付ける。
    # 「高さの違う行が並んでいる」だけでは人は気づけない。
    risers = {(r["name"], r["category"]): r for r in find_missing_risers(items)}
    for i, it in enumerate(items):
        r = risers.get(((it.name or "").strip(), (it.category or "").strip()))
        if r:
            out.setdefault(i, []).append(
                f"立上り/立下りが未計上の可能性（FL+{r['levels'][0]}〜FL+{r['levels'][-1]}"
                f"＝高低差{r['drop_mm']}mm）"
            )
    return out


def _spec_overlap(a: str | None, b: str | None) -> bool:
    """仕様欄が実質同じものを指しているか（「DN20」⊂「VP DN20 GP-1」）。"""
    na = (a or "").replace(" ", "").upper()
    nb = (b or "").replace(" ", "").upper()
    if len(na) < 3 or len(nb) < 3:
        return False
    return na in nb or nb in na


def _name_core(s: str | None) -> str:
    """名称から図面記号と括弧書きを落として「芯」だけにする。

    「GP-1   給水管」→「給水管」 / 「給水管(VP)」→「給水管」
    ここを甘くして共通2文字で判定すると、給水管と排水管が「水管」で一致して
    誤報する。誤報が増えると本物の二重計上がその中に埋もれるので、芯で比べる。
    """
    import re as _re

    t = _re.sub(r"[（(].*?[)）]", "", s or "")          # 括弧書き
    t = _re.sub(r"\b[A-Za-z]{1,4}[-_]?\d{1,3}\b", "", t)  # 図面記号 GP-1 / FT1
    return _re.sub(r"[^\wぁ-んァ-ヶ一-龠]", "", t)


def _name_overlap(a: str | None, b: str | None) -> bool:
    """名称が同じ品物を指していそうか（芯が一致するか）。

    図面からは「給水管(VP)」、機器表からは「GP-1 給水管」のように、同じ管が
    違う書き方で2行に分かれることがある。ページ・数量・単位が既に一致している
    行同士にだけ使う。
    """
    ca, cb = _name_core(a), _name_core(b)
    if len(ca) < 2 or len(cb) < 2:
        return False
    return ca == cb or ca in cb or cb in ca
