"""GOPIPE REST API（FastAPI）。

既存の拾い出し／見積／申請／予防保全／透明見積エンジンを Web（Next.js）や
他クライアントから叩けるよう REST 化する。エンジンは src/gopipe_takeoff を直接 import。

起動:
    .venv/bin/uvicorn api.main:app --reload --port 8000
    （mock は file 無しでサンプルが返る。本番は provider=claude＋PDF）
"""
from __future__ import annotations

import datetime
import hmac
import os
import sys
import tempfile
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "shared"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from gopipe_takeoff.locale import resolve as _kpath  # noqa: E402

# Vercel 等サーバレスは /tmp 以外が読取専用。出力 xlsx は応答に含めない副産物なので
# 書込可能な一時ディレクトリへ逃がす（run_takeoff が out_dir を mkdir する）。
OUT = Path(tempfile.gettempdir()) / "gopipe_out"

app = FastAPI(title="GOPIPE API", version="0.1.0")
# 画面は同一オリジンの Next.js 中継を通るので、ブラウザから直接叩く必要はない。
# "*" のままだと、どのサイトからでも本番APIを叩けてしまう。
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "GOPIPE_ALLOWED_ORIGINS",
        "https://gopipe-web.vercel.app,https://gopipe.vercel.app,http://localhost:3210",
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _save_upload(file: UploadFile | None, storage_path: str = "") -> Path:
    """設備図PDFを一時ファイルに置いてパスを返す。

    storage_path が来たら Supabase Storage から取る（本線。Vercel の 4.5MB 制限を避けるため
    ブラウザは Storage へ直接アップロードする）。file は小さいPDFの直POST用に残す。
    どちらも無ければ mock 用のダミーパス。
    """
    tmpdir = Path(tempfile.gettempdir())
    if storage_path:
        from gopipe_takeoff import store

        data = store.download_drawing(storage_path)
        tmp = tmpdir / (Path(storage_path).name or "drawing.pdf")
        tmp.write_bytes(data)
        return tmp
    if file is None:
        return ROOT / "samples" / "dummy_設備図.pdf"
    tmp = tmpdir / (file.filename or "upload.pdf")
    tmp.write_bytes(file.file.read())
    return tmp


def _items_json(items) -> list[dict]:
    """明細＋整合チェックの指摘。指摘は画面で人に見せるためのもので、数量は書き換えない。"""
    from gopipe_takeoff import validate_items as _vchk

    flags = _vchk.check(items)
    return [
        {
            "category": it.category, "name": it.name, "spec": it.spec,
            "location": it.location, "quantity": it.quantity, "unit": it.unit,
            "confidence": round(it.confidence, 2),
            # 数量の出所（表/計数/寸法/推定/未取得）。これを落とすと、画面と
            # Web経由の Excel から「確かな数」と「推定」の区別が消える。
            "qty_basis": it.qty_basis,
            "qty_cv": it.qty_cv,
            "source": it.source,
            "checks": flags.get(i, []),
            # 学習の鍵。表示名を鍵にすると、直すたびに別部材まで巻き添えで化ける。
            "raw_name": it.raw_name or it.name,
            "qty_vision": it.qty_vision,
            "page": it.page,
        }
        for i, it in enumerate(items)
    ]


# 無認証で開放してよいプロバイダ（自社の LLM キーを消費しない＝課金されない）。
# claude / openai は ANTHROPIC_API_KEY 等を消費するので、必ずキーゲートを通す。
_FREE_PROVIDERS = {"", "mock"}


def _require_key(api_key: str | None, what: str) -> None:
    """課金・書き込みを伴う操作にサーバ側キーを要求する（fail-closed）。

    GOPIPE_API_KEY が未設定なら「誰でも自社キーで Claude を叩ける」状態なので、
    未設定そのものを 503 で拒否する。mock デモは無認証のまま通す。
    """
    expected = os.environ.get("GOPIPE_API_KEY", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=f"{what} は停止中です（サーバ側 GOPIPE_API_KEY 未設定）。mock は利用できます。",
        )
    if not api_key or not hmac.compare_digest(api_key, expected):
        raise HTTPException(
            status_code=401,
            detail=f"{what} には x-gopipe-key ヘッダが必要です（provider=mock は不要）。",
        )


