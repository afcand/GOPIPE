-- 0009_color_meanings.sql — 図面の色が何を意味するかを、会社ごとに覚える
--
-- 設備図は色で系統/用途（既存再利用・移設・新設、SA/RA/OA/EA）を分ける。
-- 取り違えると数量が合っていても見積が丸ごと狂う＝数量より重い誤り。
--
-- 🔴 色の意味は会社ごと・図面ごとにしか決まらない。
-- 実測(2026-08-20 ハルキ実図面5枚): 「ダクト」が5枚で5通りの色だった。
-- 凡例があったのは1枚だけで、しかも色見本でなく「色つき文字＋引出線」。
-- さらにその凡例の緑「(移設)」に対応する作図要素は本体に0px（凡例にあっても実体が無い）。
-- → 凡例の自動発見に頼らず、会社に一度聞いて辞書に持つ。共通辞書に流用すると事故る。

create table if not exists color_meanings (
  id         uuid primary key default uuid_generate_v4(),
  org_id     uuid not null references organizations(id) on delete cascade,
  color      text not null,            -- 青 / 橙茶 など（色相の窓の名前）
  meaning    text not null,            -- 既存再利用 / 移設 / 新設 など会社の言葉
  note       text,                     -- 「この現場だけ」等の但し書き
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (org_id, color)
);
create index if not exists idx_color_meanings_org on color_meanings(org_id);

alter table color_meanings enable row level security;
drop policy if exists color_meanings_all on color_meanings;
create policy color_meanings_all on color_meanings
  for all using      (org_id in (select current_org_ids()))
          with check (org_id in (select current_org_ids()));

-- 拾い出しの明細に、機械で実測した色を残す。
-- 何色が実際に出てくるかが分からないと「何を聞けばよいか」も決められない
-- （＝辞書の口に出す選択肢が作れない）。
alter table takeoff_items add column if not exists color     text;
alter table takeoff_items add column if not exists color_hue real;
