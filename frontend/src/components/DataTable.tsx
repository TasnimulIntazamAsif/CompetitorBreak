import type { Row } from '../types'

function fmt(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (Array.isArray(v)) return v.join(', ')
  return String(v)
}

export function DataTable({
  cols,
  rows,
  empty,
  numeric = [],
}: {
  cols: string[]
  rows: Row[]
  empty: string
  numeric?: string[]
}) {
  if (!rows.length) return <p className="empty">{empty}</p>
  return (
    <table>
      <thead>
        <tr>
          {cols.map((c) => (
            <th key={c} className={numeric.includes(c) ? 'num' : undefined}>
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {cols.map((c) => (
              <td key={c} className={numeric.includes(c) ? 'num' : undefined}>
                {fmt(r[c])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
