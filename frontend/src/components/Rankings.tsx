import { useEffect, useRef, useState } from 'react'
import type { SerpRun } from '../types'
import { api } from '../api'
import { useToast } from '../toast'

export function Rankings() {
  const toast = useToast()
  const [source, setSource] = useState<'auto' | 'manual'>('auto')
  const [limit, setLimit] = useState(15)
  const [manual, setManual] = useState('')
  const [runs, setRuns] = useState<SerpRun[]>([])
  const [running, setRunning] = useState(false)
  const timer = useRef<number | null>(null)

  const load = async () => {
    try {
      setRuns(await api.serpRuns())
      const st = await api.serpStatus()
      setRunning(st.running)
      return st.running
    } catch {
      return false
    }
  }

  const poll = async () => {
    const isRunning = await load()
    if (isRunning) timer.current = window.setTimeout(poll, 3000)
  }

  useEffect(() => {
    load()
    // Default "Max terms" from the (safe) Settings value.
    api.settings().then((s) => setLimit(s.serp_limit)).catch(() => undefined)
    return () => {
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [])

  const run = async () => {
    const body: { keywords?: string[]; limit?: number } = {}
    if (source === 'manual') {
      body.keywords = manual.split('\n').map((x) => x.trim()).filter(Boolean)
      if (!body.keywords.length) {
        toast.show('Enter at least one keyword', 'error')
        return
      }
    } else {
      body.limit = limit
    }
    try {
      await api.serpCollect(body)
      toast.show('SERP collect started', 'ok')
      poll()
    } catch (err) {
      toast.show((err as Error).message, 'error')
    }
  }

  return (
    <section>
      <div className="card">
        <p className="warn-text">
          ⚠ Google scraping is against Google's Terms of Service and is best-effort — expect
          CAPTCHAs, rate limits and location-based variance. Keep volume low (longer delay in
          Settings).
        </p>
        <div className="row">
          <label>
            Keyword source
            <select value={source} onChange={(e) => setSource(e.target.value as 'auto' | 'manual')}>
              <option value="auto">Auto — top target terms from scraped blogs</option>
              <option value="manual">Manual list</option>
            </select>
          </label>
          {source === 'auto' && (
            <label>
              Max terms
              <input
                type="number"
                min={1}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
              />
            </label>
          )}
        </div>
        {source === 'manual' && (
          <label>
            Keywords (one per line)
            <textarea
              rows={4}
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              placeholder={'hr software\npayroll bangladesh'}
            />
          </label>
        )}
        <div className="row">
          <button className="primary" onClick={run}>
            Run SERP collect
          </button>
          <span className={'badge' + (running ? ' running' : '')}>{running ? 'running…' : ''}</span>
        </div>
        <p className="hint">
          Blocked by a CAPTCHA? Turn off <strong>Headless browser</strong> in Settings and re-run —
          a visible Chrome window opens so you can solve it yourself, then collection continues.
        </p>
      </div>

      <h3>Recent SERP runs</h3>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Country</th>
            <th>Status</th>
            <th className="num">Keywords</th>
            <th>Started</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {runs.length === 0 && (
            <tr>
              <td colSpan={6} className="empty">
                No SERP runs yet.
              </td>
            </tr>
          )}
          {runs.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{r.country || ''}</td>
              <td>
                <span className={'badge ' + r.status}>{r.status}</span>
              </td>
              <td className="num">{r.keywords_checked}</td>
              <td>{(r.started_at || '').replace('T', ' ')}</td>
              <td>{r.error || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
