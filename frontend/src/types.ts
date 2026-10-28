export interface Competitor {
  id: number
  domain: string
  name: string | null
  is_self: number
  blog_url: string | null
}

// What the create/update form sends (is_self as a boolean).
export interface CompetitorInput {
  domain: string
  name: string | null
  blog_url: string | null
  is_self: boolean
}

export interface Settings {
  blog_path_patterns: string[]
  max_pages: number
  crawl_delay: number
  respect_robots: boolean
  country: string
  serp_delay: number
  serp_max_results: number
  serp_limit: number
  serp_headless: boolean
  serp_source: string
  dataforseo_location: string
  llm_source: string
  llm_model: string
}

export interface AnalyzeKeyword {
  term: string
  count: number
  density: number
}

export interface AnalyzeRow {
  position: number
  domain: string
  url: string
  title: string | null
  primary_keyword: string | null
  word_count: number | null
  keywords: AnalyzeKeyword[]
  is_self?: boolean
}

export interface CompetitorKeywordRow {
  keyword: string
  found: boolean
  position: number | null
  url: string | null
  page_type: string | null
  word_count: number | null
  kw_count: number | null
  kw_density: number | null
  keywords: AnalyzeKeyword[]
}

export interface CrawlPage {
  url: string
  title: string | null
  word_count: number | null
  primary: string | null
  keywords: AnalyzeKeyword[]
}

export interface KeywordUrl {
  url: string
  count: number
}

export interface KeywordIndexRow {
  keyword: string
  total: number
  pages: number
  urls: KeywordUrl[]
}

export interface Run {
  id: number
  domain: string
  status: string
  posts_found: number
  posts_scraped: number
  started_at: string
  error: string | null
}

export interface SerpRun {
  id: number
  country: string
  status: string
  keywords_checked: number
  started_at: string
  error: string | null
}

export interface TargetTerm {
  keyword_id: number
  term: string
  posts: number
}

export interface Stats {
  competitors: number
  self: number
  posts: number
  keywords: number
  runs: number
  serp_runs: number
  serp_results: number
}

export interface ExportItem {
  name: string
  size: number
  modified: string
}

export type Row = Record<string, unknown>
