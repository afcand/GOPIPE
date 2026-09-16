import { redirect } from "next/navigation";
import { currentMembership } from "@/lib/supabase/server";
import Picker from "./Picker";

export default async function PickPage() {
  const me = await currentMembership();
  if (!me) redirect("/login");
  if (!me.org) redirect("/app");
  return <Picker orgSlug={me.org.slug} orgName={me.org.name} />;
}