def _require_org_read(org_slug: str, api_key: str | None) -> None:
    """会社を指定して辞書を引く操作は鍵必須（他社の育てた辞書を覗かせない）。"""
    if org_slug:
        _require_key(api_key, f"会社({org_slug})の辞書の参照")


def _dictionary_for(org_slug: str = ""):
    """その会社の辞書（組み込み＋会社が育てた別名）を1か所で組み立てる。

    ここを通さない経路があると、覚えた呼び方が「拾い出しでは効くのに見積では戻る」
    という形で崩れる。classify を呼ぶ全経路はこの関数を使うこと。
    """
    from gopipe_takeoff.dictionary import TakeoffDictionary
    from gopipe_takeoff.learned import load_aliases

    if org_slug:
        os.environ["GOPIPE_ORG"] = org_slug
    d = TakeoffDictionary.from_yaml(_kpath("dictionary.yaml"))
    try:
        learned = load_aliases()
        if learned:
            d.add_learned(learned)
    except Exception:  # noqa: BLE001  堀が引けなくても拾い出しは続ける
        pass
    return d


def _takeoff(
    provider: str,
    file: UploadFile | None,
    api_key: str | None = None,
    storage_path: str = "",
    org_slug: str = "",
):
    p = (provider or "mock").strip().lower()
    if p not in _FREE_PROVIDERS:
        _require_key(api_key, f"provider={p}")
    if storage_path:  # 他社の図面を引かせない。参照は必ずキー経由に閉じる。
        _require_key(api_key, "Storage 上の図面の読み込み")
    os.environ["GOPIPE_LLM_PROVIDER"] = p or "mock"
    os.environ["GOPIPE_ORG"] = org_slug or "default"  # どの会社の辞書を引くか
    from gopipe_takeoff import run_takeoff

    pdf = _save_upload(file, storage_path)
    return run_takeoff(str(pdf), str(OUT))


@app.get("/", include_in_schema=False)
def root():
    # 素の URL はルート未定義で 404 になるため、/docs(Swagger UI) へ誘導する
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/docs")


@app.get("/share", include_in_schema=False)
def share():
    # 社員・クライアント共有用の公開ハブ（マンガ・使い方動画・デモ導線・共有文）。
    # 自己完結HTML。ログイン不要で誰でも閲覧できるよう素のHTMLResponseで返す。
    from fastapi.responses import HTMLResponse

    html = (Path(__file__).resolve().parent / "share.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/municipalities")
def municipalities():
    from gopipe_takeoff.application import _load_municipalities

    data = _load_municipalities()
    return {"municipalities": [m.get("name") for m in data.get("municipalities", [])]}


@app.post("/takeoff")
async def takeoff(
    provider: str = Form("mock"),
    persist: bool = Form(False),
    org_slug: str = Form("default"),
    project_slug: str = Form("takeoff"),
    title: str = Form(""),
    storage_path: str = Form(""),
    file_name: str = Form(""),
    file: UploadFile | None = File(None),
    x_gopipe_key: str | None = Header(default=None),
):
    result = _takeoff(provider, file, x_gopipe_key, storage_path, org_slug)
    resp: dict = {"count": len(result.items), "items": _items_json(result.items)}
    # 読めなかったページは黙って落とさない。「0件」と「読めていない」は別物。
    if getattr(result, "failures", None):
        resp["warnings"] = result.failures
    if persist:
        # 書き込みは service_role（RLSバイパス）で走る。無認証で開けない。
        _require_key(x_gopipe_key, "persist=true")
        from gopipe_takeoff import store

        if store.is_enabled():
            try:
                resp["persisted"] = store.persist_takeoff(
                    org_slug=org_slug, org_name=org_slug,
                    project_slug=project_slug, title=title, items=result.items,
                    source_pdf_path=storage_path or None,
                    file_name=file_name or None,
                    warnings=getattr(result, "failures", None),
                )
            except Exception as e:  # 抽出は成功済み。保存失敗で全体は落とさない
                resp["persisted"] = {"error": str(e)}
        else:
            resp["persisted"] = {"error": "Supabase 未設定（SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY）"}
    return resp


