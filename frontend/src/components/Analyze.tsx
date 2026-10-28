import { useEffect, useRef, useState } from 'react'
import type { AnalyzeRow } from '../types'
import { api } from '../api'
import { useToast } from '../toast'

export function Analyze() {
  const toast = useToast()
  const [query, setQuery] = useState('')
  const [maxResults, setMaxResults] = useState(5)
  const [rows, setRows] = useState<AnalyzeRow[] | null>(null)
  const [markdown, setMarkdown] = useState<string | null>(null)
  const [mdFile, setMdFile] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current)
  }, [])

  const poll = async (runId: string) => {
    try {
      const st = await api.analyzeStatus(runId)
      if (st.status === 'running') {
        timer.current = window.setTimeout(() => poll(runId), 3000)
        return
      }
      const res = await api.analyzeResults(runId)
      setRunning(false)
      if (res.status === 'error') {
        toast.show(res.error || 'Analysis failed', 'error')
        return
      }
      setRows(res.results)
      setMarkdown(res.markdown)
      setMdFile(res.md_file)
    } catch (err) {
      setRunning(false)
      toast.show((err as Error).message, 'error')
    }
  }

  const run = async () => {
    if (!query.trim()) {
      toast.show('Enter a query', 'error')
      return
    }
    setRows(null)
    setMarkdown(null)
    setMdFile(null)
    setRunning(true)
    try {
      const { run_id } = await api.analyzeStart(query.trim(), maxResults)
      poll(run_id)
    } catch (err) {
      setRunning(false)
      toast.show((err as Error).message, 'error')
    }
  }

  const downloadMd = () => {
    if (!markdown) return
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = mdFile || 'analysis.md'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <section>
      <div className="card">
        <p className="hint">
          Enter a search query (e.g. <em>“best HR software in Bangladesh”</em>). Claude
          web-searches Google, reports each site’s <strong>ranking position (1–100)</strong>, and
          extracts its on-page keywords. Runs in the background; results export to a Markdown file.
          Positions are the LLM’s best read of Google’s ranking — approximate, not guaranteed exact.
        </p>
        <div className="row">
          <label>
            Search query
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !running && run()}
              placeholder="best HR software in Bangladesh"
            />
          </label>
          <label>
            Max results
            <input
              type="number"
              min={1}
              max={10}
              value={maxResults}
              onChange={(e) => setMaxResults(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
            />
          </label>
          <button className="primary" onClick={run} disabled={running}>
            {running ? 'Analyzing…' : 'Analyze'}
          </button>
        </div>
      </div>

      {running && (
        <p className="empty">Searching + scraping in the background… this can take up to a minute.</p>
      )}
      {rows !== null && !running && rows.length === 0 && (
        <p className="empty">No results returned.</p>
      )}

      {rows && rows.length > 0 && (
        <div className="actions" style={{ marginBottom: 16 }}>
          <button onClick={downloadMd}>⬇ Download .md</button>
          {mdFile && <span className="hint">Saved on server: exports/{mdFile}</span>}
        </div>
      )}

      {(() => {
        const self = rows?.find((r) => r.is_self)
        if (!self) return null
        const ahead = rows!.filter((r) => !r.is_self && r.position < self.position).length
        return (
          <div className="card self-compare">
            <strong>{self.domain}</strong> ranks at position{' '}
            <span className="rank-badge">
              #{self.position}
              <span style={{ opacity: 0.7, fontWeight: 400 }}> / 100</span>
            </span>{' '}
            for this query — <b>{ahead}</b> competitor{ahead === 1 ? '' : 's'} rank above it.
          </div>
        )
      })()}

      {rows &&
        rows.map((r) => (
          <div className={'card' + (r.is_self ? ' self-row' : '')} key={`${r.position}-${r.url}`}>
            <div className="analyze-head">
              <span className="rank-badge" title="Google ranking position (out of 100)">
                #{r.position}
                <span style={{ opacity: 0.7, fontWeight: 400 }}> / 100</span>
              </span>
              <a href={r.url} target="_blank" rel="noreferrer" className="analyze-domain">
                {r.domain}
              </a>
              {r.is_self && <span className="badge self-badge">⭐ us</span>}
              {r.primary_keyword && <span className="badge">{r.primary_keyword}</span>}
              {r.word_count != null && <span className="hint">{r.word_count} words</span>}
            </div>
            {r.title && <div className="hint analyze-title">{r.title}</div>}
            {r.keywords.length > 0 ? (
              <div className="chips">
                {r.keywords.map((k) => (
                  <span className="chip" key={k.term}>
                    {k.term} <b>{k.count}</b> · {k.density}%
                  </span>
                ))}
              </div>
            ) : (
              <p className="hint">
                Couldn’t extract on-page keywords (page not reachable or JS-only).
              </p>
            )}
          </div>
        ))}
    </section>
  )
}
