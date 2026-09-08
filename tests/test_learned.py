"""学習の堀（修正→辞書自動成長）のテスト。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "shared"))

from gopipe_takeoff.classifier import classify  # noqa: E402
from gopipe_takeoff.dictionary import TakeoffDictionary  # noqa: E402
from gopipe_takeoff.learned import load_aliases, record_alias  # noqa: E402
from gopipe_takeoff.models import TakeoffItem  # noqa: E402

DICT = ROOT / "prompts" / "dictionary.yaml"


def test_record_and_load(tmp_path):
    p = tmp_path / "learned.json"
    assert record_alias("全熱交換ユニット", "全熱交換器", category="機器", unit="台", path=p)
    assert not record_alias("同じ", "同じ", path=p)        # 変化なしは学習しない
    a = load_aliases(p)
    assert "全熱交換ユニット" in a
    assert a["全熱交換ユニット"]["canonical"] == "全熱交換器"


def test_learned_alias_improves_classification():
    # 未登録の表記ゆれを学習させると、分類が その他 → 正規名/カテゴリ になる
    raw = "ZZUNKNOWNITEMZZ"   # 辞書に無い・部分一致もしない表記
    d = TakeoffDictionary.from_yaml(DICT)
    before = classify([TakeoffItem(page=1, name=raw, quantity=1, unit="台")], d)
    assert before[0].category == "その他"

    d2 = TakeoffDictionary.from_yaml(DICT)
    d2.add_learned({raw: {"canonical": "全熱交換器", "category": "機器", "unit": "台", "raw": raw}})
    after = classify([TakeoffItem(page=1, name=raw, quantity=1, unit="台")], d2)
    assert after[0].name == "全熱交換器"
    assert after[0].category != "その他"


def test_locale_isolation(tmp_path):
    # 国ごとに堀を分離（en の学習は ja に混ざらない・その逆も）
    p = tmp_path / "l.json"
    record_alias("Air Handler", "Air Handling Unit", path=p, locale="en")
    record_alias("全熱交ユニット", "全熱交換器", path=p, locale="ja")
    en = load_aliases(p, locale="en", remote=False)
    ja = load_aliases(p, locale="ja", remote=False)
    assert "AirHandler" in en and "AirHandler" not in ja
    assert "全熱交ユニット" in ja and "全熱交ユニット" not in en


def test_learned_hint_few_shot_formatting():
    from gopipe_takeoff.extractor import _format_learned_hint
    ja = _format_learned_hint({"ぜんねつ": {"canonical": "全熱交換器", "category": "機器", "raw": "ぜんねつ"}})
    assert "全熱交換器" in ja and "ぜんねつ" in ja
    en = _format_learned_hint(
        {"ahu": {"canonical": "Air Handling Unit", "category": "Equipment", "raw": "ahu"}}, en=True)
    assert "Air Handling Unit" in en and "treat it as" in en
    assert _format_learned_hint({}) == ""


def test_load_aliases_remote_safe_when_supabase_off(tmp_path):
    # Supabase 未設定でも remote=True で落ちず、ローカルJSONを返す
    p = tmp_path / "l.json"
    record_alias("生表記X", "給水管", category="給水", unit="m", path=p)
    a = load_aliases(p, remote=True)
    assert "生表記X" in a and a["生表記X"]["canonical"] == "給水管"


def test_add_learned_creates_entry_for_unknown_canonical():
    d = TakeoffDictionary.from_yaml(DICT)
    d.add_learned({"謎部材A": {"canonical": "特注金物", "category": "雑材", "unit": "個", "raw": "謎部材A"}})
    out = classify([TakeoffItem(page=1, name="謎部材A", quantity=2, unit="個")], d)
    assert out[0].name == "特注金物" and out[0].category == "雑材"


def test_record_alias_survives_readonly_fs(monkeypatch, tmp_path):
    """サーバレス(読取専用FS)でも堀への学習を諦めない。

    ローカルJSONが書けないだけで例外を投げると、Supabase への永続まで道連れになり
    『本番だけ学習が効かない』が静かに起きる。ここが落ちたら堀が死ぬ。
    """
    import gopipe_takeoff.learned as L

    ro = tmp_path / "readonly" / "learned.json"

    def _boom(*a, **k):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(Path, "write_text", _boom)
    calls = []

    def _record(org, raw, canon, category, unit, locale="ja"):
        calls.append((org, raw, canon))
        return True

    monkeypatch.setattr("gopipe_takeoff.store.is_enabled", lambda: True)
    monkeypatch.setattr("gopipe_takeoff.store.record_learned_alias", _record)
    assert L.record_alias("全熱交ユニット", "全熱交換器", path=ro, org="haruki") is True
    assert calls and calls[0][2] == "全熱交換器"


def test_pipeline_merges_learned_aliases(monkeypatch, tmp_path):
    """会社が育てた別名が、次の拾い出しの分類に効くこと。

    ここが切れていると「直しても次回また同じ間違いが出る」＝堀が育たない。
    Streamlit 版では UI 側で辞書に混ぜていたため、エンジン単体では抜けていた。
    """
    from gopipe_takeoff.pipeline import TakeoffPipeline

    merged = {}

    def _fake_load_aliases(*a, **k):
        return {"全熱交ユニット": {"canonical": "全熱交換器", "category": "機器", "unit": "台"}}

    monkeypatch.setattr("gopipe_takeoff.learned.load_aliases", _fake_load_aliases)
    pipe = TakeoffPipeline()
    monkeypatch.setattr(
        pipe.dictionary, "add_learned", lambda a: merged.update(a) or len(a)
    )
    monkeypatch.setattr("gopipe_takeoff.pipeline.load_pdf", lambda p, grid=1, **kw: _EmptyDrawing())
    monkeypatch.setattr("gopipe_takeoff.pipeline.extract", lambda *a, **k: [])
    monkeypatch.setattr("gopipe_takeoff.pipeline.write_excel", lambda items, path, **kw: path)

    pipe.run("/nonexistent.pdf", tmp_path)
    assert "全熱交ユニット" in merged


class _EmptyDrawing:
    pages: list = []


def test_current_org_follows_request(monkeypatch):
    """会社の取り違えは他社の辞書を引くことになるので、env で明示的に切り替える。"""
    from gopipe_takeoff.learned import current_org

    monkeypatch.delenv("GOPIPE_ORG", raising=False)
    assert current_org() == "default"
    monkeypatch.setenv("GOPIPE_ORG", "haruki")
    assert current_org() == "haruki"


def test_company_wording_beats_builtin_dictionary():
    """会社が直した呼び方は、組み込み辞書の別名関係より優先されること。

    組み込み辞書は「ゲートバルブ」を「仕切弁」の別名として持つ。会社が
    「うちはゲートバルブと呼ぶ」と直したのに仕切弁へ戻されると、
    直した本人に「直しても無駄」と学習させてしまう＝定着が死ぬ。
    """
    d = TakeoffDictionary.from_yaml(DICT)
    assert d.lookup("ゲートバルブ").canonical == "仕切弁"  # 組み込みの向き

    d.add_learned(
        {"ゲートバルブ": {"canonical": "ゲートバルブ", "category": "弁類", "unit": "個",
                        "raw": "ゲートバルブ"}}
    )
    out = classify([TakeoffItem(page=1, name="ゲートバルブ", quantity=6, unit="個")], d)
    assert out[0].name == "ゲートバルブ"
    assert out[0].category == "弁類"


def test_teaching_one_valve_does_not_transform_another():
    """2種類目を教えても1種類目が消えないこと。

    旧実装は学習を組み込み索引へ直接書き込んでいたため、
    「AIが読んだ"BV-1"はうちではバタフライ弁」と教えた瞬間に、
    図面上の本物の仕切弁まで全部バタフライ弁に化けた。教えるほど堀が壊れる状態。
    """
    d = TakeoffDictionary.from_yaml(DICT)
    d.add_learned({
        "BV-1": {"canonical": "バタフライ弁", "category": "弁類", "unit": "個", "raw": "BV-1"},
        "CV-1": {"canonical": "逆止弁", "category": "弁類", "unit": "個", "raw": "CV-1"},
    })
    out = classify([
        TakeoffItem(page=1, name="BV-1", quantity=2, unit="個"),
        TakeoffItem(page=1, name="CV-1", quantity=3, unit="個"),
        TakeoffItem(page=1, name="仕切弁", quantity=5, unit="個"),
    ], d)
    assert [i.name for i in out] == ["バタフライ弁", "逆止弁", "仕切弁"]


def test_partial_match_never_renames():
    """部分一致で名前を書き換えないこと。

    「逆止弁 DN20」を辞書の部分一致で「仕切弁」に化けさせると、表が嘘になり、
    同じ名前が並んで二重計上チェックまで誤爆する。カテゴリは補ってよい。
    """
    d = TakeoffDictionary.from_yaml(DICT)
    out = classify([TakeoffItem(page=1, name="逆止弁 DN20 屋内", quantity=4, unit="個")], d)
    assert out[0].name == "逆止弁 DN20 屋内"   # AIの読みをそのまま残す
    assert out[0].category == "弁類"           # 分類だけは効く


def test_raw_name_is_preserved_for_learning():
    """分類で表示名が正規化されても、AIが読んだ生名称は残ること（学習の鍵）。"""
    d = TakeoffDictionary.from_yaml(DICT)
    out = classify([TakeoffItem(page=1, name="ゲートバルブ", quantity=1, unit="個")], d)
    assert out[0].name == "仕切弁"          # 表記ゆれは正規化される
    assert out[0].raw_name == "ゲートバルブ"  # 鍵は生の名前


def test_revoked_alias_does_not_survive_in_local_cache(monkeypatch, tmp_path):
    """取り消した言い換えが、ローカル控え経由で生き残らないこと。

    サーバレスの /tmp は温まったコンテナで残る。Supabase 側で取り消しても
    ローカルJSONを併合していると、次の実行でまだ効いてしまう
    （説明書動画の収録中に実際に踏んだ）。Supabaseがあるときはそちらだけを見る。
    """
    import gopipe_takeoff.learned as L

    stale = tmp_path / "learned.json"
    stale.write_text(
        '{"ja/バタフライ弁": {"canonical": "バタ弁", "raw": "バタフライ弁"}}', encoding="utf-8"
    )
    monkeypatch.setattr("gopipe_takeoff.store.is_enabled", lambda: True)
    monkeypatch.setattr("gopipe_takeoff.store.load_learned_aliases", lambda org, locale="ja": {})

    assert L.load_aliases(stale, remote=True) == {}          # 取り消し済み＝出てこない
    assert "バタフライ弁" in L.load_aliases(stale, remote=False)  # 単独運用では従来どおり
