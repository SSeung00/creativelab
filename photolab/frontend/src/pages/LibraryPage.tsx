import { useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, ArrowRight, Ban, Check, ChevronDown, ChevronRight, Copy, Download, Flag, FolderOpen, Grid3x3,
  EyeOff, Image as ImageIcon, Info, Search, SlidersHorizontal, Sparkles, Star, Waves, X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { post } from '@/api/client'
import { type AiScore, previewUrl, thumbUrl, useAiScores, useFolders, usePatchPhotos, usePhotoDetail, usePhotos, useRoots, waitJob } from '@/api/hooks'
import type { Photo } from '@/api/types'
import { ExportDialog } from '@/components/ExportDialog'
import { ZoomView } from '@/components/ZoomView'
import { Button, cn, IconButton, inputCls, LABEL_COLORS, Segmented, Stars } from '@/components/ui'
import { useClipboard, useLibrary } from '@/stores/library'

const LABEL_KEYS: Record<string, string> = { '6': 'red', '7': 'yellow', '8': 'green', '9': 'blue' }

export function LibraryPage() {
  const lib = useLibrary()
  const roots = useRoots()
  const navigate = useNavigate()
  const [view, setView] = useState<'grid' | 'loupe'>('grid')
  const [exportOpen, setExportOpen] = useState(false)
  const [sidebar, setSidebar] = useState(false)
  const [autoAdvance, setAutoAdvance] = useState(true)
  const [showInfo, setShowInfo] = useState(false)

  useEffect(() => {
    if (lib.rootId == null && roots.data?.length) lib.set({ rootId: roots.data[0].id })
  }, [roots.data, lib])

  const photos = usePhotos({
    root_id: lib.rootId,
    folder: lib.folder,
    min_rating: lib.minRating,
    flag: lib.flag,
    edited: lib.edited,
    label: lib.label,
    q: lib.q,
    sort: lib.sort,
  })
  const scores = useAiScores(lib.rootId)
  const [aiFilter, setAiFilter] = useState<string | null>(null)
  const list = useMemo(() => {
    const base = photos.data ?? []
    if (!aiFilter || !scores.data) return base
    const sc = scores.data
    return base.filter((p) => {
      const s = sc[p.id]
      if (!s) return false
      if (aiFilter === 'blurry') return s.blurry
      if (aiFilter === 'eyes') return s.eyes
      if (aiFilter === 'best') return s.best || s.gsize <= 1
      if (aiFilter === 'clean') return !s.blurry && !s.eyes
      return true
    })
  }, [photos.data, aiFilter, scores.data])
  const index = useMemo(() => new Map(list.map((p, i) => [p.id, i])), [list])
  const curIdx = lib.current != null ? index.get(lib.current) ?? -1 : -1
  const cur = curIdx >= 0 ? list[curIdx] : null
  const patchPhotos = usePatchPhotos()
  const qc = useQueryClient()
  const clip = useClipboard()

  const targets = useCallback((): number[] => {
    if (lib.selected.length > 1) return lib.selected
    return cur ? [cur.id] : []
  }, [lib.selected, cur])

  const go = useCallback(
    (i: number) => {
      if (!list.length) return
      const j = Math.max(0, Math.min(list.length - 1, i))
      lib.select([list[j].id], list[j].id)
    },
    [list, lib],
  )

  const mark = useCallback(
    (body: { rating?: number; flag?: number; label?: string | null; clear_label?: boolean }) => {
      const ids = targets()
      if (!ids.length) return
      patchPhotos.mutate({ ids, ...body })
      if (autoAdvance && ids.length === 1 && (body.rating !== undefined || body.flag !== undefined)) go(curIdx + 1)
    },
    [targets, patchPhotos, autoAdvance, go, curIdx],
  )

  // keyboard culling
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || exportOpen) return
      const k = e.key
      const cols = Number(document.documentElement.dataset.gridCols || 1)
      if (k === 'ArrowRight') go(curIdx + 1)
      else if (k === 'ArrowLeft') go(curIdx - 1)
      else if (k === 'ArrowDown' && view === 'grid') go(curIdx + cols)
      else if (k === 'ArrowUp' && view === 'grid') go(curIdx - cols)
      else if (/^[0-5]$/.test(k) && !e.ctrlKey) mark({ rating: Number(k) })
      else if (LABEL_KEYS[k]) mark(cur?.label === LABEL_KEYS[k] ? { clear_label: true } : { label: LABEL_KEYS[k] })
      else if (k === 'p' || k === 'P') mark({ flag: 1 })
      else if (k === 'x' || k === 'X') mark({ flag: -1 })
      else if (k === 'u' || k === 'U') mark({ flag: 0 })
      else if (k === 'Enter' || k === 'g' || k === 'G' || k === ' ') setView((v) => (v === 'grid' ? 'loupe' : 'grid'))
      else if (k === 'Escape') setView('grid')
      else if ((k === 'd' || k === 'D') && cur) navigate(`/develop/${cur.id}`)
      else if (k === 'i' || k === 'I') setShowInfo((s) => !s)
      else if ((k === 'a' || k === 'A') && e.ctrlKey) {
        e.preventDefault()
        lib.select(list.map((p) => p.id))
      } else if ((k === 'e' || k === 'E') && e.ctrlKey && e.shiftKey) {
        e.preventDefault()
        setExportOpen(true)
      } else if ((k === 'v' || k === 'V') && e.ctrlKey && e.shiftKey && clip.clip) {
        e.preventDefault()
        post('/develop/apply', { ids: targets(), from_id: clip.clip.from, groups: clip.clip.groups }).then(() =>
          qc.invalidateQueries({ queryKey: ['photos'] }),
        )
      } else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, curIdx, mark, view, cur, navigate, lib, list, exportOpen, clip.clip, qc, targets])

  const root = roots.data?.find((r) => r.id === lib.rootId)

  if (roots.data && !roots.data.length) return <EmptyLibrary />

  return (
    <div className="flex h-full">
      <aside className={cn('w-60 shrink-0 overflow-y-auto border-r border-line bg-panel', sidebar ? 'fixed inset-y-0 left-0 z-40 block shadow-2xl' : 'hidden lg:block')}>
        <FolderTree onPick={() => setSidebar(false)} />
      </aside>
      {sidebar && <div className="fixed inset-0 z-30 bg-black/50 lg:hidden" onClick={() => setSidebar(false)} />}
      <div className="flex min-w-0 flex-1 flex-col">
        <FilterBar
          count={list.length}
          selected={lib.selected.length}
          view={view}
          setView={setView}
          onFolders={() => setSidebar(true)}
          title={lib.folder ? lib.folder : root?.name ?? ''}
          onExport={() => setExportOpen(true)}
          autoAdvance={autoAdvance}
          setAutoAdvance={setAutoAdvance}
          onPaste={
            clip.clip
              ? async () => {
                  const ids = targets()
                  if (!ids.length || !confirm(`복사한 보정 설정을 ${ids.length}장에 붙여넣을까요?`)) return
                  await post('/develop/apply', { ids, from_id: clip.clip!.from, groups: clip.clip!.groups })
                  qc.invalidateQueries({ queryKey: ['photos'] })
                }
              : undefined
          }
          aiSlot={
            <AiMenu
              list={photos.data ?? []}
              scores={scores.data}
              filter={aiFilter}
              setFilter={setAiFilter}
              onDone={() => qc.invalidateQueries({ queryKey: ['ai-scores'] })}
              mark={(ids, body) => patchPhotos.mutate({ ids, ...body })}
            />
          }
        />
        <div className="relative min-h-0 flex-1">
          {photos.isLoading ? (
            <div className="p-6 text-muted">불러오는 중…</div>
          ) : view === 'grid' ? (
            <Grid list={list} scores={scores.data} onOpen={(p) => (lib.select([p.id], p.id), setView('loupe'))} />
          ) : cur ? (
            <Loupe photo={cur} list={list} idx={curIdx} go={go} mark={mark} showInfo={showInfo} setShowInfo={setShowInfo} />
          ) : (
            <div className="p-6 text-muted">사진을 골라 주세요.</div>
          )}
        </div>
        {view === 'loupe' && <Filmstrip list={list} idx={curIdx} go={go} />}
      </div>
      <ExportDialog open={exportOpen} onClose={() => setExportOpen(false)} ids={targets()} />
    </div>
  )
}

