/**
 * 类名合并工具（shadcn/React Bits 组件约定入口）。
 * 项目未使用 Tailwind，无需 tailwind-merge，仅做真值过滤拼接。
 */
export function cn(
  ...classes: Array<string | false | null | undefined>
): string {
  return classes.filter(Boolean).join(" ");
}
