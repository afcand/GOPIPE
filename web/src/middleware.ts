import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

/** ログインセッションのクッキーを更新し続ける（切れたまま使えなくなるのを防ぐ）。 */
export async function middleware(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll: (list) => {
          list.forEach(({ name, value }) => request.cookies.set(name, value));
          response = NextResponse.next({ request });
          list.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  await supabase.auth.getUser();
  return response;
}

export const config = {
  // 公開デモ(/)と静的ファイルは素通り。認証が要る画面とAPIだけ通す。
  // 新しいAPIを足したらここにも足すこと。忘れるとセッションが更新されず、
  // 週1しか開かない人がある日突然ログインを失う。
  matcher: [
    "/app/:path*",
    "/login",
    "/api/run",
    "/api/page",
    "/api/pick",
    "/api/learn",
    "/api/items",
    "/api/items/rows",
    "/api/dictionary",
    "/api/color-meanings",
    "/api/signup",
  ],
};