function EmptyLibrary() {
  const navigate = useNavigate()
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      <ImageIcon className="size-10 text-subtle" />
      <h2 className="text-base font-semibold">아직 사진 폴더가 없어요</h2>
      <p className="max-w-sm text-muted">사진이 들어 있는 폴더를 추가하면 바로 볼 수 있어요. 원본 파일은 절대 바꾸지 않아요.</p>
      <Button variant="primary" onClick={() => navigate('/import')}>
        <FolderOpen className="size-4" /> 폴더 추가하기
      </Button>
    </div>
  )
}

// ------------------------------------------------------------------ folders

function FolderTree({ onPick }: { onPick: () => void }) {
  const lib = useLibrary()
  const roots = useRoots()
  return (
    <div className="py-2">
      <div className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-subtle">폴더</div>
      {roots.data?.map((r) => (
        <RootNode key={r.id} rootId={r.id} name={r.name} count={r.count} open={lib.rootId === r.id} onPick={onPick} />
      ))}
    </div>
  )
}

function RootNode({ rootId, name, count, open, onPick }: { rootId: number; name: string; count: number; open: boolean; onPick: () => void }) {
  const lib = useLibrary()
  const folders = useFolders(open ? rootId : null)
  const tree = useMemo(() => buildTree(folders.data ?? []), [folders.data])
  return (
    <div>
      <button
        className={cn('flex w-full items-center gap-1.5 px-3 py-1.5 text-left hover:bg-panel-3', open && lib.folder == null && 'bg-panel-3 text-fg')}
        onClick={() => {
          lib.set({ rootId, folder: null })
          onPick()
        }}
      >
        {open ? <ChevronDown className="size-3.5 text-subtle" /> : <ChevronRight className="size-3.5 text-subtle" />}
        <span className="flex-1 truncate font-medium">{name}</span>
        <span className="text-[11px] tabular-nums text-subtle">{count.toLocaleString()}</span>
      </button>
      {open && tree.map((n) => <TreeNode key={n.path} node={n} depth={1} rootId={rootId} onPick={onPick} />)}
    </div>
  )
}

