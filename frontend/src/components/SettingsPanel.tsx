import { useEffect, useState, type FormEvent } from 'react'
import type { Settings } from '../types'
import { api } from '../api'
import { useToast } from '../toast'

export function SettingsPanel() {
  const toast = useToast()
  const [s, setS] = useState<Settings | null>(null)
  const [patterns, setPatterns] = useState('')

  useEffect(() => {
    api
      .settings()
      .then((data) => {
        setS(data)
        setPatterns((data.blog_path_patterns || []).join('\n'))
      })
      .catch((err) => toast.show((err as Error).message, 'error'))
  }, [])

  if (!s) return <p className="empty">Loading settings…</p>

  const update = <K extends keyof Settings>(key: K, value: Settings[K]) =>
    setS({ ...s, [key]: value })

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const body: Partial<Settings> = {
      blog_path_patterns: patterns.split('\n').map((x) => x.trim()).filter(Boolean),
      max_pages: s.max_pages,
      crawl_delay: s.crawl_delay,
      respect_robots: s.respect_robots,
      country: s.country,
      serp_delay: s.serp_delay,
      serp_max_results: s.serp_max_results,
      serp_limit: s.serp_limit,
      serp_headless: s.serp_headless,
    }
    try {
      await api.saveSettings(body)
      toast.show('Settings saved', 'ok')
    } catch (err) {
      toast.show((err as Error).message, 'error')
    }
  }

  return (
    <section>
      <form className="card" onSubmit={submit}>
        <label>
          Blog path patterns (one per line)
          <textarea rows={6} value={patterns} onChange={(e) => setPatterns(e.target.value)} />
        </label>
        <div className="row">
          <label>
            Max pages per competitor
            <input
              type="number"
              min={1}
              value={s.max_pages}
              onChange={(e) => update('max_pages', Number(e.target.value))}
            />
          </label>
          <label>
            Crawl delay (seconds)
            <input
              type="number"
              step={0.1}
              min={0}
              value={s.crawl_delay}
              onChange={(e) => update('crawl_delay', Number(e.target.value))}
            />
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={s.respect_robots}
              onChange={(e) => update('respect_robots', e.target.checked)}
            />{' '}
            Respect robots.txt
          </label>
        </div>

        <h3>SERP (rankings)</h3>
        <div className="row">
          <label>
            Country (ISO code)
            <input
              value={s.country}
              onChange={(e) => update('country', e.target.value)}
              placeholder="us"
            />
          </label>
          <label>
            SERP delay (seconds)
            <input
              type="number"
              step={0.5}
              min={0}
              value={s.serp_delay}
              onChange={(e) => update('serp_delay', Number(e.target.value))}
            />
          </label>
          <label>
            Max results per keyword
            <input
              type="number"
              min={1}
              value={s.serp_max_results}
              onChange={(e) => update('serp_max_results', Number(e.target.value))}
            />
          </label>
          <label>
            Max keywords per run
            <input
              type="number"
              min={1}
              value={s.serp_limit}
              onChange={(e) => update('serp_limit', Number(e.target.value))}
            />
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={s.serp_headless}
              onChange={(e) => update('serp_headless', e.target.checked)}
            />{' '}
            Headless browser
          </label>
        </div>
        <p className="hint">
          These defaults keep Google scraping volume low to avoid CAPTCHAs and rate limits —
          a longer SERP delay and fewer keywords per run are safer. Raise them only cautiously.
        </p>

        <div className="actions">
          <button type="submit" className="primary">
            Save settings
          </button>
        </div>
      </form>
      <p className="hint">
        Blog path patterns decide which URLs count as blog posts (case-insensitive substring match).
      </p>
    </section>
  )
}