@app.post("/estimate")
async def estimate(
    provider: str = Form("mock"),
    overhead: float = Form(0.10),
    file: UploadFile | None = File(None),
    x_gopipe_key: str | None = Header(default=None),
):
    from gopipe_takeoff.estimate import build_estimate
    from gopipe_takeoff.pricer import Pricer

    result = _takeoff(provider, file, x_gopipe_key)
    pricer = Pricer.from_yaml(_kpath("unit_prices.yaml"))
    est = build_estimate(result.items, pricer, overhead_rate=overhead)
    return {
        "subtotal": est.subtotal,
        "overhead": est.overhead,
        "tax": est.tax,
        "total": est.total,
        "lines": [
            {
                "category": ln.item.category, "name": ln.item.name, "spec": ln.item.spec,
                "quantity": ln.item.quantity, "unit": ln.item.unit,
                "unit_price": ln.unit_price, "amount": ln.amount,
                "material": ln.material, "labor": ln.labor,
            }
            for ln in est.lines
        ],
    }


@app.post("/application")
async def application(
    provider: str = Form("mock"),
    municipality: str = Form(""),
    contractor_name: str = Form(""),
    contractor_number: str = Form(""),
    chief_engineer: str = Form(""),
    owner: str = Form(""),
    address: str = Form(""),
    work_type: str = Form("改造"),
    file: UploadFile | None = File(None),
    x_gopipe_key: str | None = Header(default=None),
):
    from gopipe_takeoff.application import ProjectInfo, build_application_markdown

    result = _takeoff(provider, file, x_gopipe_key)
    proj = ProjectInfo(
        municipality=municipality, contractor_name=contractor_name,
        contractor_number=contractor_number, chief_engineer=chief_engineer,
        owner=owner, address=address, work_type=work_type,
    )
    return {"markdown": build_application_markdown(result.items, proj)}


@app.post("/maintenance")
async def maintenance(
    provider: str = Form("mock"),
    installed_year: int = Form(2008),
    current_year: int = Form(0),
    file: UploadFile | None = File(None),
    x_gopipe_key: str | None = Header(default=None),
):
    from gopipe_takeoff.maintenance import (
        build_ledger,
        build_maintenance_proposal,
        load_service_life,
        recommend_plan,
    )

    cy = current_year or datetime.date.today().year
    result = _takeoff(provider, file, x_gopipe_key)
    table, plans = load_service_life()
    ledger = build_ledger(result.items, installed_year, table)
    plan = recommend_plan(ledger, plans, current_year=cy)
    return {
        "plan": (
            {"name": plan.name, "monthly_fee": plan.monthly_fee,
             "interval_months": plan.interval_months, "scope": plan.scope}
            if plan else None
        ),
        "ledger": [
            {
                "name": r.item.name, "label": r.label, "spec": r.item.spec,
                "installed_year": r.installed_year, "service_life": r.service_life,
                "recommend_year": r.recommend_year, "remaining": r.remaining(cy),
            }
            for r in sorted(ledger, key=lambda r: r.remaining(cy))
        ],
        "markdown": build_maintenance_proposal(
            ledger, plan, plans, installed_year=installed_year, current_year=cy
        ),
    }


@app.post("/emergency")
async def emergency(symptom: str = Form(...)):
    from gopipe_takeoff.emergency import Catalog, build_quote_card, diagnose

    catalog = Catalog.from_yaml()
    cands = diagnose(symptom, catalog)
    return {
        "candidates": [
            {
                "name": c.job.name,
                "price_min_incl_tax": c.job.price_range_incl_tax()[0],
                "price_max_incl_tax": c.job.price_range_incl_tax()[1],
                "warranty_months": c.job.warranty_months,
                "score": c.score,
            }
            for c in cands
        ],
        "markdown": build_quote_card(symptom, cands),
    }


@app.post("/insulation")
async def insulation(
    rooms: list[dict] = Body(...),
    overhead: float = 0.10,
    org_slug: str = "",
    x_gopipe_key: str | None = Header(default=None),
):
    _require_org_read(org_slug, x_gopipe_key)
    """部屋寸法/面積（LiDAR・手測り）→ 断熱面積(壁/天井/床 m²)の拾い出し＋見積。

    rooms 例: [{"name":"LDK","width_m":5.4,"depth_m":4.2,"height_m":2.5,
                "exterior_wall_len_m":12,"openings_m2":8,
                "material":"断熱材(グラスウール)","thickness_mm":105}]
    LiDAR が面積を直接出す場合は floor_area_m2 / wall_area_m2 でも可。
    """
    from gopipe_takeoff.classifier import classify
    from gopipe_takeoff.dictionary import TakeoffDictionary
    from gopipe_takeoff.estimate import build_estimate
    from gopipe_takeoff.insulation_area import rooms_from_dicts, to_takeoff_items
    from gopipe_takeoff.pricer import Pricer

    items = classify(
        to_takeoff_items(rooms_from_dicts(rooms)),
        _dictionary_for(org_slug),
    )
    est = build_estimate(
        items, Pricer.from_yaml(_kpath("unit_prices.yaml")), overhead_rate=overhead
    )
    return {
        "count": len(items),
        "items": _items_json(items),
        "subtotal": est.subtotal,
        "total": est.total,
    }


