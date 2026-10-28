import { useEffect, useRef, useState } from 'react'
import type { KeywordIndexRow } from '../types'
import { api } from '../api'
import { useToast } from '../toast'

export function CrawlKeywords() {
  const toast = useToast()
  const [domain, setDomain] = useState('')
  const [maxPages, setMaxPages] = useState(15)
  const [allPages, setAllPages] = useState(false)
  const [rows, setRows] = useState<KeywordIndexRow[] | null>(null)
  const [markdown, setMarkdown] = useState<string | null>(null)
  const [mdFile, setMdFile] = useState<string | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current)
  }, [])

  const poll = async (runId: string) => {
    try {
      const st = await api.crawlStatus(runId)
      if (st.status === 'running') {
        timer.current = window.setTimeout(() => poll(runId), 3000)
        return
      }
      const res = await api.crawlResults(runId)
      setRunning(false)
      if (res.status === 'error') {
        toast.show(res.error || 'Crawl failed', 'error')
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
    setRunId(null)
    setRunning(true)
    try {
      // "All pages" needs a big cap so the whole sitemap is crawled.
      const cap = allPages ? Math.max(maxPages, 2000) : maxPages
      const { run_id } = await api.crawlStart(domain.trim(), cap, 10, allPages)
      setRunId(run_id)
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
    a.download = mdFile || 'keyword-index.md'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <section>
      <div className="card">
        <p className="hint">
          Enter a competitor website (e.g. <em>pihr.com</em>). It crawls the site’s pages and
          builds a <strong>keyword index</strong>: for each specific keyword, which URLs contain
          it and how often (frequency). No AI, no rank data — it reads the actual page content.
          Tick <strong>All pages</strong> to crawl the whole sitemap. Runs in the background.
        </p>
        <div className="row">
          <label>
            Competitor website
            <input
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !running && run()}
              placeholder="pihr.com"
            />
          </label>
          <label>
            Max pages
            <input
              type="number"
              min={1}
              max={40}
              value={maxPages}
              disabled={allPages}
              onChange={(e) => setMaxPages(Math.max(1, Math.min(40, Number(e.target.value) || 1)))}
            />
          </label>
          <label className="check-label">
            <input
              type="checkbox"
              checked={allPages}
              onChange={(e) => setAllPages(e.target.checked)}
            />
            All pages (whole sitemap)
          </label>
          <button className="primary" onClick={run} disabled={running}>
            {running ? 'Crawling…' : 'Crawl'}
          </button>
        </div>
      </div>

      {running && (
        <p className="empty">
          Crawling pages + building the keyword index in the background…
          {allPages && ' Crawling the whole sitemap can take several minutes for a large site.'}
        </p>
      )}
      {rows !== null && !running && rows.length === 0 && (
        <p className="empty">No keywords found (site unreachable or no content pages).</p>
      )}

      {rows && rows.length > 0 && (
        <>
          <div className="actions" style={{ marginBottom: 16 }}>
            <button onClick={downloadMd}>⬇ .md</button>
            {runId &&
              (['csv', 'xlsx', 'docx', 'pdf'] as const).map((fmt) => (
                <a
                  key={fmt}
                  className="btn-link"
                  href={api.crawlExportUrl(runId, fmt)}
                  download
                >
                  ⬇ .{fmt}
                </a>
              ))}
            {mdFile && <span className="hint">Saved on server: exports/{mdFile}</span>}
          </div>

          <div className="card">
            <div className="analyze-head" style={{ marginBottom: 10 }}>
              <strong>Competitor:</strong>
              <span className="badge">{domain.trim()}</span>
              <span className="hint">{rows.length} keywords</span>
            </div>
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
                {rows.map((r) =>
                  r.urls.map((u, i) => (
                    <tr key={`${r.keyword}-${u.url}`}>
                      {i === 0 && (
                        <td rowSpan={r.urls.length} className="kw-cell">
                          {r.keyword}
                          <span className="hint"> · {r.total}× total</span>
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
          </div>
        </>
      )}
    </section>
  )
}
