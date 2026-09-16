"""Supabase 永続化（任意）。

`SUPABASE_URL` と `SUPABASE_SERVICE_ROLE_KEY` が設定されているときだけ有効化される。
PostgREST へ service_role で書き込む（RLS をバイパスするため、このキーは
**サーバ側専用**。クライアント/ブラウザに出さない・リポジトリにコミットしない）。

依存追加なし（標準ライブラリ urllib のみ）＝ Vercel バンドルを増やさない。
認証/テナントは当面 org_slug 指定（将来フロントの Supabase Auth に置換予定）。
"""
from __future__ import annotations

import json
import os
import urllib.error
import unicodedata
import urllib.parse
import urllib.request

from .models import TakeoffItem

_TIMEOUT = 15


def _conf() -> tuple[str, str] | None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        return url.rstrip("/"), key
    return None


def is_enabled() -> bool:
    """永続化が設定されているか（env 2 つが揃っているか）。"""
    return _conf() is not None


def _req(method: str, path: str, *, body=None, prefer: str = "", params: str = ""):
    conf = _conf()
    if conf is None:
        raise RuntimeError("Supabase is not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY)")
    base, key = conf
    url = f"{base}/rest/v1/{path}{params}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Supabase {method} {path} -> HTTP {e.code}: {detail}") from None
    return json.loads(raw) if raw else None


def ensure_org(slug: str, name: str) -> str:
    """organizations を slug で引き、無ければ作って org_id を返す。

    既存の会社名は上書きしない。upsert にすると、API 呼び出しのたびに
    name が slug（例: "haruki"）で塗り潰され、画面に出る会社名が
    「株式会社ハルキ」から崩れてしまうため。
    """
    rows = _req("GET", "organizations", params=f"?slug=eq.{urllib.parse.quote(slug)}&select=id")
    if rows:
        return rows[0]["id"]
    rows = _req(
        "POST", "organizations",
        body=[{"slug": slug, "name": name or slug}],
        prefer="resolution=merge-duplicates,return=representation",
        params="?on_conflict=slug",
    )
    return rows[0]["id"]


def upsert_project(org_id: str, slug: str, title: str, item_count: int,
                   source_pdf_path: str | None = None) -> str:
    """projects を (org_id, slug) で upsert し、project_id を返す。"""
    row = {
        "org_id": org_id, "slug": slug, "title": title or slug,
        "status": "takeoff", "item_count": item_count,
    }
    if source_pdf_path:  # どの図面から起こしたかを案件に残す
        row["source_pdf_path"] = source_pdf_path
    rows = _req(
        "POST", "projects",
        body=[row],
        prefer="resolution=merge-duplicates,return=representation",
        params="?on_conflict=org_id,slug",
    )
    return rows[0]["id"]


def create_drawing(
    org_id: str, project_id: str, storage_path: str, *,
    file_name: str = "", page_count: int | None = None, warnings: list[str] | None = None,
) -> str:
    """図面を1枚登録して drawing_id を返す。明細はこの図面にぶら下げる。"""
    rows = _req(
        "POST", "drawings",
        body=[{
            "org_id": org_id, "project_id": project_id, "storage_path": storage_path,
            "file_name": file_name or storage_path.rsplit("/", 1)[-1],
            "page_count": page_count,
            "status": "partial" if warnings else "done",
            "warnings": warnings or [],
        }],
        prefer="return=representation",
    )
    return rows[0]["id"]


def replace_takeoff_items(
    project_id: str, org_id: str, items: list[TakeoffItem],
    drawing_id: str | None = None, append: bool = False,
) -> int:
    """明細を保存する。

    drawing_id があるときは **その図面ぶんだけ** 置き換える。案件単位で全消しすると、
    同じ物件に2枚目の図面を流した瞬間に1枚目の拾い出し（と、そこに入っていた
    人の修正）が警告なく消える。
    さらに 0件のときは削除しない。AI側の一時障害で0件が返ったときに、
    前回の結果まで道連れにしないため。

    🔴 append=True は何も消さずに足すだけ。現地実測を **既存の物件へ足す** ときに使う。
    実測は図面を持たない（drawing_id が無い）ので、置き換えにすると
    `drawing_id is null` の行が全部消える＝**人が手で足した行が警告なく消える**
    （/api/items/rows で足した行は drawing_id を持たない）。
    """
    if not items:
        return 0
    if append:
        pass  # 何も消さない
    elif drawing_id:
        _req("DELETE", "takeoff_items", params=f"?drawing_id=eq.{drawing_id}")
    else:
        # 旧経路（図面を作らない呼び出し）。図面未指定の行だけを入れ替える。
        _req(
            "DELETE", "takeoff_items",
            params=f"?project_id=eq.{project_id}&drawing_id=is.null",
        )
    body = [
        {
            "project_id": project_id, "org_id": org_id, "drawing_id": drawing_id,
            "color": it.color, "color_hue": it.color_hue,
            "category": it.category, "name": it.name, "spec": it.spec,
            "location": it.location, "quantity": it.quantity,
            "unit": it.unit, "confidence": it.confidence,
            "page": it.page, "source": it.source,
            "raw_name": it.raw_name or it.name, "qty_vision": it.qty_vision,
            "status": "ai_draft",
        }
        for it in items
    ]
    _req("POST", "takeoff_items", body=body, prefer="return=minimal")
    return len(items)