@app.post("/site_measure")
async def site_measure(
    measures: list[dict] = Body(...),
    overhead: float = 0.10,
    org_slug: str = "",
    x_gopipe_key: str | None = Header(default=None),
):
    _require_org_read(org_slug, x_gopipe_key)
    """現地実測（LiDAR/巻尺）→ 空調・配管の拾い出し＋見積。

    measures 例: [{"kind":"角ダクト","name":"角ダクト","width_mm":500,"height_mm":400,"length_m":10},
                  {"kind":"配管","name":"冷温水配管","dia_mm":80,"length_m":12},
                  {"kind":"個数","name":"吹出口","count":8}]
    """
    from gopipe_takeoff.classifier import classify
    from gopipe_takeoff.dictionary import TakeoffDictionary
    from gopipe_takeoff.estimate import build_estimate
    from gopipe_takeoff.pricer import Pricer
    from gopipe_takeoff.site_measure import items_from_measures

    items = classify(
        items_from_measures(measures),
        _dictionary_for(org_slug),
    )
    est = build_estimate(
        items, Pricer.from_yaml(_kpath("unit_prices.yaml")), overhead_rate=overhead
    )
    return {
        "count": len(items),
        "items": _items_json(items),
        "subtotal": est.subtotal,
        "total": est.total,
    }


@app.post("/riser")
async def riser(
    risers: list[dict] = Body(...),
    overhead: float = 0.10,
    org_slug: str = "",
    x_gopipe_key: str | None = Header(default=None),
):
    _require_org_read(org_slug, x_gopipe_key)
    """系統図×階高 → 立管・隠蔽配管の延長/継手/弁を積算した拾い出し＋見積。

    平面図に長さが出ない立管を 階高×階数×本数 で延長(m)化し、継手・弁も階数比例で積算する。

    risers 例: [{"name":"給水立管 PS-1","floors":5,"floor_height_m":3.2,"count":2,
                 "spec":"VLP DN20","material":"給水管","branch_per_floor_m":3,
                 "fittings_per_floor":2,"valves_per_floor":1}]
    """
    from gopipe_takeoff.classifier import classify
    from gopipe_takeoff.dictionary import TakeoffDictionary
    from gopipe_takeoff.estimate import build_estimate
    from gopipe_takeoff.pricer import Pricer
    from gopipe_takeoff.riser_estimate import risers_from_dicts, to_takeoff_items

    items = classify(
        to_takeoff_items(risers_from_dicts(risers)),
        _dictionary_for(org_slug),
    )
    est = build_estimate(
        items, Pricer.from_yaml(_kpath("unit_prices.yaml")), overhead_rate=overhead
    )
    return {
        "count": len(items),
        "items": _items_json(items),
        "subtotal": est.subtotal,
        "total": est.total,
    }


@app.post("/legend_count")
async def legend_count(
    file: UploadFile | None = File(None),
    overhead: float = 0.10,
    org_slug: str = "",
    x_gopipe_key: str | None = Header(default=None),
):
    _require_org_read(org_slug, x_gopipe_key)
    """凡例（記号→名称）を解析し、テキスト層の記号出現数を機械カウント → 個数モノの拾い出し＋見積。

    ベクターPDF専用（テキスト層が必要）。スキャン図やテキスト層が無い場合は 0 件を返す。
    各記号は「総出現数 − 凡例定義1回」を図面配置数として個でカウントする。
    """
    from gopipe_takeoff.classifier import classify
    from gopipe_takeoff.dictionary import TakeoffDictionary
    from gopipe_takeoff.estimate import build_estimate
    from gopipe_takeoff.legend_count import count_from_pdf
    from gopipe_takeoff.pricer import Pricer

    pdf = _save_upload(file)
    if not Path(pdf).exists():
        return {"count": 0, "items": [], "subtotal": 0, "total": 0,
                "note": "ベクターPDF（テキスト層あり）をアップロードしてください"}
    items = classify(
        count_from_pdf(str(pdf)),
        _dictionary_for(org_slug),
    )
    est = build_estimate(
        items, Pricer.from_yaml(_kpath("unit_prices.yaml")), overhead_rate=overhead
    )
    return {
        "count": len(items),
        "items": _items_json(items),
        "subtotal": est.subtotal,
        "total": est.total,
    }


