import { Brush, Circle, Eraser, Eye, EyeOff, Minus, Mountain, Plus, ScanFace, Sparkles, Trash2, User } from 'lucide-react'
import { useState } from 'react'
import { post } from '@/api/client'
import { waitJob } from '@/api/hooks'
import type { Mask, MaskComponent, Params } from '@/api/types'
import { cn, IconButton, Section, Slider, Spinner, Toggle } from '../ui'
import type { MaskTool } from './MaskOverlay'

const LOCAL: { key: string; label: string; min: number; max: number; step?: number; track?: string }[] = [
  { key: 'temp', label: '색온도', min: -100, max: 100, track: 'linear-gradient(90deg,#3d7bd6,#ddd,#e3a33a)' },
  { key: 'tint', label: '색조', min: -100, max: 100, track: 'linear-gradient(90deg,#4caf50,#ddd,#c040c0)' },
  { key: 'exposure', label: '노출', min: -4, max: 4, step: 0.05 },
  { key: 'contrast', label: '대비', min: -100, max: 100 },
  { key: 'highlights', label: '밝은 영역', min: -100, max: 100 },
  { key: 'shadows', label: '어두운 영역', min: -100, max: 100 },
  { key: 'whites', label: '흰색 계열', min: -100, max: 100 },
  { key: 'blacks', label: '검정 계열', min: -100, max: 100 },
  { key: 'texture', label: '텍스처', min: -100, max: 100 },
  { key: 'clarity', label: '부분 대비', min: -100, max: 100 },
  { key: 'dehaze', label: '디헤이즈', min: -100, max: 100 },
  { key: 'hue', label: '색조 이동', min: -100, max: 100 },
  { key: 'saturation', label: '채도', min: -100, max: 100 },
  { key: 'sharpness', label: '선명도', min: -100, max: 100 },
  { key: 'noise', label: '노이즈', min: 0, max: 100 },
]

const AI_KINDS: { kind: NonNullable<MaskComponent['kind']>; label: string; icon: typeof User }[] = [
  { kind: 'subject', label: '피사체', icon: ScanFace },
  { kind: 'sky', label: '하늘', icon: Mountain },
  { kind: 'person', label: '인물', icon: User },
  { kind: 'background', label: '배경', icon: Sparkles },
]

let seq = 0
const newId = () => `m${Date.now().toString(36)}${seq++}`

