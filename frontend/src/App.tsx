import { useEffect, useState } from 'react'
import type { Competitor } from './types'
import { api } from './api'
import { icons } from './icons'
import { Overview } from './components/Overview'
import { Competitors } from './components/Competitors'
import { SettingsPanel } from './components/SettingsPanel'
import { Collect } from './components/Collect'
import { Rankings } from './components/Rankings'
import { Reports } from './components/Reports'

type Tab = 'overview' | 'competitors' | 'settings' | 'collect' | 'rankings' | 'reports'

const NAV: { id: Tab; label: string; title: string; subtitle: string }[] = [
  { id: 'overview', label: 'Overview', title: 'Overview', subtitle: 'Your competitor intelligence at a glance' },
  { id: 'competitors', label: 'Competitors', title: 'Competitors', subtitle: 'Domains you track, including your own' },
  { id: 'collect', label: 'Collect', title: 'Collect', subtitle: 'Scrape competitor blogs and extract target keywords' },
  { id: 'rankings', label: 'Rankings', title: 'Rankings', subtitle: 'Where competitors rank on Google (SERP)' },
  { id: 'reports', label: 'Reports', title: 'Reports', subtitle: 'Topics, gaps, overlap and ranking reports' },
  { id: 'settings', label: 'Settings', title: 'Settings', subtitle: 'Scraper and SERP configuration' },
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
  const [competitors, setCompetitors] = useState<Competitor[]>([])
  const [theme, setTheme] = useState<Theme>(initialTheme)

  const reload = () => {
    api.competitors().then(setCompetitors).catch(() => undefined)
  }
  useEffect(reload, [])

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
          <span className="brand-mark">CB</span>
          <span className="brand-name">CompetitorBreak</span>
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
          {tab === 'competitors' && <Competitors competitors={competitors} reload={reload} />}
          {tab === 'settings' && <SettingsPanel />}
          {tab === 'collect' && <Collect competitors={competitors} />}
          {tab === 'rankings' && <Rankings />}
          {tab === 'reports' && <Reports competitors={competitors} />}
        </div>
      </div>
    </div>
  )
}