def persist_takeoff(
    *, org_slug: str, org_name: str, project_slug: str, title: str,
    items: list[TakeoffItem], source_pdf_path: str | None = None,
    file_name: str | None = None, warnings: list[str] | None = None,
    append: bool = False,
) -> dict:
    """org → project → drawing → takeoff_items を保存し、id 群と件数を返す。"""
    org_id = ensure_org(org_slug, org_name)
    project_id = upsert_project(org_id, project_slug, title, len(items), source_pdf_path)
    drawing_id = None
    if source_pdf_path:
        drawing_id = create_drawing(
            org_id, project_id, source_pdf_path,
            file_name=file_name or "",
            page_count=max((it.page for it in items), default=None),
            warnings=warnings,
        )
    n = replace_takeoff_items(project_id, org_id, items, drawing_id, append=append)
    total = _project_item_count(project_id)
    if total is not None:
        # 一覧に出るのは「その物件の合計」。直近1回の件数を出すと、
        # 2枚目を足したのに件数が減ったように見える。
        _req(
            "PATCH", "projects",
            body={"item_count": total, "updated_at": "now()"},
            params=f"?id=eq.{project_id}", prefer="return=minimal",
        )
    return {
        "org_id": org_id, "project_id": project_id,
        "drawing_id": drawing_id, "items": n, "project_items": total,
    }


def _project_item_count(project_id: str) -> int | None:
    """その案件の明細総数（図面をまたいだ合計）。"""
    try:
        rows = _req("GET", "takeoff_items", params=f"?project_id=eq.{project_id}&select=id")
        return len(rows or [])
    except Exception:  # noqa: BLE001  件数の更新失敗で保存自体は落とさない
        return None


def record_learned_alias(
    org_slug: str, raw: str, canonical: str,
    category: str | None = None, unit: str | None = None, locale: str = "ja",
) -> bool:
    """learned_aliases を (org_id, locale, raw) で upsert（service_role・ロケール別の堀の永続化）。"""
    org_id = ensure_org(org_slug, org_slug)
    # 既存があれば hits を伸ばす。どの別名が現場で効いているかの順位付けに使う
    # （抽出プロンプトへ載せる優先順・辞書ページの並び）。
    existing = _req(
        "GET", "learned_aliases",
        params=(
            f"?org_id=eq.{org_id}&locale=eq.{urllib.parse.quote(locale)}"
            f"&raw=eq.{urllib.parse.quote(raw)}&select=id,hits"
        ),
    )
    hits = (existing[0].get("hits") or 1) + 1 if existing else 1
    _req(
        "POST", "learned_aliases",
        body=[{"org_id": org_id, "raw": raw, "canonical": canonical,
               "category": category, "unit": unit, "locale": locale,
               "hits": hits, "updated_at": "now()", "revoked_at": None}],
        prefer="resolution=merge-duplicates,return=minimal",
        params="?on_conflict=org_id,locale,raw",
    )
    return True


def load_learned_aliases(org_slug: str, locale: str = "ja") -> dict:
    """org × locale の learned_aliases を {raw: {canonical, category, unit, raw}} で返す。"""
    org_id = ensure_org(org_slug, org_slug)
    # order を明示しないと、抽出プロンプトに載る先頭N件が実行ごとに変わり
    # 「昨日は直ったのに今日は戻る」になる。よく使われている別名から順に。
    rows = _req(
        "GET", "learned_aliases",
        params=(
            f"?org_id=eq.{org_id}&locale=eq.{locale}&revoked_at=is.null"
            "&select=raw,canonical,category,unit,hits"
            "&order=hits.desc,updated_at.desc&limit=2000"
        ),
    )
    out: dict = {}
    for r in (rows or []):
        out[r["raw"]] = {
            "canonical": r.get("canonical"), "category": r.get("category"),
            "unit": r.get("unit"), "raw": r.get("raw"), "hits": r.get("hits") or 1,
        }
    return out


