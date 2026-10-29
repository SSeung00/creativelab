import { useQueryClient } from '@tanstack/react-query'
import { Aperture, Download, FolderInput, Images, Settings, SlidersHorizontal } from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router'
import { post } from '@/api/client'
import { useAuthStatus, useJobs } from '@/api/hooks'
import { useLibrary } from '@/stores/library'
import { Button, cn, inputCls } from './ui'

const NAV = [
  { to: '/', label: '라이브러리', icon: Images, end: true },
  { to: '/develop', label: '현상', icon: SlidersHorizontal },
  { to: '/import', label: '가져오기', icon: FolderInput },
  { to: '/exports', label: '내보낸 사진', icon: Download },
  { to: '/settings', label: '설정', icon: Settings },
]

export function AppShell() {
  const auth = useAuthStatus()
  const [locked, setLocked] = useState(false)
  useEffect(() => {
    const on = () => setLocked(true)
    window.addEventListener('photolab:locked', on)
    return () => window.removeEventListener('photolab:locked', on)
  }, [])
  if (auth.data && !auth.data.authed) return <LockScreen pinSet={auth.data.pin_set} />
  if (locked) return <LockScreen pinSet />
  return (
    <div className="flex h-dvh flex-col">
      <TopBar />
      <main className="min-h-0 flex-1">
        <Outlet />
      </main>
      <BottomNav />
    </div>
  )
}

function TopBar() {
  const current = useLibrary((s) => s.current)
  return (
    <header className="hidden h-11 shrink-0 items-center gap-1 border-b border-line bg-panel px-3 md:flex">
      <div className="mr-4 flex items-center gap-2 font-semibold tracking-tight">
        <Aperture className="size-5 text-accent" />
        PhotoLab
      </div>
      {NAV.map((n) => (
        <NavLink
          key={n.to}
          to={n.to === '/develop' ? (current ? `/develop/${current}` : '/') : n.to}
          end={n.end}
          className={({ isActive }) =>
            cn(
              'flex h-8 items-center gap-1.5 rounded-md px-3 text-[13px] text-muted hover:text-fg',
              (isActive || (n.to === '/develop' && location.pathname.startsWith('/develop'))) && 'bg-panel-3 text-fg',
            )
          }
        >
          <n.icon className="size-4" />
          {n.label}
        </NavLink>
      ))}
      <div className="flex-1" />
      <JobIndicator />
    </header>
  )
}

function BottomNav() {
  const current = useLibrary((s) => s.current)
  const loc = useLocation()
  if (loc.pathname.startsWith('/develop')) return null
  return (
    <nav className="grid h-14 shrink-0 grid-cols-5 border-t border-line bg-panel pb-[env(safe-area-inset-bottom)] md:hidden">
      {NAV.map((n) => (
        <NavLink
          key={n.to}
          to={n.to === '/develop' ? (current ? `/develop/${current}` : '/') : n.to}
          end={n.end}
          className={({ isActive }) =>
            cn(
              'flex flex-col items-center justify-center gap-0.5 text-[10px] text-subtle',
              isActive && (n.to !== '/develop' || current) && 'text-accent',
              n.to === '/develop' && !current && 'opacity-40',
            )
          }
        >
          <n.icon className="size-5" />
          {n.label}
        </NavLink>
      ))}
    </nav>
  )
}

function JobIndicator() {
  const jobs = useJobs(true)
  const qc = useQueryClient()
  const active = jobs.data ?? []
  const [prev, setPrev] = useState(0)
  useEffect(() => {
    if (active.length < prev) {
      // something finished: refresh lists
      qc.invalidateQueries({ queryKey: ['photos'] })
      qc.invalidateQueries({ queryKey: ['roots'] })
      qc.invalidateQueries({ queryKey: ['folders'] })
    }
    setPrev(active.length)
  }, [active.length, prev, qc])
  if (!active.length) return null
  const j = active[0]
  const pct = j.total ? Math.round((j.done / j.total) * 100) : null
  return (
    <div className="flex items-center gap-2 text-[12px] text-muted" title={j.message ?? ''}>
      <div className="h-1.5 w-28 overflow-hidden rounded bg-panel-3">
        <div className={cn('h-full bg-accent transition-all', pct === null && 'w-1/3 animate-pulse')} style={pct !== null ? { width: `${pct}%` } : undefined} />
      </div>
      <span className="max-w-64 truncate">{j.message || j.title}</span>
    </div>
  )
}

function LockScreen({ pinSet }: { pinSet: boolean }) {
  const [pin, setPin] = useState('')
  const [err, setErr] = useState('')
  const qc = useQueryClient()
  const unlock = async () => {
    try {
      await post('/auth/unlock', { pin })
      await qc.invalidateQueries()
      location.reload()
    } catch (e) {
      setErr((e as Error).message)
    }
  }
  return (
    <div className="flex h-dvh flex-col items-center justify-center gap-4 p-6">
      <Aperture className="size-10 text-accent" />
      <h1 className="text-lg font-semibold">PhotoLab</h1>
      {pinSet ? (
        <>
          <input
            className={cn(inputCls, 'h-11 w-48 text-center text-lg tracking-[0.5em]')}
            inputMode="numeric"
            type="password"
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && unlock()}
            autoFocus
          />
          <Button variant="primary" onClick={unlock}>
            열기
          </Button>
          {err && <p className="text-[12px] text-bad">{err}</p>}
        </>
      ) : (
        <p className="max-w-xs text-center text-muted">PC의 PhotoLab 설정에서 휴대폰용 PIN을 먼저 정해 주세요.</p>
      )}
    </div>
  )
}
