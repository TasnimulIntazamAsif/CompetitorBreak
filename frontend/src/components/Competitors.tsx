import { useState, type FormEvent } from 'react'
import type { Competitor } from '../types'
import { api } from '../api'
import { useToast } from '../toast'

export function Competitors({
  competitors,
  reload,
}: {
  competitors: Competitor[]
  reload: () => void
}) {
  const toast = useToast()
  const [editId, setEditId] = useState<number | null>(null)
  const [domain, setDomain] = useState('')
  const [name, setName] = useState('')
  const [blog, setBlog] = useState('')
  const [self, setSelf] = useState(false)

  const reset = () => {
    setEditId(null)
    setDomain('')
    setName('')
    setBlog('')
    setSelf(false)
  }

  const fill = (c: Competitor) => {
    setEditId(c.id)
    setDomain(c.domain)
    setName(c.name || '')
    setBlog(c.blog_url || '')
    setSelf(!!c.is_self)
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const body = {
      domain: domain.trim(),
      name: name.trim() || null,
      blog_url: blog.trim() || null,
      is_self: self,
    }
    try {
      if (editId) await api.updateCompetitor(editId, body)
      else await api.addCompetitor(body)
      toast.show('Saved ' + body.domain, 'ok')
      reset()
      reload()
    } catch (err) {
      toast.show((err as Error).message, 'error')
    }
  }

  const remove = async (c: Competitor) => {
    if (!window.confirm(`Delete ${c.domain} and all its scraped data?`)) return
    try {
      await api.deleteCompetitor(c.id)
      toast.show('Deleted ' + c.domain, 'ok')
      reload()
    } catch (err) {
      toast.show((err as Error).message, 'error')
    }
  }

  return (
    <section>
      <form className="card" onSubmit={submit}>
        <div className="row">
          <label>
            Domain
            <input
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              placeholder="rival.com"
              required
              disabled={editId !== null}
            />
          </label>
          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Rival Inc" />
          </label>
        </div>
        <div className="row">
          <label>
            Blog URL (optional)
            <input
              value={blog}
              onChange={(e) => setBlog(e.target.value)}
              placeholder="https://rival.com/sitemap.xml"
            />
          </label>
          <label className="check">
            <input type="checkbox" checked={self} onChange={(e) => setSelf(e.target.checked)} /> This
            is my own domain
          </label>
        </div>
        <div className="actions">
          <button type="submit" className="primary">
            {editId ? 'Update competitor' : 'Add competitor'}
          </button>
          {editId && (
            <button type="button" className="ghost" onClick={reset}>
              Cancel
            </button>
          )}
        </div>
      </form>

      <table>
        <thead>
          <tr>
            <th>Domain</th>
            <th>Name</th>
            <th>Self</th>
            <th>Blog URL</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {competitors.length === 0 && (
            <tr>
              <td colSpan={5} className="empty">
                No competitors yet. Add one above.
              </td>
            </tr>
          )}
          {competitors.map((c) => (
            <tr key={c.id}>
              <td>{c.domain}</td>
              <td>{c.name || ''}</td>
              <td>{c.is_self ? 'yes' : ''}</td>
              <td>{c.blog_url || ''}</td>
              <td>
                <button className="link" onClick={() => fill(c)}>
                  Edit
                </button>
                <button className="link danger" onClick={() => remove(c)}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
