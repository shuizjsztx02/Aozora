interface PlaceholderPageProps {
  title: string
  /** 交付该页面的子卡编号。 */
  task: string
  description: string
}

/** 未交付页面的显式占位——本卡不实现回放（8-07）与导出（8-09）。 */
export function PlaceholderPage({ title, task, description }: PlaceholderPageProps) {
  return (
    <section className="rounded-lg border border-dashed border-gray-300 bg-white p-8 text-center">
      <h1 className="text-lg font-semibold text-gray-900">{title}</h1>
      <p className="mt-2 text-sm text-gray-600">{description}</p>
      <p className="mt-4 text-xs text-gray-400">该页面由 T-M8 的 {task} 交付，当前为占位。</p>
    </section>
  )
}
