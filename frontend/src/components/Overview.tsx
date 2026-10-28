import { useEffect, useState } from 'react'
import { api } from '../api'

export function Overview({ onNavigate }: { onNavigate: (tab: string) => void }) {
  const [savedCount, setSavedCount] = useState<number | null>(null)

  useEffect(() => {
    api.listExports().then((x) => setSavedCount(x.length)).catch(() => undefined)
  }, [])

  return (
    <section>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Competitor keyword crawler</h2>
        <p className="hint">
          Crawl any competitor website and get a <strong>keyword index</strong> — each keyword
          with the URLs it appears on and how often (frequency). It reads the actual page content,
          so there’s no AI guesswork. Results are saved to the <strong>Store</strong>, viewable as
          a table and exportable as .md / .csv / .xlsx / .docx / .pdf.
        </p>
        <div className="actions">
          <button className="primary" onClick={() => onNavigate('competitor-keywords')}>
            Start a keyword crawl
          </button>
          <button onClick={() => onNavigate('store')}>
            View saved results{savedCount != null ? ` (${savedCount})` : ''}
          </button>
        </div>
      </div>

      <div className="two-col">
        <div className="card">
          <h3 style={{ marginTop: 0 }}>How it works</h3>
          <ol className="steps">
            <li>Enter a competitor’s website in the <b>Keywords</b> tab.</li>
            <li>Tick <b>All pages</b> to crawl the whole sitemap (or set a page limit).</li>
            <li>It scrapes each page and builds a keyword → URLs → frequency index.</li>
            <li>The result is saved to the <b>Store</b>, where you can view and export it.</li>
          </ol>
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Saved crawls</h3>
          <div className="stat-value">{savedCount ?? '—'}</div>
          <div className="stat-sub">reports in the Store</div>
          <div className="actions" style={{ marginTop: 18 }}>
            <button onClick={() => onNavigate('store')}>Open Store</button>
          </div>
        </div>
      </div>
    </section>
  )
}
