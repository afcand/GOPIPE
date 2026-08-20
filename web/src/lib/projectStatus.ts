/** 物件の進み具合。DBの check 制約（0002_projects.sql）と必ず一致させること。 */
export const PROJECT_STEPS = [
  { key: "draft", label: "下書き", hint: "図面を入れる前" },
  { key: "takeoff", label: "拾い出し", hint: "明細ができた" },
  { key: "estimated", label: "見積", hint: "金額を出した" },
  { key: "submitted", label: "提出", hint: "客先へ出した" },
  { key: "done", label: "完了", hint: "受注・完工" },
] as const;

export type ProjectStatus = (typeof PROJECT_STEPS)[number]["key"];

export function stepIndex(status: string | null | undefined): number {
  const i = PROJECT_STEPS.findIndex((s) => s.key === status);
  return i < 0 ? 0 : i;
}

export function stepLabel(status: string | null | undefined): string {
  return PROJECT_STEPS[stepIndex(status)].label;
}
