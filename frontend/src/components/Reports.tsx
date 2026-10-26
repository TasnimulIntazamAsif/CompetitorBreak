import { useState } from 'react'
import type { Competitor, Row } from '../types'
import { api } from '../api'
import { useToast } from '../toast'
import { DataTable } from './DataTable'

type ReportType = 'topics' | 'posts' | 'gaps' | 'overlap' | 'rankings' | 'serp' | 'density'

const REPORT_OPTIONS: { id: ReportType; label: string }[] = [
  { id: 'topics', label: 'Top target keywords' },
  { id: 'posts', label: 'Posts & primary keyword' },
  { id: 'density', label: 'Keyword density (per post)' },
  { id: 'gaps', label: "Content gaps (they target, we don't)" },
  { id: 'overlap', label: 'Shared targets (head-to-head)' },
  { id: 'rankings', label: 'Google rankings (domain)' },
  { id: 'serp', label: 'SERP for a keyword' },
]

const COLS: Record<ReportType, string[]> = {
  topics: ['term', 'posts', 'as_primary'],
  posts: ['primary_keyword', 'kw_count', 'kw_density', 'word_count', 'published_at', 'title', 'url'],
  density: ['count', 'density', 'word_count', 'title', 'url'],
  gaps: ['term', 'posts', 'competitors'],
  overlap: ['term', 'their_posts'],
  rankings: ['keyword', 'position', 'url', 'captured_at'],
  serp: ['position', 'domain', 'tracked', 'url', 'title'],
}

export function Reports({ competitors }: { competitors: Competitor[] }) {
  const toast = useToast()
  const [type, setType] = useState<ReportType>('topics')
  const [domain, setDomain] = useState('')
  const [keyword, setKeyword] = useState('')
  const [limit, setLimit] = useState(50)
  const [rows, setRows] = useState<Row[] | null>(null)

  const needsKeyword = type === 'serp' || type === 'density'
  const needsDomain = type !== 'serp'

  const run = async () => {
    let path: string
    if (type === 'gaps') {
      path = `/api/reports/gaps?limit=${limit}` + (domain ? `&competitor=${encodeURIComponent(domain)}` : '')
    } else if (type === 'serp') {
      if (!keyword.trim()) {
        toast.show('Enter a keyword', 'error')
        return
      }
      path = `/api/reports/serp?keyword=${encodeURIComponent(keyword.trim())}&limit=${limit}`
    } else if (type === 'density') {
      if (!keyword.trim()) {
        toast.show('Enter a keyword', 'error')
        return
      }
      path = `/api/reports/density?domain=${encodeURIComponent(domain)}&keyword=${encodeURIComponent(keyword.trim())}&limit=${limit}`
    } else {
      path = `/api/reports/${type}?domain=${encodeURIComponent(domain)}&limit=${limit}`
    }
    try {
      setRows(await api.report(path))
    } catch (err) {
      toast.show((err as Error).message, 'error')
    }
  }

  return (
    <section>
      <div className="card">
        <div className="row">
          <label>
            Report
            <select value={type} onChange={(e) => setType(e.target.value as ReportType)}>
              {REPORT_OPTIONS.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          {needsDomain && (
            <label style={{ opacity: type === 'gaps' ? 0.6 : 1 }}>
              Domain
              <select value={domain} onChange={(e) => setDomain(e.target.value)}>
                {type === 'gaps' && <option value="">All competitors</option>}
                {competitors.map((c) => (
                  <option key={c.id} value={c.domain}>
                    {c.domain}
                  </option>
                ))}
              </select>
            </label>
          )}
          {needsKeyword && (
            <label>
              Keyword
              <input
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="hr software"
              />
            </label>
          )}
          <label>
            Limit
            <input
              type="number"
              min={1}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
            />
          </label>
          <button className="primary" onClick={run}>
            Run report
          </button>
        </div>
      </div>

      {rows !== null && (
        <DataTable
          cols={COLS[type]}
          rows={rows}
          numeric={['count', 'density', 'word_count', 'kw_count', 'kw_density', 'position', 'posts', 'as_primary', 'their_posts']}
          empty="No results. Run a collect/SERP first, or widen the limit."
        />
      )}
    </section>
  )
}
