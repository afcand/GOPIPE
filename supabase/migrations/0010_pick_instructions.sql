-- 0010_pick_instructions.sql — 人が「指した指示」を会社ごとに覚える
--
-- 2026-09-16 に「指して拾う」を出したが、指示はその場限りで消えていた。
-- 同じ様式の図面が次に来ても、また囲み直し・また色を指し直しになる。
-- 拾い出しの堀は「この会社はここをこう拾う」が溜まることで効くので、指示を残す。
--
-- 🔴 色の意味は会社ごと・図面ごとにしか決まらない（実測: 5枚で「ダクト」が5通りの色）。
-- 共通辞書へ流用しない。必ず org で閉じる。color_meanings と同じ掟。
--
-- sheet_key は「図面の型」。図枠の文字から作る署名で、同じ様式の紙（M-001-01〜08 など）は
-- 同じ鍵になる。空文字は「この会社の全図面」を意味する。

create table if not exists pick_instructions (
  -- 🔴 uuid_generate_v4() は uuid-ossp 拡張の関数で、実行する役割の search_path に
  -- extensions スキーマが無いと落ちる（2026-09-16 に db push がここで失敗）。
  -- gen_random_uuid() は Postgres 本体の関数なので、どの役割からでも通る。
  id         uuid primary key default gen_random_uuid(),
  org_id     uuid not null references organizations(id) on delete cascade,
  kind       text not null check (kind in ('region', 'color')),
  sheet_key  text not null default '',
  -- 同じ指示を二重に覚えないための短い鍵。region は丸めた座標、color は色。
  ref        text not null,
  -- region: {page,x0,y0,x1,y1,label} / color: {hex,name,category,unit,action}
  payload    jsonb not null,
  -- 使われた回数。溜めるだけで使われない指示を見分けるため。
  hits       int not null default 0,
  note       text,
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (org_id, kind, sheet_key, ref)
);
create index if not exists idx_pick_instructions_org on pick_instructions(org_id, kind, sheet_key);

alter table pick_instructions enable row level security;
drop policy if exists pick_instructions_all on pick_instructions;
create policy pick_instructions_all on pick_instructions
  for all using      (org_id in (select current_org_ids()))
          with check (org_id in (select current_org_ids()));
