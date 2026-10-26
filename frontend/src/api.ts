import type {
  Competitor,
  CompetitorInput,
  Row,
  Run,
  SerpRun,
  Settings,
  Stats,
  TargetTerm,
} from './types'

// Same origin when served by the backend. To point the dev UI at a remote
// backend, run: localStorage.setItem('cb_api', 'http://localhost:8000')
const BASE = (localStorage.getItem('cb_api') || '').replace(/\/$/, '')

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const opts: RequestInit = { method }
  if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(BASE + path, opts)
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail || detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return res.status === 204 ? (null as T) : (res.json() as Promise<T>)
}

export const api = {
  stats: () => req<Stats>('GET', '/api/stats'),
  competitors: () => req<Competitor[]>('GET', '/api/competitors'),
  addCompetitor: (b: CompetitorInput) => req<Competitor>('POST', '/api/competitors', b),
  updateCompetitor: (id: number, b: CompetitorInput) =>
    req<Competitor>('PUT', `/api/competitors/${id}`, b),
  deleteCompetitor: (id: number) => req<unknown>('DELETE', `/api/competitors/${id}`),

  settings: () => req<Settings>('GET', '/api/settings'),
  saveSettings: (b: Partial<Settings>) => req<Settings>('PUT', '/api/settings', b),

  collect: (domain: string | null) => req<unknown>('POST', '/api/collect', { domain }),
  collectStatus: () => req<{ running: boolean }>('GET', '/api/collect/status'),
  runs: () => req<Run[]>('GET', '/api/runs'),

  keywords: () => req<TargetTerm[]>('GET', '/api/keywords'),
  serpCollect: (b: { keywords?: string[]; limit?: number }) =>
    req<unknown>('POST', '/api/serp/collect', b),
  serpStatus: () => req<{ running: boolean }>('GET', '/api/serp/status'),
  serpRuns: () => req<SerpRun[]>('GET', '/api/serp/runs'),

  report: (path: string) => req<Row[]>('GET', path),
}