def load_suppressions(org_slug: str, *, min_hits: int = 2, limit: int = 200) -> list[dict]:
    """人が「要らない」と消した行を集計して返す＝拾わないことの学習。

    なぜ削除を学ぶのか:
      名称の直しは「Xと読んだがYだ」という言い換えで、打つ手間がかかる。
      削除はワンクリックで、しかも **その行が要るか要らないか** という
      積算で最も効く判断そのもの。入力を増やさずに一番強い信号が取れる。

    🔴 1回の削除では効かせない（min_hits=2）。誤って消したときや、
    その物件限りの事情で消したときに、次から拾わなくなると
    「無かったことになる」＝拾い出しで最悪の失敗を起こす。
    **別々の物件で2回以上**消されたものだけを候補にする。

    返すのは候補であって、消す指示ではない。抽出側はこれをプロンプトで
    「拾わない」と教えるだけで、出てきた行を黙って捨てはしない。
    """
    org_id = ensure_org(org_slug, org_slug)
    rows = _req(
        "GET", "takeoff_corrections",
        params=(
            f"?org_id=eq.{org_id}&kind=eq.removed&select=before,project_id"
            "&order=created_at.desc&limit=3000"
        ),
    )
    agg: dict[str, dict] = {}
    for r in (rows or []):
        before = r.get("before") or {}
        name = (before.get("raw_name") or before.get("name") or "").strip()
        if not name:
            continue
        key = unicodedata.normalize("NFKC", name).replace(" ", "").replace("　", "")
        cur = agg.setdefault(key, {"name": name, "hits": 0, "projects": set(), "specs": set()})
        cur["hits"] += 1
        if r.get("project_id"):
            cur["projects"].add(r["project_id"])
        if before.get("spec"):
            cur["specs"].add(str(before["spec"])[:24])
    out = [
        {
            "name": v["name"], "hits": v["hits"],
            "projects": len(v["projects"]), "specs": sorted(v["specs"])[:3],
        }
        for v in agg.values()
        # 同じ物件で連打しただけのものを学ばない。物件をまたいで消されて初めて
        # 「この会社では要らない」と言える。
        if v["hits"] >= min_hits and len(v["projects"]) >= min_hits
    ]
    out.sort(key=lambda x: (-x["hits"], x["name"]))
    return out[:limit]


def load_color_meanings(org_slug: str) -> dict[str, dict]:
    """会社が決めた「色 → 意味」。{色: {meaning, note}}。

    🔴 図面の色の意味は会社ごと・図面ごとにしか決まらない
    （実測: 5枚で「ダクト」が5通りの色。凡例があったのは1枚だけ）。
    共通辞書に流用すると「既存を新設で拾う」事故になるので、必ず org で閉じる。
    """
    org_id = ensure_org(org_slug, org_slug)
    rows = _req(
        "GET", "color_meanings",
        params=f"?org_id=eq.{org_id}&select=color,meaning,note&order=color",
    )
    return {
        r["color"]: {"meaning": r.get("meaning"), "note": r.get("note")}
        for r in (rows or [])
    }


def save_color_meaning(org_slug: str, color: str, meaning: str, note: str | None = None) -> bool:
    """色の意味を1件覚える（同じ色は上書き）。"""
    org_id = ensure_org(org_slug, org_slug)
    _req(
        "POST", "color_meanings",
        body=[{"org_id": org_id, "color": color, "meaning": meaning,
               "note": note, "updated_at": "now()"}],
        prefer="resolution=merge-duplicates,return=minimal",
        params="?on_conflict=org_id,color",
    )
    return True


def delete_color_meaning(org_slug: str, color: str) -> bool:
    org_id = ensure_org(org_slug, org_slug)
    _req("DELETE", "color_meanings",
         params=f"?org_id=eq.{org_id}&color=eq.{urllib.parse.quote(color)}")
    return True


