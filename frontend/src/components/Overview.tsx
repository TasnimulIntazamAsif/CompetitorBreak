import { useEffect, useState } from 'react'
import type { Run, SerpRun, Stats } from '../types'
import { api } from '../api'

function StatCard({ label, value, sub }: { label: string; value: number; sub?: string }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value.toLocaleString()}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

export function Overview({ onNavigate }: { onNavigate: (tab: string) => void }) {
  const [stats, setStats] = useState<Stats | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [serpRuns, setSerpRuns] = useState<SerpRun[]>([])

  useEffect(() => {
    api.stats().then(setStats).catch(() => undefined)
    api.runs().then((r) => setRuns(r.slice(0, 5))).catch(() => undefined)
    api.serpRuns().then((r) => setSerpRuns(r.slice(0, 5))).catch(() => undefined)
  }, [])

  return (
    <section>
      <div className="stat-grid">
        <StatCard label="Competitors" value={stats?.competitors ?? 0} sub={`${stats?.self ?? 0} own domain`} />
        <StatCard label="Blog posts" value={stats?.posts ?? 0} sub="scraped" />
        <StatCard label="Keywords" value={stats?.keywords ?? 0} sub="extracted" />
        <StatCard label="SERP results" value={stats?.serp_results ?? 0} sub={`${stats?.serp_runs ?? 0} runs`} />
      </div>

      <div className="card quick">
        <span className="quick-label">Quick actions</span>
        <button className="primary" onClick={() => onNavigate('competitors')}>Add competitor</button>
        <button onClick={() => onNavigate('collect')}>Run collect</button>
        <button onClick={() => onNavigate('rankings')}>Check rankings</button>
      </div>

      <div className="two-col">
        <div>
          <h3>Recent scrape runs</h3>
          <table>
            <thead><tr><th>Domain</th><th>Status</th><th className="num">Scraped</th><th>Started</th></tr></thead>
            <tbody>
              {runs.length === 0 && <tr><td colSpan={4} className="empty">No runs yet.</td></tr>}
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>{r.domain}</td>
                  <td><span className={'badge ' + r.status}>{r.status}</span></td>
                  <td className="num">{r.posts_scraped}</td>
                  <td>{(r.started_at || '').replace('T', ' ').slice(0, 16)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <h3>Recent SERP runs</h3>
          <table>
            <thead><tr><th>Country</th><th>Status</th><th className="num">Keywords</th><th>Started</th></tr></thead>
            <tbody>
              {serpRuns.length === 0 && <tr><td colSpan={4} className="empty">No SERP runs yet.</td></tr>}
              {serpRuns.map((r) => (
                <tr key={r.id}>
                  <td>{r.country || ''}</td>
                  <td><span className={'badge ' + r.status}>{r.status}</span></td>
                  <td className="num">{r.keywords_checked}</td>
                  <td>{(r.started_at || '').replace('T', ' ').slice(0, 16)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
