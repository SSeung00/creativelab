import { useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, ChevronLeft, ChevronRight, ClipboardCopy, ClipboardPaste, Copy, Crop, Download, FlipHorizontal2,
  History, Layers, Redo2, RotateCcw, RotateCw, Save, SplitSquareHorizontal, Undo2, ZoomIn,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { api, del, post, put } from '@/api/client'
import { thumbUrl, useDevelop, usePhotos, usePresets, waitJob } from '@/api/hooks'
import type { DevelopData, Mask, Params } from '@/api/types'
import { CropOverlay, fitCrop } from '@/components/develop/CropOverlay'
import { Histogram } from '@/components/develop/Histogram'
import { MaskOverlay, type MaskTool } from '@/components/develop/MaskOverlay'
import { MasksPanel } from '@/components/develop/MasksPanel'
import {
  BasicPanel, ColorPanel, CurvePanel, DetailPanel, EffectsPanel, GradingPanel, OpticsPanel, setIn, TransformPanel,
} from '@/components/develop/panels'
import { useLiveRender } from '@/components/develop/useLiveRender'
import { ExportDialog } from '@/components/ExportDialog'
import { Button, cn, IconButton, inputCls, Section, Segmented, Spinner } from '@/components/ui'
import { useClipboard, useLibrary } from '@/stores/library'

const ASPECTS: { label: string; value: number | null }[] = [
  { label: '자유', value: null },
  { label: '원본', value: -1 },
  { label: '1:1', value: 1 },
  { label: '4:5', value: 4 / 5 },
  { label: '3:2', value: 3 / 2 },
  { label: '16:9', value: 16 / 9 },
  { label: '9:16', value: 9 / 16 },
]

export function DevelopPage() {
  const { id: idStr } = useParams()
  const id = Number(idStr)
  const data = useDevelop(id)
  if (!data.data) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-muted">
        {data.error ? `불러오지 못했어요: ${(data.error as Error).message}` : (<><Spinner /> RAW 여는 중…</>)}
      </div>
    )
  }
  return <Editor key={id} id={id} data={data.data} />
}

