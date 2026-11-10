/** UUID 列：等宽字体 + 截断，完整值放 title。 */
export function MonoId({ value }: { value: string }) {
  return (
    <span className="font-mono text-xs text-gray-700" title={value}>
      {value.length > 12 ? `${value.slice(0, 8)}…` : value}
    </span>
  )
}
