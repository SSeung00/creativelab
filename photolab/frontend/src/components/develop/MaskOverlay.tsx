import { useRef } from 'react'
import type { Mask, MaskComponent, Pt } from '@/api/types'

export type MaskTool = null | 'linear' | 'radial' | 'brush' | 'erase'

/**
 * Draws the selected mask's handles over the displayed (cropped) image and lets the user
 * create gradients / paint brush strokes. Coordinates are crop-normalised (0..1).
 */
export function MaskOverlay({
  rect, mask, tool, brush, onChange, onCommit,
}: {
  rect: { left: number; top: number; width: number; height: number }
  mask: Mask | null
  tool: MaskTool
  brush: { size: number; feather: number; flow: number }
  onChange: (m: Mask) => void
  onCommit: () => void
}) {
  const drag = useRef<{ kind: string; ci: number; x: number; y: number; comp: MaskComponent; start: Pt } | null>(null)
  const stroke = useRef<Pt[] | null>(null)
  const norm = (e: React.PointerEvent): Pt => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]
  }
  if (!mask) return null
  const W = rect.width
  const H = rect.height

  const setComp = (ci: number, c: MaskComponent) => {
    const comps = mask.components.map((x, i) => (i === ci ? c : x))
    onChange({ ...mask, components: comps })
  }

  const onDown = (e: React.PointerEvent) => {
    if (!tool) return
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    const p = norm(e)
    if (tool === 'brush' || tool === 'erase') {
      stroke.current = [p]
      // ensure a brush component exists
      let ci = mask.components.findIndex((c) => c.type === 'brush')
      if (ci < 0) {
        onChange({ ...mask, components: [...mask.components, { type: 'brush', strokes: [], mode: 'add' }] })
        ci = mask.components.length
      }
      drag.current = { kind: 'paint', ci, x: e.clientX, y: e.clientY, comp: mask.components[ci] ?? { type: 'brush', strokes: [] }, start: p }
      return
    }
    const comp: MaskComponent =
      tool === 'linear'
        ? { type: 'linear', x0: p[0], y0: p[1], x1: p[0], y1: p[1] + 0.001, mode: 'add' }
        : { type: 'radial', cx: p[0], cy: p[1], rx: 0.01, ry: 0.01, angle: 0, feather: 50, mode: 'add' }
    const ci = mask.components.length
    onChange({ ...mask, components: [...mask.components, comp] })
    drag.current = { kind: 'create', ci, x: e.clientX, y: e.clientY, comp, start: p }
  }

  const onMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    const p = norm(e)
    if (d.kind === 'paint') {
      stroke.current?.push(p)
      const strokes = [...(d.comp.strokes ?? []), { pts: [...(stroke.current ?? [])], ...brush, erase: tool === 'erase' }]
      setComp(d.ci, { ...d.comp, type: 'brush', strokes })
      return
    }
    const c = d.comp
    if (d.kind === 'create') {
      if (c.type === 'linear') setComp(d.ci, { ...c, x1: p[0], y1: p[1] })
      else
        setComp(d.ci, {
          ...c,
          rx: Math.max(0.01, Math.abs(p[0] - d.start[0])),
          ry: Math.max(0.01, (Math.abs(p[1] - d.start[1]) * H) / H),
        })
      return
    }
    const dx = (e.clientX - d.x) / W
    const dy = (e.clientY - d.y) / H
    if (d.kind === 'l0') setComp(d.ci, { ...c, x0: c.x0! + dx, y0: c.y0! + dy })
    else if (d.kind === 'l1') setComp(d.ci, { ...c, x1: c.x1! + dx, y1: c.y1! + dy })
    else if (d.kind === 'lmove') setComp(d.ci, { ...c, x0: c.x0! + dx, y0: c.y0! + dy, x1: c.x1! + dx, y1: c.y1! + dy })
    else if (d.kind === 'rmove') setComp(d.ci, { ...c, cx: c.cx! + dx, cy: c.cy! + dy })
    else if (d.kind === 'rx') setComp(d.ci, { ...c, rx: Math.max(0.01, c.rx! + dx) })
    else if (d.kind === 'ry') setComp(d.ci, { ...c, ry: Math.max(0.01, c.ry! + dy) })
  }

  const onUp = () => {
    if (drag.current) onCommit()
    drag.current = null
    stroke.current = null
  }

  const grab = (kind: string, ci: number) => (e: React.PointerEvent) => {
    e.stopPropagation()
    ;(e.currentTarget as Element).setPointerCapture(e.pointerId)
    drag.current = { kind, ci, x: e.clientX, y: e.clientY, comp: mask.components[ci], start: [0, 0] }
  }

  return (
    <div
      className="absolute touch-none"
      style={{ ...rect, cursor: tool ? 'crosshair' : 'default' }}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
    >
      <svg className="absolute inset-0 size-full overflow-visible">
        {mask.components.map((c, ci) => {
          if (c.type === 'linear') {
            const x0 = c.x0! * W, y0 = c.y0! * H, x1 = c.x1! * W, y1 = c.y1! * H
            const dx = x1 - x0, dy = y1 - y0
            const len = Math.hypot(dx, dy) || 1
            const nx = (-dy / len) * 2000, ny = (dx / len) * 2000
            return (
              <g key={ci}>
                <line x1={x0 - nx} y1={y0 - ny} x2={x0 + nx} y2={y0 + ny} stroke="white" strokeWidth={1.5} />
                <line x1={x1 - nx} y1={y1 - ny} x2={x1 + nx} y2={y1 + ny} stroke="white" strokeDasharray="5 4" strokeWidth={1.2} />
                <line x1={x0} y1={y0} x2={x1} y2={y1} stroke="white" strokeOpacity={0.6} onPointerDown={grab('lmove', ci)} style={{ cursor: 'move' }} strokeWidth={8} />
                <circle cx={x0} cy={y0} r={7} fill="#e8b04a" stroke="white" onPointerDown={grab('l0', ci)} style={{ cursor: 'move' }} />
                <circle cx={x1} cy={y1} r={6} fill="white" onPointerDown={grab('l1', ci)} style={{ cursor: 'move' }} />
              </g>
            )
          }
          if (c.type === 'radial') {
            const cx = c.cx! * W, cy = c.cy! * H, rx = c.rx! * W, ry = c.ry! * H
            return (
              <g key={ci} transform={`rotate(${c.angle ?? 0} ${cx} ${cy})`}>
                <ellipse cx={cx} cy={cy} rx={rx} ry={ry} fill="none" stroke="white" strokeWidth={1.5} />
                <ellipse cx={cx} cy={cy} rx={rx * (1 - (c.feather ?? 50) / 100)} ry={ry * (1 - (c.feather ?? 50) / 100)} fill="none" stroke="white" strokeOpacity={0.5} strokeDasharray="4 4" />
                <circle cx={cx} cy={cy} r={7} fill="#e8b04a" stroke="white" onPointerDown={grab('rmove', ci)} style={{ cursor: 'move' }} />
                <circle cx={cx + rx} cy={cy} r={5} fill="white" onPointerDown={grab('rx', ci)} style={{ cursor: 'ew-resize' }} />
                <circle cx={cx} cy={cy + ry} r={5} fill="white" onPointerDown={grab('ry', ci)} style={{ cursor: 'ns-resize' }} />
              </g>
            )
          }
          return null
        })}
      </svg>
    </div>
  )
}
