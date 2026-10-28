import { useEffect, useMemo, useRef, useState } from 'react'
import type { ExportItem, KeywordIndexRow } from '../types'
import { api } from '../api'
import { useToast } from '../toast'

interface CrawlResult {
  kind?: string
  domain?: string
  created?: string
  rows: KeywordIndexRow[]
}

export function Store() {
  const toast = useToast()
  const [query, setQuery] = useState('')
  const [asking, setAsking] = useState(false)
  const [items, setItems] = useState<ExportItem[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [content, setContent] = useState('')
  const timer = useRef<number | null>(null)

  const load = () => api.listExports().then(setItems).catch(() => undefined)

  useEffect(() => {
    load()
    return () => {
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [])

  const view = async (name: string) => {
    setSelected(name)
    try {
      const r = await api.getExport(name)
      setContent(r.content)
    } catch (err) {
      toast.show((err as Error).message, 'error')
    }
  }

  const poll = async (runId: string) => {
    try {
      const st = await api.askStatus(runId)
      if (st.status === 'running') {
        timer.current = window.setTimeout(() => poll(runId), 3000)
        return
      }
      setAsking(false)
      if (st.status === 'error') {
        toast.show(st.error || 'Query failed', 'error')
        return
      }
      toast.show('Saved to store', 'ok')
      await load()
      if (st.md_file) view(st.md_file)
    } catch (err) {
      setAsking(false)
      toast.show((err as Error).message, 'error')
    }
  }

  const ask = async () => {
    if (!query.trim()) {
      toast.show('Enter a query', 'error')
      return
    }
    setAsking(true)
    try {
      const { run_id } = await api.askStart(query.trim())
      poll(run_id)
    } catch (err) {
      setAsking(false)
      toast.show((err as Error).message, 'error')
    }
  }

  const download = () => {
    if (!selected || !content) return
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = selected
    a.click()
    URL.revokeObjectURL(url)
  }

  const remove = async (name: string) => {
    if (!window.confirm(`Delete ${name}?`)) return
    try {
      await api.deleteExport(name)
      if (selected === name) {
        setSelected(null)
        setContent('')
      }
      load()
    } catch (err) {
      toast.show((err as Error).message, 'error')
    }
  }

  // Crawl results are stored as .json — parse them to render a structured table.
  const crawl = useMemo<CrawlResult | null>(() => {
    if (!selected || !selected.endsWith('.json') || !content) return null
    try {
      const data = JSON.parse(content)
      if (Array.isArray(data?.rows)) return data as CrawlResult
    } catch {
      /* not valid JSON */
    }
    return null
  }, [selected, content])

  return (
    <section>
      <div className="card">
        <p className="hint">
          Ask Claude (web search) a free-form query — the answer is saved to the store as a
          Markdown file. Runs in the background (~1–2 min).
        </p>
        <div className="row">
          <label>
            Query
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !asking && ask()}
              placeholder="Search the web for the top sites for 'best HR software in Bangladesh' and list their URLs"
            />
          </label>
          <button className="primary" onClick={ask} disabled={asking}>
            {asking ? 'Asking…' : 'Ask & save'}
          </button>
        </div>
      </div>

      {!selected ? (
        <div className="card store-list">
          <div className="analyze-head" style={{ justifyContent: 'space-between' }}>
            <h3 style={{ margin: 0 }}>Saved reports</h3>
            <button className="link" onClick={load} title="Refresh">
              ⟳ Refresh
            </button>
          </div>
          {items.length === 0 && <p className="empty">Nothing saved yet.</p>}
          {items.map((it) => (
            <div key={it.name} className="store-item">
              <button className="store-name" onClick={() => view(it.name)} title={it.name}>
                {it.name}
              </button>
              <span className="hint">{it.modified.replace('T', ' ')}</span>
              <button className="link danger" onClick={() => remove(it.name)} title="Delete">
                ✕
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="card store-view">
          <div className="store-view-bar">
            <button className="link" onClick={() => { setSelected(null); setContent('') }}>
              ← Reports
            </button>
            {crawl ? (
              <div className="actions" style={{ margin: 0 }}>
                {(['md', 'csv', 'xlsx', 'docx', 'pdf'] as const).map((fmt) => (
                  <a key={fmt} className="btn-link" href={api.exportFileUrl(selected, fmt)} download>
                    ⬇ .{fmt}
                  </a>
                ))}
              </div>
            ) : (
              <button onClick={download}>⬇ Download</button>
            )}
          </div>
          <div className="analyze-head" style={{ marginBottom: 10 }}>
            <strong>{selected}</strong>
            {crawl?.domain && (
              <>
                <span className="badge">{crawl.domain}</span>
                <span className="hint">{crawl.rows.length} keywords</span>
                {crawl.created && <span className="hint">{crawl.created.replace('T', ' ')}</span>}
              </>
            )}
          </div>
          {crawl ? (
            <div className="scroll-table">
              <table className="ckw-table">
                <thead>
                  <tr>
                    <th>Keyword</th>
                    <th>URL</th>
                    <th className="num">Frequency</th>
                  </tr>
                </thead>
                <tbody>
                  {crawl.rows.map((r) =>
                    r.urls.map((u, i) => (
                      <tr key={`${r.keyword}-${u.url}`}>
                        {i === 0 && (
                          <td rowSpan={r.urls.length} className="kw-cell">
                            {r.keyword}
                            <span className="hint"> · {r.total}×</span>
                          </td>
                        )}
                        <td className="ckw-url">
                          <a href={u.url} target="_blank" rel="noreferrer">
                            {u.url}
                          </a>
                        </td>
                        <td className="num">{u.count}</td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </div>
          ) : (
            <pre className="md-view">{content}</pre>
          )}
        </div>
      )}
    </section>
  )
}
