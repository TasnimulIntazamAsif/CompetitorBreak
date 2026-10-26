import { useEffect, useRef, useState } from 'react'
import type { Competitor, Run } from '../types'
import { api } from '../api'
import { useToast } from '../toast'

export function Collect({ competitors }: { competitors: Competitor[] }) {
  const toast = useToast()
  const [target, setTarget] = useState('')
  const [runs, setRuns] = useState<Run[]>([])
  const [running, setRunning] = useState(false)
  const timer = useRef<number | null>(null)

  const load = async () => {
    try {
      setRuns(await api.runs())
      const st = await api.collectStatus()
      setRunning(st.running)
      return st.running
    } catch {
      return false
    }
  }

  const poll = async () => {
    const isRunning = await load()
    if (isRunning) timer.current = window.setTimeout(poll, 2000)
  }

  useEffect(() => {
    load()
    return () => {
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [])

  const run = async () => {
    try {
      await api.collect(target || null)
      toast.show('Collect started', 'ok')
      poll()
    } catch (err) {
      toast.show((err as Error).message, 'error')
    }
  }

  return (
    <section>
      <div className="card">
        <div className="row">
          <label>
            Competitor
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">All competitors</option>
              {competitors.map((c) => (
                <option key={c.id} value={c.domain}>
                  {c.domain}
                </option>
              ))}
            </select>
          </label>
          <button className="primary" onClick={run}>
            Run collect
          </button>
          <span className={'badge' + (running ? ' running' : '')}>{running ? 'running…' : ''}</span>
        </div>
        <p className="hint">
          Scraping runs in the background. Watch progress in the runs table below.
        </p>
      </div>

      <h3>Recent runs</h3>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Domain</th>
            <th>Status</th>
            <th className="num">Found</th>
            <th className="num">Scraped</th>
            <th>Started</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {runs.length === 0 && (
            <tr>
              <td colSpan={7} className="empty">
                No runs yet.
              </td>
            </tr>
          )}
          {runs.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{r.domain}</td>
              <td>
                <span className={'badge ' + r.status}>{r.status}</span>
              </td>
              <td className="num">{r.posts_found}</td>
              <td className="num">{r.posts_scraped}</td>
              <td>{(r.started_at || '').replace('T', ' ')}</td>
              <td>{r.error || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
