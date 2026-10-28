import { useEffect, useState } from 'react'
import { icons } from './icons'
import { Overview } from './components/Overview'
import { SettingsPanel } from './components/SettingsPanel'
import { CrawlKeywords } from './components/CrawlKeywords'
import { Store } from './components/Store'

type Tab = 'overview' | 'competitor-keywords' | 'store' | 'settings'

const NAV: { id: Tab; label: string; title: string; subtitle: string }[] = [
  { id: 'overview', label: 'Overview', title: 'Overview', subtitle: 'Competitor keyword crawler at a glance' },
  { id: 'competitor-keywords', label: 'Keywords', title: 'Competitor keyword index', subtitle: 'Crawl a site and list each keyword with the URLs it appears on and its frequency' },
  { id: 'store', label: 'Store', title: 'Store', subtitle: 'Saved keyword crawls (structured) and Claude web-search reports' },
  { id: 'settings', label: 'Settings', title: 'Settings', subtitle: 'Scraper configuration' },
]

type Theme = 'light' | 'dark'

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem('cb_theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch {
    /* ignore */
  }
  return 'light'
}

export function App() {
  const [tab, setTab] = useState<Tab>('overview')
  const [theme, setTheme] = useState<Theme>(initialTheme)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try {
      localStorage.setItem('cb_theme', theme)
    } catch {
      /* ignore */
    }
  }, [theme])

  const meta = NAV.find((n) => n.id === tab)!

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">CA</span>
          <span className="brand-name">CompetitorANK</span>
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <button
              key={n.id}
              className={'nav-item' + (tab === n.id ? ' active' : '')}
              onClick={() => setTab(n.id)}
            >
              <span className="nav-icon">{icons[n.id]}</span>
              <span>{n.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <button
            className="theme-toggle"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            title="Toggle theme"
          >
            <span className="nav-icon">{theme === 'dark' ? icons.sun : icons.moon}</span>
            <span>{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>
          </button>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div>
            <h1>{meta.title}</h1>
            <p>{meta.subtitle}</p>
          </div>
        </header>
        <div className="content">
          {tab === 'overview' && <Overview onNavigate={(t) => setTab(t as Tab)} />}
          {tab === 'competitor-keywords' && <CrawlKeywords />}
          {tab === 'store' && <Store />}
          {tab === 'settings' && <SettingsPanel />}
        </div>
      </div>
    </div>
  )
}