export function MasksPanel({
  photoId, p, setMasks, commit, selected, setSelected, tool, setTool, brush, setBrush, overlay, setOverlay,
}: {
  photoId: number
  p: Params
  setMasks: (m: Mask[]) => void
  commit: (label: string) => void
  selected: number | null
  setSelected: (i: number | null) => void
  tool: MaskTool
  setTool: (t: MaskTool) => void
  brush: { size: number; feather: number; flow: number }
  setBrush: (b: { size: number; feather: number; flow: number }) => void
  overlay: boolean
  setOverlay: (v: boolean) => void
}) {
  const [aiBusy, setAiBusy] = useState<string | null>(null)
  const [aiErr, setAiErr] = useState('')
  const masks = p.masks
  const m = selected != null ? masks[selected] : null

  const add = (comp: MaskComponent | null, name: string, t: MaskTool) => {
    const mask: Mask = { id: newId(), name, visible: true, amount: 100, components: comp ? [comp] : [], adjust: {} }
    const next = [...masks, mask]
    setMasks(next)
    setSelected(next.length - 1)
    setTool(t)
    if (comp) commit(`마스크 추가: ${name}`)
  }

  const addAi = async (kind: NonNullable<MaskComponent['kind']>, label: string, toExisting = false) => {
    setAiBusy(kind)
    setAiErr('')
    try {
      const r = await post<{ job_id?: number; ready?: boolean }>(`/ai/mask/${photoId}`, { kind })
      if (r.job_id) await waitJob(r.job_id)
      if (toExisting && m && selected != null) {
        update({ ...m, components: [...m.components, { type: 'ai', kind, mode: 'add' }] })
        commit(`AI ${label} 추가`)
      } else add({ type: 'ai', kind, mode: 'add' }, `AI ${label}`, null)
    } catch (e) {
      setAiErr((e as Error).message)
    } finally {
      setAiBusy(null)
    }
  }

  const update = (mm: Mask) => setMasks(masks.map((x, i) => (i === selected ? mm : x)))

  return (
    <Section title="마스크 (부분 보정)" id="masks">
      <div className="mb-2 grid grid-cols-4 gap-1">
        <AddBtn icon={<Minus className="size-4 rotate-45" />} label="선형" onClick={() => add(null, '선형 그라디언트', 'linear')} />
        <AddBtn icon={<Circle className="size-4" />} label="원형" onClick={() => add(null, '원형 그라디언트', 'radial')} />
        <AddBtn icon={<Brush className="size-4" />} label="브러시" onClick={() => add(null, '브러시', 'brush')} />
        <div />
        {AI_KINDS.map((a) => (
          <AddBtn
            key={a.kind}
            icon={aiBusy === a.kind ? <Spinner /> : <a.icon className="size-4" />}
            label={a.label}
            onClick={() => addAi(a.kind, a.label)}
            disabled={!!aiBusy}
          />
        ))}
      </div>
      {aiErr && <p className="mb-2 text-[11px] text-bad">{aiErr}</p>}
      {masks.length > 0 && (
        <div className="mb-2 space-y-0.5">
          {masks.map((mk, i) => (
            <div
              key={mk.id}
              onClick={() => setSelected(i === selected ? null : i)}
              className={cn('flex cursor-pointer items-center gap-1 rounded px-1.5 py-1 hover:bg-panel-3', i === selected && 'bg-accent-soft')}
            >
              <span className="flex-1 truncate text-[12px]">{mk.name}</span>
              <IconButton
                className="size-6"
                onClick={(e) => {
                  e.stopPropagation()
                  setMasks(masks.map((x, j) => (j === i ? { ...x, visible: !x.visible } : x)))
                  commit('마스크 표시')
                }}
              >
                {mk.visible ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />}
              </IconButton>
              <IconButton
                className="size-6"
                onClick={(e) => {
                  e.stopPropagation()
                  setMasks(masks.filter((_, j) => j !== i))
                  setSelected(null)
                  commit('마스크 삭제')
                }}
              >
                <Trash2 className="size-3.5" />
              </IconButton>
            </div>
          ))}
        </div>
      )}
      {m && (
        <div className="space-y-2 rounded-md bg-panel-2 p-2">
          <div className="flex flex-wrap items-center gap-2">
            <Toggle checked={overlay} onChange={setOverlay} label="영역 표시 (O)" />
            <Toggle checked={!!m.invert} onChange={(v) => (update({ ...m, invert: v }), commit('마스크 반전'))} label="반전" />
          </div>
          <div className="flex flex-wrap gap-1">
            <IconButton active={tool === 'linear'} onClick={() => setTool(tool === 'linear' ? null : 'linear')} title="선형 추가">
              <Minus className="size-4 rotate-45" />
            </IconButton>
            <IconButton active={tool === 'radial'} onClick={() => setTool(tool === 'radial' ? null : 'radial')} title="원형 추가">
              <Circle className="size-4" />
            </IconButton>
            <IconButton active={tool === 'brush'} onClick={() => setTool(tool === 'brush' ? null : 'brush')} title="브러시로 칠하기">
              <Brush className="size-4" />
            </IconButton>
            <IconButton active={tool === 'erase'} onClick={() => setTool(tool === 'erase' ? null : 'erase')} title="지우개">
              <Eraser className="size-4" />
            </IconButton>
            {AI_KINDS.map((a) => (
              <IconButton key={a.kind} onClick={() => addAi(a.kind, a.label, true)} title={`AI ${a.label} 더하기`} disabled={!!aiBusy}>
                <Plus className="absolute size-2 translate-x-2 -translate-y-2" />
                <a.icon className="size-4" />
              </IconButton>
            ))}
          </div>
          {(tool === 'brush' || tool === 'erase') && (
            <div>
              <Slider label="크기" value={Math.round(brush.size * 100)} def={5} min={1} max={30} unsigned onChange={(v) => setBrush({ ...brush, size: v / 100 })} />
              <Slider label="페더" value={brush.feather} def={50} min={0} max={100} unsigned onChange={(v) => setBrush({ ...brush, feather: v })} />
              <Slider label="흐름" value={brush.flow} def={100} min={5} max={100} unsigned onChange={(v) => setBrush({ ...brush, flow: v })} />
            </div>
          )}
          {m.components.some((c) => c.type === 'radial') && (
            <Slider
              label="페더"
              value={m.components.find((c) => c.type === 'radial')?.feather ?? 50}
              def={50}
              min={0}
              max={100}
              unsigned
              onChange={(v) => update({ ...m, components: m.components.map((c) => (c.type === 'radial' ? { ...c, feather: v } : c)) })}
              onCommit={() => commit('페더')}
            />
          )}
          <Slider label="양" value={m.amount} def={100} min={0} max={200} unsigned onChange={(v) => update({ ...m, amount: v })} onCommit={() => commit('마스크 양')} />
          <div className="border-t border-line pt-2">
            {LOCAL.map((l) => (
              <Slider
                key={l.key}
                label={l.label}
                value={m.adjust[l.key] ?? 0}
                def={0}
                min={l.min}
                max={l.max}
                step={l.step}
                track={l.track}
                onChange={(v) => update({ ...m, adjust: { ...m.adjust, [l.key]: v } })}
                onCommit={() => commit(`마스크 ${l.label}`)}
              />
            ))}
          </div>
        </div>
      )}
    </Section>
  )
}

function AddBtn({ icon, label, onClick, disabled }: { icon: React.ReactNode; label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      className="flex flex-col items-center gap-0.5 rounded-md py-1.5 text-[11px] text-muted hover:bg-panel-3 hover:text-fg disabled:opacity-40"
    >
      {icon}
      {label}
    </button>
  )
}
