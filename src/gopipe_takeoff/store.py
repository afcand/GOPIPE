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
