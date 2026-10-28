import type {
  AnalyzeRow,
  Competitor,
  CompetitorInput,
  CompetitorKeywordRow,
  KeywordIndexRow,
  ExportItem,
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

  analyzeStart: (query: string, maxResults: number) =>
    req<{ run_id: string }>('POST', '/api/analyze/start', { query, max_results: maxResults }),
  analyzeStatus: (runId: string) =>
    req<{ status: string; error: string | null; count: number }>(
      'GET', `/api/analyze/status?run_id=${runId}`,
    ),
  analyzeResults: (runId: string) =>
    req<{
      status: string
      error: string | null
      results: AnalyzeRow[]
      markdown: string | null
      md_file: string | null
    }>('GET', `/api/analyze/results?run_id=${runId}`),

  ckwStart: (domain: string, maxKeywords: number) =>
    req<{ run_id: string }>('POST', '/api/competitor-keywords/start', {
      domain,
      max_keywords: maxKeywords,
    }),
  ckwStatus: (runId: string) =>
    req<{ status: string; error: string | null; count: number }>(
      'GET', `/api/competitor-keywords/status?run_id=${runId}`,
    ),
  ckwResults: (runId: string) =>
    req<{
      status: string
      error: string | null
      results: CompetitorKeywordRow[]
      markdown: string | null
      md_file: string | null
    }>('GET', `/api/competitor-keywords/results?run_id=${runId}`),

  crawlStart: (domain: string, maxPages: number, maxKeywords: number, allPages: boolean) =>
    req<{ run_id: string }>('POST', '/api/crawl-keywords/start', {
      domain,
      max_pages: maxPages,
      max_keywords: maxKeywords,
      all_pages: allPages,
    }),
  crawlStatus: (runId: string) =>
    req<{ status: string; error: string | null; count: number }>(
      'GET', `/api/crawl-keywords/status?run_id=${runId}`,
    ),
  crawlResults: (runId: string) =>
    req<{
      status: string
      error: string | null
      results: KeywordIndexRow[]
      markdown: string | null
      md_file: string | null
    }>('GET', `/api/crawl-keywords/results?run_id=${runId}`),
  crawlExportUrl: (runId: string, fmt: 'csv' | 'xlsx' | 'docx' | 'pdf') =>
    `${BASE}/api/crawl-keywords/export?run_id=${encodeURIComponent(runId)}&fmt=${fmt}`,

  askStart: (query: string) => req<{ run_id: string }>('POST', '/api/ask/start', { query }),
  askStatus: (runId: string) =>
    req<{ status: string; error: string | null; md_file: string | null }>(
      'GET', `/api/ask/status?run_id=${runId}`,
    ),
  exportFileUrl: (name: string, fmt: 'md' | 'csv' | 'xlsx' | 'docx' | 'pdf') =>
    `${BASE}/api/exports/${encodeURIComponent(name)}/download?fmt=${fmt}`,
  listExports: () => req<ExportItem[]>('GET', '/api/exports'),
  getExport: (name: string) =>
    req<{ name: string; content: string }>('GET', `/api/exports/${encodeURIComponent(name)}`),
  deleteExport: (name: string) =>
    req<unknown>('DELETE', `/api/exports/${encodeURIComponent(name)}`),
}