# --------------------------------------------------------------------------
# Web UI（Vercel）から使う口。Streamlit が in-process でやっていた
# 「修正 → 学習の堀」「Excel 出力」を REST 化する。
# --------------------------------------------------------------------------

@app.get("/suppressions")
async def suppressions(org_slug: str = "", x_gopipe_key: str | None = Header(default=None)):
    """この会社が「要らない」と繰り返し消してきた品目（拾わないことの学習）。

    見えない学習は暴走する。何を学んだかを人が読めて、間違いなら消せる状態にしておく。
    """
    _require_org_read(org_slug, x_gopipe_key)
    from gopipe_takeoff import store

    if not store.is_enabled():
        return {"count": 0, "items": [], "note": "Supabase 未設定"}
    items = store.load_suppressions(org_slug or "default")
    return {"count": len(items), "items": items}


@app.post("/learn")
async def learn(
    payload: dict = Body(...),
    x_gopipe_key: str | None = Header(default=None),
):
    """人が直した行を学習の堀（learned_aliases）へ還元する。

    payload = {"org_slug": "haruki", "project": "webui",
               "corrections": [{"before": {...}, "after": {...}}, ...]}
    before/after は name / spec / quantity / unit / category を持つ dict。
    名称・カテゴリ・単位のいずれかが変わった行だけを別名として学習する。
    """
    _require_key(x_gopipe_key, "学習の記録(/learn)")

    from gopipe_takeoff.feedback import record_correction
    from gopipe_takeoff.learned import record_alias

    org = str(payload.get("org_slug") or "default")
    project = str(payload.get("project") or "webui")
    rows = payload.get("corrections") or []
    ts = datetime.date.today().isoformat()

    captured = learned = 0
    for row in rows:
        before = row.get("before") or {}
        after = row.get("after") or {}
        # 鍵は「AIが実際に読んだ生の名前」。表示名(before.name)は分類で正規化済みの
        # ことがあり、それを鍵にすると別部材まで巻き添えで化ける。
        old_name = str(before.get("raw_name") or before.get("name") or "").strip()
        new_name = str(after.get("name") or "").strip()
        if not new_name:
            continue
        try:
            record_correction(project=project, before=before, after=after, ts=ts)
            captured += 1
        except Exception:  # noqa: BLE001  副産物の記録失敗で学習を止めない
            pass
        changed = (
            old_name != new_name
            or str(before.get("category") or "") != str(after.get("category") or "")
            or str(before.get("unit") or "") != str(after.get("unit") or "")
        )
        if changed and old_name:
            try:
                if record_alias(
                    old_name, new_name,
                    category=(after.get("category") or None),
                    unit=(after.get("unit") or None),
                    org=org,
                ):
                    learned += 1
            except Exception:  # noqa: BLE001
                pass
    return {"captured": captured, "learned": learned, "org_slug": org}


@app.post("/export/xlsx")
async def export_xlsx(payload: dict = Body(...)):
    """確定済みの拾い出し明細を Excel にして返す（ダウンロード用）。

    サーバレスは /tmp だけが書込可なので、生成物は一時ファイル経由でバイト列にして返す。
    """
    from fastapi.responses import Response

    from gopipe_takeoff.excel_writer import write_excel
    from gopipe_takeoff.models import TakeoffItem

    rows = payload.get("items") or []
    items = [
        TakeoffItem(
            page=int(r.get("page") or 1),
            name=str(r.get("name") or "").strip(),
            spec=(r.get("spec") or None),
            quantity=float(r.get("quantity") or 0),
            unit=str(r.get("unit") or ""),
            location=(r.get("location") or None),
            category=(r.get("category") or None),
            confidence=float(r.get("confidence") or 1.0),
            # 画面で確定した明細を Excel にするとき、出所を運ばないと
            # 備考の「図面の表から／🔴AI推定（要検算）」が全部消える。
            qty_basis=(r.get("qty_basis") or None),
            qty_cv=(float(r["qty_cv"]) if r.get("qty_cv") is not None else None),
            source=(r.get("source") or None),
        )
        for r in rows
        if str(r.get("name") or "").strip()
    ]
    if not items:
        raise HTTPException(status_code=400, detail="items が空です")

    OUT.mkdir(parents=True, exist_ok=True)
    path = write_excel(items, OUT / "GOPIPE_拾い出し.xlsx")
    data = Path(path).read_bytes()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="GOPIPE_takeoff.xlsx"'},
    )


