import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { fetchRuntime } from '../api/client'
import Icon from './ui/Icon'

export default function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const [backend, setBackend] = useState('连接中')
  const [online, setOnline] = useState(false)
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' })
  }, [location.pathname])
  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    const controller = new AbortController()
    const refresh = async () => {
      if (document.hidden) {
        timer = setTimeout(refresh, 30000)
        return
      }
      try {
        const info = await fetchRuntime(controller.signal)
        if (!disposed) {
          setBackend(info.provider === 'codex' ? 'Codex CLI' : info.model)
          setOnline(true)
        }
      } catch {
        if (!disposed) {
          setBackend('服务未连接')
          setOnline(false)
        }
      } finally {
        if (!disposed) timer = setTimeout(refresh, 30000)
      }
    }
    void refresh()
    return () => {
      disposed = true
      clearTimeout(timer)
      controller.abort()
    }
  }, [])
  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        跳到内容
      </a>
      <aside className="app-sidebar">
        <Link to="/" className="brand">
          <span className="brand-mark">
            <Icon name="book" width={23} height={23} />
          </span>
          <span>
            Paper Research<small>文献研究工作台</small>
          </span>
        </Link>
        <div className="sidebar-label">工作空间</div>
        <nav aria-label="主导航">
          <Link
            to="/"
            className={`nav-item ${location.pathname !== '/new' ? 'active' : ''}`}
            aria-current={location.pathname === '/' ? 'page' : undefined}
          >
            <Icon name="grid" />
            研究任务
          </Link>
          <Link
            to="/new"
            className={`nav-item ${location.pathname === '/new' ? 'active' : ''}`}
            aria-current={location.pathname === '/new' ? 'page' : undefined}
          >
            <Icon name="plus" />
            新建研究
          </Link>
        </nav>
        <div className="sidebar-footer">
          <div className="flex items-center gap-2 text-sm">
            <span className={`connection-dot ${online ? 'online' : ''}`} />
            {backend}
          </div>
          <p>
            本地工作空间
            <br />
            从论文证据，到研究洞见。
          </p>
        </div>
      </aside>
      <div className="app-main">
        <header className="workspace-header">
          <span>
            WORKSPACE <span className="mx-2 text-gray-300">/</span>{' '}
            <span className="text-gray-700">
              {location.pathname === '/'
                ? '研究任务'
                : location.pathname === '/new'
                  ? '新建研究'
                  : '研究详情'}
            </span>
          </span>
          <span className="workspace-tag">个人研究空间</span>
        </header>
        <main id="main-content" className="workspace-content">
          {children}
        </main>
        <footer className="workspace-footer">
          Paper Research Agent <span>检索 · 阅读 · 整理 · 写作</span>
        </footer>
      </div>
    </div>
  )
}
