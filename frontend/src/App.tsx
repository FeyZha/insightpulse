import { lazy, Suspense, useEffect, useState } from 'react'
import { ArrowUpRight, BarChart3, ChevronRight, Compass, Database, Gamepad2, History, Menu, X } from 'lucide-react'
import { BrandSignature } from '@/components/brand-mark'
import { IssueDetail } from '@/components/product-ui'
import { forGame, get } from '@/lib/api'
import type { Issue } from '@/lib/domain'
import { OverviewPage } from '@/pages/overview'
const TrackingPage = lazy(() => import('@/pages/tracking').then(module => ({ default: module.TrackingPage })))
const DecisionsPage = lazy(() => import('@/pages/decisions').then(module => ({ default: module.DecisionsPage })))

const pages = [
  { id: 'overview', label: '反馈总览', icon: BarChart3 },
  { id: 'tracking', label: '变化追踪', icon: History },
  { id: 'decisions', label: '决策建议', icon: Compass },
] as const
type Page = typeof pages[number]['id']
const games = {
  'dinner-date': { name: 'Dinner Date', image: '/images/dinner-date-header.jpg', steam: 'https://store.steampowered.com/app/94000/Dinner_Date/' },
  ostranauts: { name: 'Ostranauts', image: '/images/ostranauts-header.jpg', steam: 'https://store.steampowered.com/app/1022980/Ostranauts/' },
} as const
type GameId = keyof typeof games
type Route = { page: Page; game: GameId }
function readRoute(): Route {
  const params = new URLSearchParams(location.search)
  const requested = params.get('page')
  const source = requested === 'ask' ? params.get('from') : requested
  const fixedPage = pages.some(item => item.id === source) ? source as Page : 'overview'
  const game = params.get('game') === 'ostranauts' ? 'ostranauts' : 'dinner-date'
  return { page: fixedPage, game }
}
const routeUrl = (route: Route) => {
  const params = new URLSearchParams()
  if (route.page !== 'overview') params.set('page', route.page)
  if (route.game !== 'dinner-date') params.set('game', route.game)
  return `${location.pathname}${params.size ? `?${params}` : ''}`
}

export default function App() {
  const [route, setRoute] = useState(readRoute)
  const { page, game } = route
  const currentGame = games[game]
  const [menuOpen, setMenuOpen] = useState(false)
  const [issue, setIssue] = useState<Issue>()
  const [issueError, setIssueError] = useState('')
  const openIssue = (id: string) => { setIssueError(''); get<Issue>(forGame(`/api/chatbi/issues/${encodeURIComponent(id)}`, game)).then(setIssue).catch(error => setIssueError(error.message)) }
  useEffect(() => {
    history.replaceState(null, '', routeUrl(readRoute()))
    const restore = () => {
      const next = readRoute()
      setRoute(next); setMenuOpen(false)
      setIssue(undefined); setIssueError('')
    }
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [])
  const navigate = (target: string) => {
    const next: Route = { page: target as Page, game }
    if (routeUrl(next) !== routeUrl(route)) history.pushState(null, '', routeUrl(next))
    setRoute(next); setMenuOpen(false)
    window.scrollTo(0, 0)
  }
  const selectGame = (nextGame: GameId) => {
    if (nextGame === game) return
    setIssue(undefined); setIssueError(''); setMenuOpen(false)
    const next: Route = { page: 'overview', game: nextGame }
    history.pushState(null, '', routeUrl(next)); setRoute(next); window.scrollTo(0, 0)
  }
  return <div className={`app-shell ${page === 'overview' ? 'decision-shell' : ''}`}>
    <a className="skip-link" href="#workspace">跳至主要内容</a>
    <header className="global-header">
      <button className="mobile-menu" aria-label={menuOpen ? '关闭导航' : '打开导航'} aria-expanded={menuOpen} onClick={() => setMenuOpen(value => !value)}>{menuOpen ? <X/> : <Menu/>}</button>
      <button className="brand" onClick={() => navigate('overview')} aria-label="InsightPulse 首页"><BrandSignature/></button>
      <div className="global-actions"><span role="note">预制数据 Demo · 操作仅保存在本浏览器</span></div>
    </header>
    {menuOpen && <button className="menu-backdrop" aria-label="关闭导航菜单" onClick={() => setMenuOpen(false)}/>}
    <aside className={menuOpen ? 'sidebar open' : 'sidebar'}>
      <div className="sidebar-label"><Gamepad2/>游戏工作区 <span>02</span></div>
      <div className="library-games">{(Object.entries(games) as [GameId, (typeof games)[GameId]][]).map(([id, item]) => <button key={id} className={`library-game ${id === game ? 'active' : ''}`} aria-pressed={id === game} onClick={() => selectGame(id)}><img src={item.image} alt={`${item.name} 游戏封面`}/><span><strong>{item.name}</strong><small>{id === game && <i/>}{id === game ? '当前游戏' : '查看观测'}</small></span></button>)}</div>
      <div className="sidebar-section-label">反馈分析</div>
      <nav className="library-nav" aria-label="工作区导航">{pages.map(item => <button key={item.id} className={page === item.id ? 'active' : ''} aria-current={page === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><item.icon/><span>{item.label}</span>{page === item.id && <ChevronRight className="nav-chevron"/>}</button>)}</nav>
      <div className="sidebar-foot"><div><Database/><span>评论快照</span></div><a href={currentGame.steam} target="_blank" rel="noreferrer">查看 {currentGame.name} Steam 页面<ArrowUpRight/></a></div>
    </aside>
    <main className="workspace-main" id="workspace" tabIndex={-1}>
      <div className="topbar"><div className="topbar-location"><Gamepad2/><button onClick={() => navigate('overview')}>{currentGame.name}</button><ChevronRight/><span>{pages.find(item => item.id === page)?.label}</span></div><span className="snapshot-label"><i/>Steam 评论快照</span></div>
      {issueError && <div className="issue-load-error" role="alert">{issueError}<button onClick={() => setIssueError('')}>关闭</button></div>}
      <div className="page-content"><Suspense fallback={<div className="state-box">加载中…</div>}><div key={game}>
        {page === 'overview' && <OverviewPage game={game} openIssue={openIssue}/>}
        {page === 'decisions' && <DecisionsPage game={game} openIssue={openIssue}/>}
        {page === 'tracking' && <TrackingPage game={game}/>}
      </div></Suspense></div>
    </main>
    <IssueDetail issue={issue} open={Boolean(issue)} onOpenChange={open => !open && setIssue(undefined)}/>
  </div>
}