def seen_colors(org_slug: str, limit: int = 4000) -> list[dict]:
    """この会社の図面に実際に出てきた色と件数。

    何色が出るか分からないと「何を聞けばよいか」も決まらない。
    人に出す質問は、実在する色の分だけにする（実験Gの「人へ1問だけ出す」）。
    """
    org_id = ensure_org(org_slug, org_slug)
    rows = _req(
        "GET", "takeoff_items",
        params=f"?org_id=eq.{org_id}&color=not.is.null&select=color,color_hue&limit={limit}",
    )
    agg: dict[str, dict] = {}
    for r in (rows or []):
        c = r.get("color")
        if not c:
            continue
        cur = agg.setdefault(c, {"color": c, "count": 0, "hues": []})
        cur["count"] += 1
        if r.get("color_hue") is not None:
            cur["hues"].append(float(r["color_hue"]))
    out = []
    for v in agg.values():
        hues = v.pop("hues")
        v["hue"] = round(sum(hues) / len(hues), 1) if hues else None
        out.append(v)
    out.sort(key=lambda x: -x["count"])
    return out


def download_drawing(storage_path: str) -> bytes:
    """Storage バケット drawings から設備図PDFを取ってくる（service_role）。

    Vercel のリクエストボディ上限(4.5MB)を避けるため、ブラウザは Storage へ直接
    アップロードし、API にはこのパスだけが渡る。パスの先頭は組織 slug。
    """
    conf = _conf()
    if conf is None:
        raise RuntimeError("Supabase is not configured")
    base, key = conf
    safe = storage_path.lstrip("/")
    if ".." in safe:
        raise RuntimeError("不正なパスです")
    url = f"{base}/storage/v1/object/drawings/{urllib.parse.quote(safe)}"
    req = urllib.request.Request(
        url, method="GET",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"図面の取得に失敗しました (HTTP {e.code}): {detail}") from None


def revoke_learned_alias(org_slug: str, raw: str, locale: str = "ja") -> bool:
    """覚えさせた言い換えを取り消す（論理削除）。

    物理削除にしないのは、押し間違い1回で会社が育てた資産を永久に失わせないため。
    同じ raw をもう一度教えれば取り消しは自動で解除される。
    """
    org_id = ensure_org(org_slug, org_slug)
    _req(
        "PATCH", "learned_aliases",
        body={"revoked_at": "now()"},
        params=(
            f"?org_id=eq.{org_id}&locale=eq.{urllib.parse.quote(locale)}"
            f"&raw=eq.{urllib.parse.quote(raw)}"
        ),
        prefer="return=minimal",
    )
    return True

# --- 指した指示を覚える（箇所・色） ------------------------------------------


def save_pick_instruction(
    org_slug: str, *, kind: str, ref: str, payload: dict,
    sheet_key: str = "", note: str | None = None,
) -> bool:
    """人が指した指示を1件覚える（同じ鍵は上書き）。

    kind='region' は「この図面の型のここを拾う」、kind='color' は「この色はこう拾う」。
    共通辞書には流用しない。色の意味と同じく、会社ごとにしか決まらないため。
    """
    org_id = ensure_org(org_slug, org_slug)
    _req(
        "POST", "pick_instructions",
        body=[{"org_id": org_id, "kind": kind, "sheet_key": sheet_key or "",
               "ref": ref, "payload": payload, "note": note, "updated_at": "now()"}],
        prefer="resolution=merge-duplicates,return=minimal",
        params="?on_conflict=org_id,kind,sheet_key,ref",
    )
    return True


def load_pick_instructions(org_slug: str, *, kind: str = "", sheet_key: str | None = None) -> list[dict]:
    """覚えている指示を返す。

    sheet_key を渡すと「その図面の型」と「全図面向け（空文字）」の両方を返す。
    図面ごとの指示だけにすると、会社共通の決め事（この色は既存流用、等）が毎回消える。
    """
    org_id = ensure_org(org_slug, org_slug)
    q = f"?org_id=eq.{org_id}&select=kind,sheet_key,ref,payload,hits,note&order=updated_at.desc"
    if kind:
        q += f"&kind=eq.{kind}"
    if sheet_key is not None:
        key = urllib.parse.quote(sheet_key or "")
        q += f"&or=(sheet_key.eq.{key},sheet_key.eq.)"
    return _req("GET", "pick_instructions", params=q) or []


def delete_pick_instruction(org_slug: str, *, kind: str, ref: str, sheet_key: str = "") -> bool:
    org_id = ensure_org(org_slug, org_slug)
    _req("DELETE", "pick_instructions",
         params=(f"?org_id=eq.{org_id}&kind=eq.{kind}"
                 f"&sheet_key=eq.{urllib.parse.quote(sheet_key or '')}"
                 f"&ref=eq.{urllib.parse.quote(ref)}"))
    return True
