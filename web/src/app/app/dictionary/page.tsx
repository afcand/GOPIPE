import { redirect } from "next/navigation";
import Link from "next/link";
import { currentMembership } from "@/lib/supabase/server";
import DictionaryList from "./DictionaryList";
import SuppressionList from "./SuppressionList";
import ColorMeanings from "./ColorMeanings";

export const dynamic = "force-dynamic";

export default async function DictionaryPage() {
  const me = await currentMembership();
  if (!me) redirect("/login");
  if (!me.org) redirect("/app");

  return (
    <main className="mx-auto w-full max-w-[900px] px-5 pb-24">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] py-6">
        <div>
          <p className="m-0 text-[12px] font-bold tracking-[0.3em] text-[var(--cyan)]">GOPIPE</p>
          <p className="m-0 text-[20px] font-black">{me.org.name}が覚えた言い換え</p>
        </div>
        <span className="flex items-center gap-4">
          <Link href="/app/guide" className="text-[13.5px] font-bold text-[var(--cyan)] hover:underline">
            使い方
          </Link>
          <Link href="/app" className="text-[14px] font-bold text-[var(--cyan)] hover:underline">
            ← 拾い出しに戻る
          </Link>
        </span>
      </header>

      <section className="py-8">
        <p className="mt-0 mb-6 max-w-[62ch] text-[14px] text-[var(--mut)]">
          「この直しを覚えさせる」で貯まった、この会社だけの言い換えです。
          使うほど増え、次からAIがこの名前で出すようになります。
          間違えて覚えさせたものは取り消せます（同じ直しをもう一度すれば戻ります）。
        </p>
        <DictionaryList />
      </section>

      <section className="border-t border-[var(--line)] py-8">
        <p className="m-0 mb-1 text-[18px] font-black">図面の色の意味</p>
        <p className="mt-0 mb-6 max-w-[62ch] text-[14px] text-[var(--mut)]">
          設備図は色で区別します（既存再利用・移設・新設、系統の違い）。
          ここを一度決めておくと、次から拾い出し表の「図面の色」欄に意味が入ります。
          <strong className="text-[var(--ink)]">色の意味は会社ごと・図面ごとに違う</strong>ので、
          AIには推測させません（ハルキ様の実図面5枚では「ダクト」が5通りの色でした）。
          出てくるのは、実際に御社の図面に描かれていた色だけです。
        </p>
        <ColorMeanings />
      </section>

      <section className="border-t border-[var(--line)] py-8">
        <p className="m-0 mb-1 text-[18px] font-black">拾わないもの</p>
        <p className="mt-0 mb-6 max-w-[62ch] text-[14px] text-[var(--mut)]">
          要らない行を消すと、その判断もこの会社の資産になります。
          <strong className="text-[var(--ink)]">別々の物件で2回以上</strong>消された品目は、
          次からAIが原則として拾わなくなります。
          ただし今回の図面で明らかに工事対象なら、AIは拾ったうえで「要確認」の印を付けます
          （黙って落とすと、必要な品目が二度と表に出なくなるため）。
        </p>
        <SuppressionList />
      </section>
    </main>
  );
}