interface TNode {
  name: string
  path: string
  count: number
  children: TNode[]
}

function buildTree(folders: { folder: string; count: number }[]): TNode[] {
  const top: TNode[] = []
  const map = new Map<string, TNode>()
  for (const f of folders) {
    if (!f.folder) continue
    const parts = f.folder.split('/')
    let path = ''
    let siblings = top
    for (const part of parts) {
      path = path ? `${path}/${part}` : part
      let n = map.get(path)
      if (!n) {
        n = { name: part, path, count: 0, children: [] }
        map.set(path, n)
        siblings.push(n)
      }
      n.count += f.count
      siblings = n.children
    }
  }
  return top
}

function TreeNode({ node, depth, rootId, onPick }: { node: TNode; depth: number; rootId: number; onPick: () => void }) {
  const lib = useLibrary()
  const [open, setOpen] = useState(lib.folder?.startsWith(node.path) ?? false)
  const active = lib.folder === node.path
  return (
    <div>
      <div
        className={cn('flex cursor-pointer items-center gap-1 py-1 pr-3 hover:bg-panel-3', active && 'bg-accent-soft text-fg')}
        style={{ paddingLeft: 10 + depth * 12 }}
        onClick={() => {
          lib.set({ rootId, folder: node.path })
          onPick()
        }}
      >
        {node.children.length ? (
          <button
            className="text-subtle"
            onClick={(e) => {
              e.stopPropagation()
              setOpen((o) => !o)
            }}
          >
            {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          </button>
        ) : (
          <span className="w-3.5" />
        )}
        <span className="flex-1 truncate text-[12.5px]">{node.name}</span>
        <span className="text-[11px] tabular-nums text-subtle">{node.count.toLocaleString()}</span>
      </div>
      {open && node.children.map((c) => <TreeNode key={c.path} node={c} depth={depth + 1} rootId={rootId} onPick={onPick} />)}
    </div>
  )
}

// ------------------------------------------------------------------ filter bar

function FilterBar({
  count, selected, view, setView, onFolders, title, onExport, autoAdvance, setAutoAdvance, aiSlot, onPaste,
}: {
  count: number
  selected: number
  view: 'grid' | 'loupe'
  setView: (v: 'grid' | 'loupe') => void
  onFolders: () => void
  title: string
  onExport: () => void
  autoAdvance: boolean
  setAutoAdvance: (v: boolean) => void
  aiSlot: React.ReactNode
  onPaste?: () => void
}) {
  const lib = useLibrary()
  const [search, setSearch] = useState(false)
  return (
    <div className="flex shrink-0 items-center gap-1.5 overflow-x-auto border-b border-line bg-panel px-2 py-1.5 sm:flex-wrap [&>*]:shrink-0">
      <IconButton className="lg:hidden" onClick={onFolders} aria-label="폴더">
        <FolderOpen className="size-4" />
      </IconButton>
      <div className="mr-1 min-w-0 max-w-[55vw] truncate text-[13px] font-medium sm:max-w-[40vw]">
        {title} <span className="text-subtle tabular-nums">· {count.toLocaleString()}장{selected > 1 && ` · ${selected}장 선택`}</span>
      </div>
      <div className="flex-1" />
      <Segmented
        value={lib.minRating}
        onChange={(v) => lib.set({ minRating: v })}
        options={[
          { value: 0, label: '전체' },
          { value: 1, label: '★1+' },
          { value: 3, label: '★3+' },
          { value: 5, label: '★5' },
        ]}
      />
      <Segmented
        value={lib.flag ?? ''}
        onChange={(v) => lib.set({ flag: v || null })}
        options={[
          { value: '', label: '모두', title: '플래그 상관없이' },
          { value: 'pick', label: <Flag className="size-3.5" />, title: '픽만' },
          { value: 'notreject', label: <Check className="size-3.5" />, title: '제외 안 한 것' },
          { value: 'reject', label: <Ban className="size-3.5" />, title: '제외한 것' },
        ]}
      />
      <Segmented
        value={lib.edited === null ? '' : lib.edited ? 'y' : 'n'}
        onChange={(v) => lib.set({ edited: v === '' ? null : v === 'y' })}
        options={[
          { value: '', label: '편집 무관' },
          { value: 'y', label: <SlidersHorizontal className="size-3.5" />, title: '편집한 사진' },
        ]}
      />
      <div className="hidden items-center gap-1 sm:flex">
        {Object.entries(LABEL_COLORS).map(([k, c]) => (
          <button
            key={k}
            title={`${k} 라벨`}
            onClick={() => lib.set({ label: lib.label === k ? null : k })}
            className={cn('size-3.5 rounded-sm ring-offset-1 ring-offset-panel', lib.label === k && 'ring-2 ring-fg')}
            style={{ background: c }}
          />
        ))}
      </div>
      {search ? (
        <div className="relative">
          <input
            autoFocus
            className={cn(inputCls, 'h-7 w-40 pr-6')}
            placeholder="파일명·폴더·렌즈"
            value={lib.q}
            onChange={(e) => lib.set({ q: e.target.value })}
          />
          <button className="absolute right-1.5 top-1.5 text-subtle" onClick={() => (lib.set({ q: '' }), setSearch(false))}>
            <X className="size-4" />
          </button>
        </div>
      ) : (
        <IconButton onClick={() => setSearch(true)} aria-label="검색">
          <Search className="size-4" />
        </IconButton>
      )}
      <select className={cn(inputCls, 'h-7 w-auto')} value={lib.sort} onChange={(e) => lib.set({ sort: e.target.value as 'time' })}>
        <option value="time">촬영 시각</option>
        <option value="name">파일명</option>
        <option value="rating">별점</option>
      </select>
      {view === 'grid' && (
        <input
          type="range"
          className="pl-slider hidden w-24 md:block"
          min={110}
          max={360}
          value={lib.thumbSize}
          onChange={(e) => lib.set({ thumbSize: Number(e.target.value) })}
          title="썸네일 크기"
        />
      )}
      {aiSlot}
      <IconButton active={autoAdvance} onClick={() => setAutoAdvance(!autoAdvance)} title="별점·플래그 후 다음 사진으로">
        <ArrowRight className="size-4" />
      </IconButton>
      <IconButton active={view === 'grid'} onClick={() => setView('grid')} title="그리드 (G)">
        <Grid3x3 className="size-4" />
      </IconButton>
      <IconButton active={view === 'loupe'} onClick={() => setView('loupe')} title="크게 보기 (Enter)">
        <ImageIcon className="size-4" />
      </IconButton>
      {onPaste && (
        <Button className="h-7" onClick={onPaste} title="현상에서 복사한 설정을 선택한 사진에 적용 (Ctrl+Shift+V)">
          설정 붙여넣기{selected > 1 ? ` (${selected})` : ''}
        </Button>
      )}
      <Button className="h-7" onClick={onExport}>
        <Download className="size-3.5" /> 내보내기
      </Button>
    </div>
  )
}

// ------------------------------------------------------------------ virtual grid

function Grid({ list, onOpen, scores }: { list: Photo[]; onOpen: (p: Photo) => void; scores?: Record<string, AiScore> }) {
  const lib = useLibrary()
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(800)
  const [height, setHeight] = useState(600)
  const [scroll, setScroll] = useState(0)
  const lastClick = useRef<number | null>(null)
  const isPhone = width < 640
  const size = isPhone ? Math.floor((width - 8) / 3) - 4 : lib.thumbSize
  const gap = isPhone ? 4 : 10
  const cols = Math.max(1, Math.floor((width - gap) / (size + gap)))
  const cell = Math.floor((width - gap) / cols) - gap
  const rowH = cell + gap + (isPhone ? 0 : 22)
  const rows = Math.ceil(list.length / cols)
  useEffect(() => {
    document.documentElement.dataset.gridCols = String(cols)
  }, [cols])
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      setWidth(el.clientWidth)
      setHeight(el.clientHeight)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  // keep current photo in view
  useEffect(() => {
    const el = ref.current
    if (!el || lib.current == null) return
    const i = list.findIndex((p) => p.id === lib.current)
    if (i < 0) return
    const top = Math.floor(i / cols) * rowH
    if (top < el.scrollTop) el.scrollTop = top
    else if (top + rowH > el.scrollTop + el.clientHeight) el.scrollTop = top + rowH - el.clientHeight
  }, [lib.current, list, cols, rowH])
  const first = Math.max(0, Math.floor(scroll / rowH) - 2)
  const last = Math.min(rows, Math.ceil((scroll + height) / rowH) + 2)
  const sel = useMemo(() => new Set(lib.selected), [lib.selected])
  const items = []
  for (let r = first; r < last; r++) {
    for (let c = 0; c < cols; c++) {
      const i = r * cols + c
      if (i >= list.length) break
      const p = list[i]
      items.push(
        <Cell
          key={p.id}
          p={p}
          ai={scores?.[p.id]}
          size={cell}
          compact={isPhone}
          style={{ position: 'absolute', top: r * rowH + gap, left: gap + c * (cell + gap), width: cell }}
          current={lib.current === p.id}
          selected={sel.has(p.id)}
          onClick={(e) => {
            if (e.shiftKey && lastClick.current != null) {
              const a = list.findIndex((x) => x.id === lastClick.current)
              const [lo, hi] = a < i ? [a, i] : [i, a]
              lib.select(list.slice(lo, hi + 1).map((x) => x.id), p.id)
            } else if (e.ctrlKey || e.metaKey) {
              const s = new Set(lib.selected)
              if (s.has(p.id)) s.delete(p.id)
              else s.add(p.id)
              lib.select([...s], p.id)
              lastClick.current = p.id
            } else {
              lib.select([p.id], p.id)
              lastClick.current = p.id
              if (isPhone) onOpen(p)
            }
          }}
          onDoubleClick={() => onOpen(p)}
        />,
      )
    }
  }
  return (
    <div ref={ref} className="absolute inset-0 overflow-y-auto" onScroll={(e) => setScroll(e.currentTarget.scrollTop)}>
      <div style={{ height: rows * rowH + gap, position: 'relative' }}>{items}</div>
    </div>
  )
}

function Cell({
  p, ai, size, style, current, selected, onClick, onDoubleClick, compact,
}: {
  p: Photo
  ai?: AiScore
  size: number
  style: React.CSSProperties
  current: boolean
  selected: boolean
  onClick: (e: React.MouseEvent) => void
  onDoubleClick: () => void
  compact: boolean
}) {
  return (
    <div
      style={style}
      className={cn(
        'group select-none rounded-md',
        !compact && 'bg-panel-2 p-1',
        selected && !compact && 'bg-panel-3',
        current && 'ring-2 ring-accent',
        p.flag === -1 && 'opacity-40',
      )}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
    >
      <div className="relative flex items-center justify-center overflow-hidden" style={{ height: compact ? size : size - 8 }}>
        <img src={thumbUrl(p)} loading="lazy" decoding="async" draggable={false} className="max-h-full max-w-full object-contain" alt="" />
        {p.flag === 1 && <Flag className="absolute left-1 top-1 size-3.5 fill-white text-white drop-shadow" />}
        {p.edited && <SlidersHorizontal className="absolute right-1 bottom-1 size-3.5 text-white/90 drop-shadow" />}
        {p.copy && <Copy className="absolute right-1 top-1 size-3.5 text-white/90 drop-shadow" />}
        {ai && (ai.blurry || ai.eyes || ai.best) && (
          <span className="absolute bottom-1 left-1 flex gap-0.5">
            {ai.blurry && <span title="흔들림·초점 의심" className="rounded bg-black/60 p-0.5"><Waves className="size-3 text-amber-300" /></span>}
            {ai.eyes && <span title="눈 감음" className="rounded bg-black/60 p-0.5"><EyeOff className="size-3 text-amber-300" /></span>}
            {ai.best && <span title={`연사 ${ai.gsize}장 중 베스트`} className="rounded bg-black/60 p-0.5"><Star className="size-3 fill-accent text-accent" /></span>}
          </span>
        )}
        {compact && p.rating > 0 && (
          <span className="absolute bottom-0.5 left-1 text-[10px] text-accent drop-shadow">{'★'.repeat(p.rating)}</span>
        )}
      </div>
      {!compact && (
        <div className="flex h-5 items-center gap-1 px-0.5 text-[11px] text-subtle">
          <Stars value={p.rating} size={10} />
          <span className="flex-1" />
          {p.label && <span className="size-2.5 rounded-sm" style={{ background: LABEL_COLORS[p.label] }} />}
          <span className="hidden truncate group-hover:inline">{p.filename.replace(/\.[^.]+$/, '')}</span>
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ loupe

function Loupe({
  photo, list, idx, go, mark, showInfo, setShowInfo,
}: {
  photo: Photo
  list: Photo[]
  idx: number
  go: (i: number) => void
  mark: (b: { rating?: number; flag?: number; label?: string | null; clear_label?: boolean }) => void
  showInfo: boolean
  setShowInfo: (v: boolean) => void
}) {
  const navigate = useNavigate()
  const touch = useRef<{ x: number; y: number } | null>(null)
  // prefetch neighbours
  useEffect(() => {
    for (const d of [1, 2, -1]) {
      const n = list[idx + d]
      if (n) new Image().src = previewUrl(n)
    }
  }, [idx, list])
  return (
    <div className="absolute inset-0 flex">
      <div
        className="relative min-w-0 flex-1 bg-[#101010]"
        onTouchStart={(e) => (touch.current = { x: e.touches[0].clientX, y: e.touches[0].clientY })}
        onTouchEnd={(e) => {
          if (!touch.current) return
          const dx = e.changedTouches[0].clientX - touch.current.x
          const dy = e.changedTouches[0].clientY - touch.current.y
          if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) go(idx + (dx < 0 ? 1 : -1))
          touch.current = null
        }}
      >
        <ZoomView key={photo.id} photo={photo} />
        <div className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-2 bg-gradient-to-t from-black/70 to-transparent p-3 pb-4">
          <IconButton onClick={() => go(idx - 1)} className="text-white/80">
            <ArrowLeft className="size-5" />
          </IconButton>
          <IconButton active={photo.flag === 1} onClick={() => mark({ flag: photo.flag === 1 ? 0 : 1 })} title="픽 (P)" className="text-white/80">
            <Flag className="size-5" />
          </IconButton>
          <Stars value={photo.rating} onChange={(v) => mark({ rating: v })} size={20} />
          <IconButton active={photo.flag === -1} onClick={() => mark({ flag: photo.flag === -1 ? 0 : -1 })} title="제외 (X)" className="text-white/80">
            <Ban className="size-5" />
          </IconButton>
          <IconButton onClick={() => navigate(`/develop/${photo.id}`)} title="현상 (D)" className="text-white/80">
            <SlidersHorizontal className="size-5" />
          </IconButton>
          <IconButton active={showInfo} onClick={() => setShowInfo(!showInfo)} title="정보 (I)" className="text-white/80">
            <Info className="size-5" />
          </IconButton>
          <IconButton onClick={() => go(idx + 1)} className="text-white/80">
            <ArrowRight className="size-5" />
          </IconButton>
        </div>
        <div className="absolute left-3 top-2 text-[12px] text-white/70 drop-shadow">
          {idx + 1} / {list.length} · {photo.filename}
          {photo.copy ? ` (${photo.copy})` : ''}
        </div>
      </div>
      {showInfo && <InfoPanel id={photo.id} />}
    </div>
  )
}

function InfoPanel({ id }: { id: number }) {
  const d = usePhotoDetail(id)
  const p = d.data
  if (!p) return <aside className="w-64 border-l border-line bg-panel p-3" />
  const rows: [string, string | null][] = [
    ['촬영', p.time ? new Date(p.time).toLocaleString('ko-KR') : null],
    ['카메라', [p.make, p.model].filter(Boolean).join(' ') || null],
    ['렌즈', p.lens],
    ['초점거리', p.focal_length ? `${p.focal_length}mm` : null],
    ['조리개', p.aperture ? `f/${p.aperture}` : null],
    ['셔터', p.shutter ? `${p.shutter}s` : null],
    ['ISO', p.iso ? String(p.iso) : null],
    ['노출 보정', p.exposure_bias != null ? `${p.exposure_bias > 0 ? '+' : ''}${p.exposure_bias} EV` : null],
    ['파일', `${p.filename} · ${(p.size / 1e6).toFixed(1)}MB`],
    ['위치', p.path],
  ]
  return (
    <aside className="hidden w-64 shrink-0 overflow-y-auto border-l border-line bg-panel p-3 sm:block">
      <h3 className="mb-2 text-[12px] font-semibold text-muted">사진 정보</h3>
      <dl className="space-y-1.5">
        {rows.filter(([, v]) => v).map(([k, v]) => (
          <div key={k}>
            <dt className="text-[11px] text-subtle">{k}</dt>
            <dd className="break-all text-[12.5px]">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-4 flex items-center gap-1 text-[11px] text-subtle">
        <Sparkles className="size-3" /> 원본은 읽기만 해요.
      </p>
    </aside>
  )
}

function Filmstrip({ list, idx, go }: { list: Photo[]; idx: number; go: (i: number) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const W = 92
  const [scroll, setScroll] = useState(0)
  const [width, setWidth] = useState(1000)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    setWidth(el.clientWidth)
    el.scrollLeft = Math.max(0, idx * W - el.clientWidth / 2 + W / 2)
  }, [idx])
  const first = Math.max(0, Math.floor(scroll / W) - 5)
  const last = Math.min(list.length, Math.ceil((scroll + width) / W) + 5)
  return (
    <div ref={ref} className="hidden h-[84px] shrink-0 overflow-x-auto overflow-y-hidden border-t border-line bg-panel sm:block" onScroll={(e) => setScroll(e.currentTarget.scrollLeft)}>
      <div style={{ width: list.length * W, height: '100%', position: 'relative' }}>
        {list.slice(first, last).map((p, k) => {
          const i = first + k
          return (
            <button
              key={p.id}
              onClick={() => go(i)}
              style={{ position: 'absolute', left: i * W + 3, top: 6, width: W - 6, height: 70 }}
              className={cn('flex items-center justify-center overflow-hidden rounded bg-panel-2', i === idx && 'ring-2 ring-accent', p.flag === -1 && 'opacity-40')}
            >
              <img src={thumbUrl(p)} loading="lazy" className="max-h-full max-w-full object-contain" alt="" draggable={false} />
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ AI culling menu

function AiMenu({
  list, scores, filter, setFilter, onDone, mark,
}: {
  list: Photo[]
  scores?: Record<string, AiScore>
  filter: string | null
  setFilter: (f: string | null) => void
  onDone: () => void
  mark: (ids: number[], body: { flag?: number }) => void
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const analysed = scores ? list.filter((p) => scores[p.id]).length : 0
  const run = async () => {
    setBusy('분석 시작…')
    setNote('')
    try {
      const r = await post<{ job_id: number }>('/ai/cull', { ids: list.map((p) => p.id) })
      const j = await waitJob(r.job_id, (jj) => setBusy(jj.message || '분석 중…'))
      onDone()
      setNote(j.result ?? '')
    } catch (e) {
      setNote((e as Error).message)
    } finally {
      setBusy(null)
    }
  }
  const blurry = scores ? list.filter((p) => scores[p.id]?.blurry) : []
  const closed = scores ? list.filter((p) => scores[p.id]?.eyes) : []
  const best = scores ? list.filter((p) => scores[p.id]?.best) : []
  const suspect = [...new Set([...blurry, ...closed].map((p) => p.id))]
  const opts: [string, string, number][] = [
    ['clean', '흔들림·눈 감음 없는 사진', analysed - suspect.length],
    ['best', '연사는 베스트 컷만', list.filter((p) => scores?.[p.id] && (scores[p.id].best || scores[p.id].gsize <= 1)).length],
    ['blurry', '흔들림·초점 의심', blurry.length],
    ['eyes', '눈 감은 사진', closed.length],
  ]
  return (
    <div className="relative">
      <IconButton active={!!filter || open} onClick={() => setOpen((o) => !o)} title="AI 골라내기">
        <Sparkles className={cn('size-4', busy && 'animate-pulse text-accent')} />
      </IconButton>
      {open && (
        <div className="absolute right-0 top-9 z-30 w-72 rounded-lg border border-line-strong bg-panel p-2 shadow-2xl">
          <div className="px-2 pb-2 text-[12px] text-muted">
            AI 골라내기 · 분석됨 {analysed.toLocaleString()} / {list.length.toLocaleString()}장
          </div>
          <Button className="mb-2 w-full" variant="primary" disabled={!!busy} onClick={run}>
            <Sparkles className="size-4" /> {busy ?? (analysed ? '다시 분석하기' : '이 목록 분석하기')}
          </Button>
          {note && <p className="px-2 pb-2 text-[11.5px] text-good">{note}</p>}
          {analysed > 0 && (
            <>
              <div className="px-2 pb-1 text-[11px] font-semibold text-subtle">보기</div>
              <button className={cn('block w-full rounded px-2 py-1.5 text-left text-[12.5px] hover:bg-panel-3', !filter && 'text-accent')} onClick={() => setFilter(null)}>
                전체
              </button>
              {opts.map(([k, label, n]) => (
                <button
                  key={k}
                  className={cn('flex w-full justify-between rounded px-2 py-1.5 text-left text-[12.5px] hover:bg-panel-3', filter === k && 'text-accent')}
                  onClick={() => setFilter(filter === k ? null : k)}
                >
                  {label} <span className="tabular-nums text-subtle">{n}</span>
                </button>
              ))}
              <div className="mt-2 border-t border-line px-2 pb-1 pt-2 text-[11px] font-semibold text-subtle">한 번에 표시</div>
              <button
                className="block w-full rounded px-2 py-1.5 text-left text-[12.5px] hover:bg-panel-3 disabled:opacity-40"
                disabled={!suspect.length}
                onClick={() => {
                  if (confirm(`흔들림·눈 감음 의심 ${suspect.length}장을 '제외'로 표시할까요? (파일은 지우지 않아요)`)) mark(suspect, { flag: -1 })
                }}
              >
                의심 사진을 '제외'로 표시 ({suspect.length})
              </button>
              <button
                className="block w-full rounded px-2 py-1.5 text-left text-[12.5px] hover:bg-panel-3 disabled:opacity-40"
                disabled={!best.length}
                onClick={() => {
                  if (confirm(`연사 베스트 ${best.length}장을 '픽'으로 표시할까요?`)) mark(best.map((p) => p.id), { flag: 1 })
                }}
              >
                연사 베스트를 '픽'으로 표시 ({best.length})
              </button>
              <p className="px-2 pt-2 text-[11px] text-subtle">AI 판단은 참고용이에요. 표시는 언제든 다시 바꿀 수 있어요.</p>
            </>
          )}
        </div>
      )}
    </div>
  )
}