function Editor({ id, data }: { id: number; data: DevelopData }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const lib = useLibrary()
  const clip = useClipboard()
  const [params, setParams] = useState<Params>(data.params)
  const [undo, setUndo] = useState<Params[]>([])
  const [redo, setRedo] = useState<Params[]>([])
  const [history, setHistory] = useState(data.history)
  const [snapshots, setSnapshots] = useState(data.snapshots)
  const [mode, setMode] = useState<'normal' | 'crop'>('normal')
  const [before, setBefore] = useState(false)
  const [picking, setPicking] = useState(false)
  const [maskSel, setMaskSel] = useState<number | null>(null)
  const [maskTool, setMaskTool] = useState<MaskTool>(null)
  const [overlay, setOverlay] = useState(false)
  const [brush, setBrush] = useState({ size: 0.05, feather: 50, flow: 100 })
  const [aspect, setAspect] = useState<number | null>(null)
  const [zoom, setZoom] = useState<{ cx: number; cy: number } | null>(null)
  const [exportOpen, setExportOpen] = useState(false)
  const [tab, setTab] = useState<'edit' | 'masks'>('edit')
  const [saving, setSaving] = useState<'idle' | 'pending' | 'saved'>('idle')
  const [nonce, setNonce] = useState(0)
  const [dnBusy, setDnBusy] = useState<string | null>(null)
  const lastCommitted = useRef<Params>(data.params)
  const saveTimer = useRef<number | null>(null)
  const pendingLabel = useRef<string | null>(null)
  const d = data.defaults

  // ---- photo list for prev/next + filmstrip
  const photos = usePhotos({
    root_id: lib.rootId, folder: lib.folder, min_rating: lib.minRating, flag: lib.flag, edited: lib.edited,
    label: lib.label, q: lib.q, sort: lib.sort,
  })
  const list = photos.data ?? []
  const idx = list.findIndex((p) => p.id === id)
  useEffect(() => {
    if (lib.current !== id) lib.select([id], id)
  }, [id, lib])

  // ---- editing
  const upd = useCallback((path: string, value: unknown) => {
    setParams((p) => setIn(p, path, value))
  }, [])

  const flushSave = useCallback(
    async (p: Params, label: string | null) => {
      setSaving('pending')
      const r = await put<{ history_id: number | null }>(`/develop/${id}`, { params: p, label })
      setSaving('saved')
      qc.invalidateQueries({ queryKey: ['photos'] })
      if (label && r.history_id) setHistory((h) => [{ id: r.history_id!, label, at: new Date().toISOString() }, ...h].slice(0, 80))
    },
    [id, qc],
  )

  const commit = useCallback(
    (label: string) => {
      setParams((p) => {
        if (JSON.stringify(p) === JSON.stringify(lastCommitted.current)) return p
        setUndo((u) => [...u.slice(-100), lastCommitted.current])
        setRedo([])
        lastCommitted.current = p
        pendingLabel.current = label
        if (saveTimer.current) window.clearTimeout(saveTimer.current)
        setSaving('pending')
        saveTimer.current = window.setTimeout(() => {
          flushSave(lastCommitted.current, pendingLabel.current)
          pendingLabel.current = null
        }, 500)
        return p
      })
    },
    [flushSave],
  )

  // save when leaving
  useEffect(
    () => () => {
      if (saveTimer.current) {
        window.clearTimeout(saveTimer.current)
        put(`/develop/${id}`, { params: lastCommitted.current, label: pendingLabel.current }).then(() =>
          qc.invalidateQueries({ queryKey: ['photos'] }),
        )
      }
    },
    [id, qc],
  )

  const doUndo = useCallback(() => {
    setUndo((u) => {
      if (!u.length) return u
      const prev = u[u.length - 1]
      setRedo((r) => [...r, lastCommitted.current])
      lastCommitted.current = prev
      setParams(prev)
      flushSave(prev, '되돌리기')
      return u.slice(0, -1)
    })
  }, [flushSave])
  const doRedo = useCallback(() => {
    setRedo((r) => {
      if (!r.length) return r
      const next = r[r.length - 1]
      setUndo((u) => [...u, lastCommitted.current])
      lastCommitted.current = next
      setParams(next)
      flushSave(next, '다시 실행')
      return r.slice(0, -1)
    })
  }, [flushSave])

  const applyAll = useCallback(
    (p: Params, label: string) => {
      setParams(p)
      setTimeout(() => commit(label), 0)
    },
    [commit],
  )

  // ---- viewport + rendering
  const stage = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 800, h: 600 })
  useEffect(() => {
    const el = stage.current
    if (!el) return
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const [cropPx, setCropPx] = useState<[number, number] | null>(null)

  const renderParams = useMemo(() => {
    let p = before ? { ...d, crop: params.crop, rotate90: params.rotate90, flip: params.flip } : params
    if (mode === 'crop') p = { ...p, crop: { left: 0, top: 0, right: 1, bottom: 1, angle: params.crop.angle, aspect: null } }
    return p
  }, [params, before, mode, d])

  const req = useMemo(() => {
    const pad = 24
    const w = (size.w - pad) * dpr
    const h = (size.h - pad) * dpr
    if (zoom && cropPx && mode === 'normal') {
      const rw = Math.min(1, w / cropPx[0])
      const rh = Math.min(1, h / cropPx[1])
      const x0 = Math.max(0, Math.min(1 - rw, zoom.cx - rw / 2))
      const y0 = Math.max(0, Math.min(1 - rh, zoom.cy - rh / 2))
      return { params: renderParams, w: rw * cropPx[0], h: rh * cropPx[1], region: [x0, y0, x0 + rw, y0 + rh] as [number, number, number, number], mask_overlay: overlay ? maskSel : null, nonce }
    }
    return { params: renderParams, w, h, region: null, mask_overlay: overlay && mode === 'normal' ? maskSel : null, nonce }
  }, [renderParams, size, dpr, zoom, cropPx, mode, overlay, maskSel, nonce])
  const out = useLiveRender(id, req)
  // AI denoise runs once per photo (then cached); start it when the renderer asks for it.
  const needDn = !!out.info?.missing_denoise
  useEffect(() => {
    if (!needDn || dnBusy) return
    setDnBusy('AI 노이즈 제거 준비 중…')
    post<{ job_id?: number }>(`/ai/denoise/${id}`, {})
      .then(async (r) => {
        if (r.job_id) await waitJob(r.job_id, (j) => setDnBusy(j.message || 'AI 노이즈 제거 중…'))
        setNonce((n) => n + 1)
      })
      .catch((e) => alert((e as Error).message))
      .finally(() => setDnBusy(null))
  }, [needDn, dnBusy, id])
  useEffect(() => {
    const c = out.info?.crop_px
    if (c && mode === 'normal' && !zoom) setCropPx((old) => (old && old[0] === c[0] && old[1] === c[1] ? old : c))
  }, [out.info, mode, zoom])

  // displayed image rect (css px, relative to stage)
  const rect = useMemo(() => {
    if (!out.info) return null
    const iw = out.info.w / dpr
    const ih = out.info.h / dpr
    const s = zoom ? 1 : Math.min((size.w - 24) / iw, (size.h - 24) / ih, 1 / 1)
    const w = iw * s
    const h = ih * s
    return { left: (size.w - w) / 2, top: (size.h - h) / 2, width: w, height: h }
  }, [out.info, size, dpr, zoom])

  // ---- keyboard
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA') return
      const k = e.key.toLowerCase()
      if (e.ctrlKey && k === 'z' && !e.shiftKey) doUndo()
      else if ((e.ctrlKey && k === 'y') || (e.ctrlKey && e.shiftKey && k === 'z')) doRedo()
      else if (e.key === '\\') setBefore((b) => !b)
      else if (k === 'r' && !e.ctrlKey) setMode((m) => (m === 'crop' ? 'normal' : 'crop'))
      else if (k === 'w') setPicking((p) => !p)
      else if (k === 'z' && !e.ctrlKey) setZoom((z) => (z ? null : { cx: 0.5, cy: 0.5 }))
      else if (k === 'o') setOverlay((o) => !o)
      else if (k === 'escape') {
        setMode('normal')
        setPicking(false)
        setMaskTool(null)
        setZoom(null)
      } else if (e.key === 'ArrowRight' && e.ctrlKey && list[idx + 1]) navigate(`/develop/${list[idx + 1].id}`)
      else if (e.key === 'ArrowLeft' && e.ctrlKey && list[idx - 1]) navigate(`/develop/${list[idx - 1].id}`)
      else if (e.ctrlKey && e.shiftKey && k === 'c') clip.setClip({ from: id, groups: ['basic', 'wb', 'curve', 'color', 'detail', 'optics', 'effects'] })
      else if (e.ctrlKey && e.shiftKey && k === 'v') paste()
      else if (e.ctrlKey && e.shiftKey && k === 'e') setExportOpen(true)
      else if (k === 'g' || (k === 'e' && !e.ctrlKey)) navigate('/')
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const paste = async () => {
    if (!clip.clip) return
    await post('/develop/apply', { ids: [id], from_id: clip.clip.from, groups: clip.clip.groups })
    const fresh = await api<DevelopData>(`/develop/${id}`)
    setUndo((u) => [...u, lastCommitted.current])
    lastCommitted.current = fresh.params
    setParams(fresh.params)
    qc.invalidateQueries({ queryKey: ['photos'] })
  }

  const onStageClick = async (e: React.MouseEvent) => {
    if (!rect || !out.info) return
    const x = (e.clientX - stage.current!.getBoundingClientRect().left - rect.left) / rect.width
    const y = (e.clientY - stage.current!.getBoundingClientRect().top - rect.top) / rect.height
    if (x < 0 || x > 1 || y < 0 || y > 1) return
    if (picking) {
      const reg = req.region
      const cx = reg ? reg[0] + x * (reg[2] - reg[0]) : x
      const cy = reg ? reg[1] + y * (reg[3] - reg[1]) : y
      try {
        const wb = await post<{ temp: number; tint: number }>(`/develop/${id}/pick-wb`, { params, x: cx, y: cy })
        setParams((p) => ({ ...p, wb: 'custom', temp: wb.temp, tint: wb.tint }))
        setTimeout(() => commit('흰색 균형 스포이드'), 0)
        setPicking(false)
      } catch (err) {
        alert((err as Error).message)
      }
      return
    }
    if (mode === 'normal' && !maskTool && e.detail === 2) setZoom((z) => (z ? null : { cx: x, cy: y }))
  }

  const onAuto = async () => {
    const r = await post<Partial<Params>>(`/develop/${id}/auto`, { params })
    applyAll({ ...params, ...r }, '자동 톤')
  }

  const setMasks = (m: Mask[]) => setParams((p) => ({ ...p, masks: m }))
  const mask = maskSel != null ? params.masks[maskSel] ?? null : null
  const OW = out.info?.oriented[0] ?? data.info.oriented[0]
  const OH = out.info?.oriented[1] ?? data.info.oriented[1]

  const panels = (
    <>
      <div className="border-b border-line p-3">
        <Histogram hist={out.hist} clipped={out.info?.clipped ?? null} />
        <div className="mt-1.5 flex items-center justify-between text-[11px] text-subtle">
          <span>
            {out.info ? `${out.info.crop_px[0]}×${out.info.crop_px[1]}` : ''} {out.info?.wb && `· ${out.info.wb.temp}K ${out.info.wb.tint > 0 ? '+' : ''}${out.info.wb.tint}`}
          </span>
          <span className="flex items-center gap-1">
            {out.busy && <Spinner className="size-3" />}
            {saving === 'pending' ? '저장 중…' : saving === 'saved' ? '저장됨' : ''}
          </span>
        </div>
      </div>
      <div className="flex border-b border-line">
        {(['edit', 'masks'] as const).map((t) => (
          <button
            key={t}
            onClick={() => {
              setTab(t)
              if (t === 'edit') {
                setMaskTool(null)
                setOverlay(false)
              }
            }}
            className={cn('flex-1 py-2 text-[12px] font-medium text-muted', tab === t && 'border-b-2 border-accent text-fg')}
          >
            {t === 'edit' ? '편집' : `마스크${params.masks.length ? ` (${params.masks.length})` : ''}`}
          </button>
        ))}
      </div>
      {mode === 'crop' ? (
        <CropPanel
          params={params}
          aspect={aspect}
          setAspect={(a) => {
            setAspect(a)
            if (a) {
              const target = a === -1 ? OW / OH : a
              const c = params.crop
              const w = (c.right - c.left) * OW
              const h = (c.bottom - c.top) * OH
              let nw = w
              let nh = w / target
              if (nh > h) {
                nh = h
                nw = h * target
              }
              const cx = (c.left + c.right) / 2
              const cy = (c.top + c.bottom) / 2
              applyAll(
                { ...params, crop: fitCrop({ ...c, left: cx - nw / OW / 2, right: cx + nw / OW / 2, top: cy - nh / OH / 2, bottom: cy + nh / OH / 2 }, c.angle, OW, OH) },
                '자르기 비율',
              )
            }
          }}
          onAngle={(a) => setParams((p) => ({ ...p, crop: fitCrop(p.crop, a, OW, OH) }))}
          commit={commit}
          applyAll={applyAll}
          done={() => setMode('normal')}
        />
      ) : tab === 'edit' ? (
        <>
          <BasicPanel
            p={params}
            d={d}
            upd={upd}
            commit={commit}
            data={data}
            wbNow={out.info?.wb ?? null}
            picking={picking}
            setPicking={setPicking}
            onAuto={onAuto}
          />
          <CurvePanel p={params} d={d} upd={upd} commit={commit} hist={out.hist} />
          <ColorPanel p={params} d={d} upd={upd} commit={commit} />
          <GradingPanel p={params} d={d} upd={upd} commit={commit} />
          <DetailPanel p={params} d={d} upd={upd} commit={commit} onAiDenoise={() => undefined} />
          <OpticsPanel p={params} d={d} upd={upd} commit={commit} lensName={data.info.lens_profile} />
          <TransformPanel p={params} d={d} upd={upd} commit={commit} />
          <EffectsPanel p={params} d={d} upd={upd} commit={commit} />
        </>
      ) : (
        <MasksPanel
          photoId={id}
          p={params}
          setMasks={setMasks}
          commit={commit}
          selected={maskSel}
          setSelected={setMaskSel}
          tool={maskTool}
          setTool={setMaskTool}
          brush={brush}
          setBrush={setBrush}
          overlay={overlay}
          setOverlay={setOverlay}
        />
      )}
    </>
  )

  const name = list[idx]?.filename ?? ''

  return (
    <div className="flex h-full flex-col">
      {/* toolbar */}
      <div className="flex h-10 shrink-0 items-center gap-1 border-b border-line bg-panel px-2">
        <IconButton onClick={() => navigate('/')} title="라이브러리 (G)">
          <ArrowLeft className="size-4" />
        </IconButton>
        <IconButton disabled={idx <= 0} onClick={() => navigate(`/develop/${list[idx - 1].id}`)} title="이전 (Ctrl+←)">
          <ChevronLeft className="size-4" />
        </IconButton>
        <IconButton disabled={idx < 0 || idx >= list.length - 1} onClick={() => navigate(`/develop/${list[idx + 1].id}`)} title="다음 (Ctrl+→)">
          <ChevronRight className="size-4" />
        </IconButton>
        <span className="mx-1 hidden truncate text-[12px] text-muted sm:inline">{name}</span>
        <div className="flex-1" />
        <IconButton onClick={doUndo} disabled={!undo.length} title="되돌리기 (Ctrl+Z)">
          <Undo2 className="size-4" />
        </IconButton>
        <IconButton onClick={doRedo} disabled={!redo.length} title="다시 실행 (Ctrl+Y)">
          <Redo2 className="size-4" />
        </IconButton>
        <IconButton active={before} onClick={() => setBefore((b) => !b)} title="보정 전/후 (\)">
          <SplitSquareHorizontal className="size-4" />
        </IconButton>
        <IconButton active={mode === 'crop'} onClick={() => setMode((m) => (m === 'crop' ? 'normal' : 'crop'))} title="자르기·수평 (R)">
          <Crop className="size-4" />
        </IconButton>
        <IconButton active={!!zoom} onClick={() => setZoom((z) => (z ? null : { cx: 0.5, cy: 0.5 }))} title="100% 보기 (Z, 더블클릭)">
          <ZoomIn className="size-4" />
        </IconButton>
        <IconButton onClick={() => clip.setClip({ from: id, groups: ['basic', 'wb', 'curve', 'color', 'detail', 'optics', 'effects'] })} title="설정 복사 (Ctrl+Shift+C)">
          <ClipboardCopy className="size-4" />
        </IconButton>
        <IconButton disabled={!clip.clip || clip.clip.from === id} onClick={paste} title="설정 붙여넣기 (Ctrl+Shift+V)">
          <ClipboardPaste className="size-4" />
        </IconButton>
        <IconButton
          onClick={async () => {
            const c = await post<{ id: number }>(`/photos/${id}/virtual-copy`)
            qc.invalidateQueries({ queryKey: ['photos'] })
            navigate(`/develop/${c.id}`)
          }}
          title="가상 복사본 만들기"
        >
          <Copy className="size-4" />
        </IconButton>
        <IconButton
          onClick={() => {
            if (!confirm('모든 보정을 초기화할까요? (히스토리에서 되돌릴 수 있어요)')) return
            applyAll({ ...d }, '초기화')
          }}
          title="초기화"
        >
          <RotateCcw className="size-4" />
        </IconButton>
        <Button className="ml-1 h-7" onClick={() => setExportOpen(true)}>
          <Download className="size-3.5" /> <span className="hidden sm:inline">내보내기</span>
        </Button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <LeftPanel
          id={id}
          params={params}
          applyAll={applyAll}
          history={history}
          snapshots={snapshots}
          setSnapshots={setSnapshots}
        />
        {/* stage */}
        <div
          ref={stage}
          className={cn('relative min-h-[45dvh] flex-1 select-none overflow-hidden bg-[#111]', picking && 'cursor-crosshair', zoom && 'cursor-grab')}
          onClick={onStageClick}
          onPointerDown={(e) => {
            if (!zoom || picking || maskTool) return
            const start = { x: e.clientX, y: e.clientY, z: zoom }
            const move = (ev: PointerEvent) => {
              if (!cropPx) return
              setZoom({
                cx: Math.min(1, Math.max(0, start.z.cx - ((ev.clientX - start.x) * dpr) / cropPx[0])),
                cy: Math.min(1, Math.max(0, start.z.cy - ((ev.clientY - start.y) * dpr) / cropPx[1])),
              })
            }
            const up = () => {
              window.removeEventListener('pointermove', move)
              window.removeEventListener('pointerup', up)
            }
            window.addEventListener('pointermove', move)
            window.addEventListener('pointerup', up)
          }}
        >
          {out.url && rect && (
            <img src={out.url} alt="" draggable={false} className="absolute" style={rect} />
          )}
          {!out.url && (
            <div className="absolute inset-0 flex items-center justify-center gap-2 text-muted">
              <Spinner /> 렌더링 중…
            </div>
          )}
          {out.error && <div className="absolute left-3 top-3 rounded bg-bad/20 px-2 py-1 text-[12px] text-bad">{out.error}</div>}
          {before && <div className="absolute left-3 top-3 rounded bg-black/60 px-2 py-0.5 text-[11px]">보정 전</div>}
          {dnBusy && (
            <div className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-2 rounded-full bg-black/70 px-3 py-1 text-[12px]">
              <Spinner className="size-3" /> {dnBusy}
            </div>
          )}
          {out.info?.missing_ai?.length ? (
            <div className="absolute right-3 top-3 rounded bg-black/60 px-2 py-0.5 text-[11px] text-accent">AI 마스크 준비 중…</div>
          ) : null}
          {mode === 'crop' && rect && (
            <CropOverlay
              rect={rect}
              crop={params.crop}
              aspect={aspect === -1 ? OW / OH : aspect}
              OW={OW}
              OH={OH}
              onChange={(c) => setParams((p) => ({ ...p, crop: c }))}
              onCommit={() => commit('자르기')}
            />
          )}
          {mode === 'normal' && tab === 'masks' && rect && !zoom && (
            <MaskOverlay
              rect={rect}
              mask={mask}
              tool={maskTool}
              brush={brush}
              onChange={(m) => setParams((p) => ({ ...p, masks: p.masks.map((x, i) => (i === maskSel ? m : x)) }))}
              onCommit={() => {
                commit('마스크 편집')
                if (maskTool === 'linear' || maskTool === 'radial') setMaskTool(null)
              }}
            />
          )}
        </div>
        <aside className="max-h-[55dvh] w-full shrink-0 overflow-y-auto border-l border-line bg-panel md:max-h-none md:w-[320px]">
          {panels}
        </aside>
      </div>
      <div
        ref={(el) => {
          const cur = el?.querySelector('[data-current="1"]') as HTMLElement | null
          if (el && cur) el.scrollLeft = cur.offsetLeft - el.clientWidth / 2 + cur.clientWidth / 2
        }}
        className="hidden h-[64px] shrink-0 gap-1 overflow-x-auto border-t border-line bg-panel px-2 py-1.5 lg:flex"
      >
        {list.slice(Math.max(0, idx - 30), idx + 31).map((p) => (
          <button
            key={p.id}
            data-current={p.id === id ? '1' : undefined}
            onClick={() => navigate(`/develop/${p.id}`)}
            className={cn('flex h-full w-[72px] shrink-0 items-center justify-center overflow-hidden rounded bg-panel-2', p.id === id && 'ring-2 ring-accent')}
          >
            <img src={thumbUrl(p)} loading="lazy" className="max-h-full max-w-full object-contain" alt="" />
          </button>
        ))}
      </div>
      <ExportDialog open={exportOpen} onClose={() => setExportOpen(false)} ids={[id]} />
    </div>
  )
}

function CropPanel({
  params, aspect, setAspect, onAngle, commit, applyAll, done,
}: {
  params: Params
  aspect: number | null
  setAspect: (a: number | null) => void
  onAngle: (a: number) => void
  commit: (l: string) => void
  applyAll: (p: Params, l: string) => void
  done: () => void
}) {
  return (
    <Section title="자르기 · 수평" id="crop">
      <div className="mb-2 flex flex-wrap gap-1">
        {ASPECTS.map((a) => (
          <button
            key={a.label}
            onClick={() => setAspect(a.value)}
            className={cn('h-7 rounded-md px-2 text-[12px] text-muted hover:bg-panel-3', aspect === a.value && 'bg-accent-soft text-accent')}
          >
            {a.label}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-[68px_1fr_48px] items-center gap-2">
        <span className="text-[12px] text-muted">수평 각도</span>
        <input
          type="range"
          className="pl-slider"
          min={-45}
          max={45}
          step={0.1}
          value={params.crop.angle}
          onChange={(e) => onAngle(Number(e.target.value))}
          onPointerUp={() => commit('수평 맞추기')}
          onDoubleClick={() => (onAngle(0), commit('수평 맞추기'))}
        />
        <span className="text-right text-[12px] tabular-nums">{params.crop.angle.toFixed(1)}°</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-1">
        <IconButton onClick={() => applyAll({ ...params, rotate90: (params.rotate90 + 3) % 4 }, '왼쪽으로 회전')} title="왼쪽으로 90°">
          <RotateCcw className="size-4" />
        </IconButton>
        <IconButton onClick={() => applyAll({ ...params, rotate90: (params.rotate90 + 1) % 4 }, '오른쪽으로 회전')} title="오른쪽으로 90°">
          <RotateCw className="size-4" />
        </IconButton>
        <IconButton active={params.flip} onClick={() => applyAll({ ...params, flip: !params.flip }, '좌우 반전')} title="좌우 반전">
          <FlipHorizontal2 className="size-4" />
        </IconButton>
        <div className="flex-1" />
        <Button variant="ghost" onClick={() => applyAll({ ...params, crop: { left: 0, top: 0, right: 1, bottom: 1, angle: 0, aspect: null } }, '자르기 초기화')}>
          초기화
        </Button>
        <Button variant="primary" onClick={done}>
          완료
        </Button>
      </div>
    </Section>
  )
}

function LeftPanel({
  id, params, applyAll, history, snapshots, setSnapshots,
}: {
  id: number
  params: Params
  applyAll: (p: Params, l: string) => void
  history: DevelopData['history']
  snapshots: DevelopData['snapshots']
  setSnapshots: (s: DevelopData['snapshots']) => void
}) {
  const presets = usePresets()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'presets' | 'snap' | 'history'>('presets')
  const [name, setName] = useState('')
  const groups = (presets.data ?? []).reduce<Record<string, typeof presets.data>>((acc, p) => {
    ;(acc[p.group] ||= []).push(p)
    return acc
  }, {})
  return (
    <aside className="hidden w-60 shrink-0 overflow-y-auto border-r border-line bg-panel xl:block">
      <div className="p-2">
        <Segmented
          className="w-full"
          value={tab}
          onChange={setTab}
          options={[
            { value: 'presets', label: <span className="flex items-center gap-1"><Layers className="size-3.5" />프리셋</span> },
            { value: 'snap', label: '스냅샷' },
            { value: 'history', label: <span className="flex items-center gap-1"><History className="size-3.5" />기록</span> },
          ]}
        />
      </div>
      {tab === 'presets' && (
        <div className="px-2 pb-3">
          {Object.entries(groups).map(([g, items]) => (
            <div key={g} className="mb-2">
              <div className="px-1 py-1 text-[11px] font-semibold text-subtle">{g}</div>
              {items!.map((p) => (
                <div key={p.id} className="group flex items-center rounded hover:bg-panel-3">
                  <button
                    className="flex-1 truncate px-2 py-1 text-left text-[12.5px]"
                    onClick={() => applyAll(deepMerge(params, p.params) as Params, `프리셋: ${p.name}`)}
                  >
                    {p.name}
                  </button>
                  {p.id > 0 && (
                    <button
                      className="hidden px-2 text-[11px] text-subtle hover:text-bad group-hover:block"
                      onClick={async () => {
                        await del(`/develop/presets/${p.id}`)
                        qc.invalidateQueries({ queryKey: ['presets'] })
                      }}
                    >
                      삭제
                    </button>
                  )}
                </div>
              ))}
            </div>
          ))}
          <div className="mt-2 flex gap-1">
            <input className={cn(inputCls, 'h-7')} placeholder="새 프리셋 이름" value={name} onChange={(e) => setName(e.target.value)} />
            <IconButton
              disabled={!name}
              onClick={async () => {
                await post('/develop/presets', { name, from_id: id })
                setName('')
                qc.invalidateQueries({ queryKey: ['presets'] })
              }}
              title="현재 설정을 프리셋으로 저장"
            >
              <Save className="size-4" />
            </IconButton>
          </div>
        </div>
      )}
      {tab === 'snap' && (
        <div className="px-2 pb-3">
          <div className="mb-2 flex gap-1">
            <input className={cn(inputCls, 'h-7')} placeholder="스냅샷 이름" value={name} onChange={(e) => setName(e.target.value)} />
            <IconButton
              disabled={!name}
              onClick={async () => {
                const s = await post<DevelopData['snapshots'][number]>(`/develop/${id}/snapshots`, { name, params })
                setSnapshots([...snapshots, s])
                setName('')
              }}
            >
              <Save className="size-4" />
            </IconButton>
          </div>
          {snapshots.map((s) => (
            <button
              key={s.id}
              className="block w-full truncate rounded px-2 py-1 text-left text-[12.5px] hover:bg-panel-3"
              onClick={async () => {
                const r = await api<{ params: Params }>(`/develop/${id}/snapshots/${s.id}`)
                applyAll(r.params, `스냅샷: ${s.name}`)
              }}
            >
              {s.name}
            </button>
          ))}
          {!snapshots.length && <p className="px-2 text-[11px] text-subtle">지금 상태를 이름 붙여 저장해 두고 언제든 돌아갈 수 있어요.</p>}
        </div>
      )}
      {tab === 'history' && (
        <div className="px-2 pb-3">
          {history.map((h) => (
            <button
              key={h.id}
              className="flex w-full items-center justify-between rounded px-2 py-1 text-left text-[12px] hover:bg-panel-3"
              onClick={async () => {
                try {
                  const r = await api<{ params: Params }>(`/develop/${id}/history/${h.id}`)
                  applyAll(r.params, `기록으로 이동: ${h.label}`)
                } catch {
                  /* steps created in this session have no server id yet */
                }
              }}
            >
              <span className="truncate">{h.label}</span>
              <span className="text-[10px] text-subtle">{new Date(h.at).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}</span>
            </button>
          ))}
        </div>
      )}
    </aside>
  )
}

function deepMerge(base: unknown, over: unknown): unknown {
  if (over && typeof over === 'object' && !Array.isArray(over) && base && typeof base === 'object' && !Array.isArray(base)) {
    const out: Record<string, unknown> = { ...(base as Record<string, unknown>) }
    for (const [k, v] of Object.entries(over as Record<string, unknown>)) out[k] = deepMerge((base as Record<string, unknown>)[k], v)
    return out
  }
  return over
}
