import { useEffect, useRef, useState } from 'react'
import type { CompetitorKeywordRow } from '../types'
import { api } from '../api'
import { useToast } from '../toast'

export function CompetitorKeywords() {
  const toast = useToast()
  const [domain, setDomain] = useState('')
  const [maxKeywords, setMaxKeywords] = useState(15)
  const [rows, setRows] = useState<CompetitorKeywordRow[] | null>(null)
  const [markdown, setMarkdown] = useState<string | null>(null)
  const [mdFile, setMdFile] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current)
  }, [])

  const poll = async (runId: string) => {
    try {
      const st = await api.ckwStatus(runId)
      if (st.status === 'running') {
        timer.current = window.setTimeout(() => poll(runId), 3000)
        return
      }
      const res = await api.ckwResults(runId)
      setRunning(false)
      if (res.status === 'error') {
        toast.show(res.error || 'Keyword check failed', 'error')
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
    if (!domain.trim()) {
      toast.show('Enter a domain', 'error')
      return
    }
    setRows(null)
    setMarkdown(null)
    setMdFile(null)
    setRunning(true)
    try {
      const { run_id } = await api.ckwStart(domain.trim(), maxKeywords)
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
    a.download = mdFile || 'competitor-keywords.md'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <section>
      <div className="card">
        <p className="hint">
          Enter a website (e.g. <em>ibos.io</em>). Claude web-searches which keywords that
          domain ranks for, each with its <strong>approximate position</strong>, the ranking
          page type, and that page’s keyword density. Runs in the background (~1–2 min).
          Positions are the LLM’s approximate read of Google, not exact rank-tracker data.
        </p>
        <div className="row">
          <label>
            Website / domain
            <input
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !running && run()}
              placeholder="ibos.io"
            />
          </label>
          <label>
            Max keywords
            <input
              type="number"
              min={1}
              max={30}
              value={maxKeywords}
              onChange={(e) =>
                setMaxKeywords(Math.max(1, Math.min(30, Number(e.target.value) || 1)))
              }
            />
          </label>
          <button className="primary" onClick={run} disabled={running}>
            {running ? 'Checking…' : 'Check keywords'}
          </button>
        </div>
      </div>

      {running && (
        <p className="empty">
          Asking Claude + scraping ranking pages in the background… this can take a minute or two.
        </p>
      )}
      {rows !== null && !running && rows.length === 0 && (
        <p className="empty">No ranking keywords returned.</p>
      )}

      {rows && rows.length > 0 && (
        <>
          <div className="actions" style={{ marginBottom: 16 }}>
            <button onClick={downloadMd}>⬇ Download .md</button>
            {mdFile && <span className="hint">Saved on server: exports/{mdFile}</span>}
          </div>

          <div className="card">
            <table className="ckw-table">
              <thead>
                <tr>
                  <th>Keyword</th>
                  <th className="num">Position</th>
                  <th className="num">Count</th>
                  <th className="num">Density</th>
                  <th>Page</th>
                  <th>URL</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.keyword}>
                    <td>{r.keyword}</td>
                    <td className="num">
                      {r.position != null ? (
                        <span className="rank-badge">#{r.position}</span>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="num">{r.kw_count != null ? r.kw_count : '—'}</td>
                    <td className="num">{r.kw_density != null ? `${r.kw_density}%` : '—'}</td>
                    <td>{r.page_type || '—'}</td>
                    <td className="ckw-url">
                      {r.url ? (
                        <a href={r.url} target="_blank" rel="noreferrer">
                          {r.url}
                        </a>
                      ) : (
                        '—'
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {rows.some((r) => r.keywords.length > 0) && (
            <div className="card">
              <h3 style={{ marginTop: 0 }}>On-page keywords per ranking URL</h3>
              {rows
                .filter((r) => r.keywords.length > 0)
                .map((r) => (
                  <div key={r.keyword} style={{ marginBottom: 14 }}>
                    <div className="analyze-head">
                      <strong>{r.keyword}</strong>
                      {r.word_count != null && (
                        <span className="hint">{r.word_count} words</span>
                      )}
                    </div>
                    <div className="chips">
                      {r.keywords.map((k) => (
                        <span className="chip" key={k.term}>
                          {k.term} <b>{k.count}</b> · {k.density}%
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
            </div>
          )}
        </>
      )}
    </section>
  )
}