@app.get("/learned")
async def learned_list(
    org_slug: str = "default",
    locale: str = "ja",
    x_gopipe_key: str | None = Header(default=None),
):
    """その会社が育てた学習済み別名（＝堀の中身）を Supabase から返す。

    顧客データなのでキー必須。UI では「使うほど育っている」ことを見せる材料になる。
    """
    _require_key(x_gopipe_key, "学習済み別名の取得(/learned)")
    from gopipe_takeoff import store

    if not store.is_enabled():
        raise HTTPException(status_code=503, detail="Supabase 未設定")
    aliases = store.load_learned_aliases(org_slug, locale=locale)
    return {
        "org_slug": org_slug, "locale": locale, "count": len(aliases),
        "aliases": [
            {"raw": k, "canonical": v.get("canonical"),
             "category": v.get("category"), "unit": v.get("unit")}
            for k, v in aliases.items()
        ],
    }


@app.post("/inspect")
async def inspect(
    storage_path: str = Form(""),
    file: UploadFile | None = File(None),
    x_gopipe_key: str | None = Header(default=None),
):
    """投げる前に、その図面が読めるものかを返す。

    スキャン画像の図面はテキスト層が無く、機器表の数量を確定情報として使えないため
    精度が落ちる。実行して薄い結果が出てから「AIは使えない」と結論される前に、
    入力側の問題であることを本人に伝えるための口。
    """
    if storage_path:
        _require_key(x_gopipe_key, "Storage 上の図面の読み込み")
    import fitz  # PyMuPDF

    pdf = _save_upload(file, storage_path)
    if not Path(pdf).exists():
        raise HTTPException(status_code=400, detail="図面が見つかりません")

    doc = fitz.open(str(pdf))
    pages = doc.page_count
    text_chars = 0
    for i in range(min(pages, 20)):  # 先頭20ページで判定（大判一式でも即答するため）
        text_chars += len(doc[i].get_text() or "")
    doc.close()

    # 前処理（自動コントラスト＋鮮鋭化）が実際に効く環境かを見る。
    # 入っていないと手書き・スキャンの読み取りが素の画像のまま進むので、
    # 「対策したつもりで効いていない」を作らないため明示する。
    try:
        import PIL  # noqa: F401
        enhance_available = True
    except Exception:  # noqa: BLE001
        enhance_available = False

    has_text = text_chars >= 200
    if has_text:
        kind, advice = "cad", "テキスト層あり。機器表の数量を確定情報として使えます。"
    else:
        kind, advice = "scan", (
            "文字データがありません（スキャン図面・手書き図面・写真の可能性）。"
            "この場合、機器表の数量で裏を取れないため、数量はAIの読み取りだけが頼りになります。"
            "特に手書きの数字は読み違えが起きやすいので、表の数量は必ずご確認ください。"
            "CAD出力のPDFが用意できるなら、そちらの方が確実です。"
        )
    return {
        "pages": pages,
        "kind": kind,
        "has_text_layer": has_text,
        "text_chars": text_chars,
        "advice": advice,
        "enhance_available": enhance_available,
        # 1ページあたり十数秒〜。300秒の上限に対して危ないかを先に伝える
        "may_time_out": pages > 12,
    }


@app.delete("/learned")
async def revoke_learned(
    org_slug: str,
    raw: str,
    locale: str = "ja",
    x_gopipe_key: str | None = Header(default=None),
):
    """誤って覚えさせた言い換えを取り消す（論理削除・同じ内容を再度教えれば復活）。"""
    _require_key(x_gopipe_key, "学習の取り消し(/learned)")
    from gopipe_takeoff import store

    if not store.is_enabled():
        raise HTTPException(status_code=503, detail="Supabase 未設定")
    store.revoke_learned_alias(org_slug, raw, locale=locale)
    return {"ok": True, "raw": raw}
