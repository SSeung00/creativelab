import { useRef } from 'react'
import type { Params } from '@/api/types'

type Crop = Params['crop']

/** Shrink a crop (keeping its centre and aspect) so it fits inside the image rotated by `angle`. */
export function fitCrop(c: Crop, angle: number, OW: number, OH: number): Crop {
  const a = Math.abs((angle * Math.PI) / 180)
  const cos = Math.cos(a)
  const sin = Math.sin(a)
  let w = (c.right - c.left) * OW
  let h = (c.bottom - c.top) * OH
  let cx = ((c.left + c.right) / 2 - 0.5) * OW
  let cy = ((c.top + c.bottom) / 2 - 0.5) * OH
  // corners of the crop must lie inside the rotated image: check in image coordinates
  const inside = (w_: number, h_: number, cx_: number, cy_: number) => {
    for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const x = cx_ + (sx * w_) / 2
      const y = cy_ + (sy * h_) / 2
      const ang = (angle * Math.PI) / 180
      const X = Math.cos(ang) * x - Math.sin(ang) * y
      const Y = Math.sin(ang) * x + Math.cos(ang) * y
      if (Math.abs(X) > OW / 2 + 0.5 || Math.abs(Y) > OH / 2 + 0.5) return false
    }
    return true
  }
  if (!inside(w, h, cx, cy)) {
    // first try centring, then shrink
    const s0 = Math.min(OW / (w * cos + h * sin), OH / (w * sin + h * cos), 1)
    let lo = 0
    let hi = 1
    for (let i = 0; i < 30; i++) {
      const t = (lo + hi) / 2
      if (inside(w, h, cx * (1 - t), cy * (1 - t))) hi = t
      else lo = t
    }
    if (hi < 1 && inside(w, h, cx * (1 - hi), cy * (1 - hi))) {
      cx *= 1 - hi
      cy *= 1 - hi
    } else {
      w *= s0
      h *= s0
      cx = 0
      cy = 0
      lo = s0
      hi = 1
      // grow back as far as possible
      for (let i = 0; i < 25; i++) {
        const t = (lo + hi) / 2
        if (inside((w / s0) * t, (h / s0) * t, 0, 0)) lo = t
        else hi = t
      }
      w = (w / s0) * lo
      h = (h / s0) * lo
    }
  }
  return {
    ...c,
    angle,
    left: (cx - w / 2) / OW + 0.5,
    right: (cx + w / 2) / OW + 0.5,
    top: (cy - h / 2) / OH + 0.5,
    bottom: (cy + h / 2) / OH + 0.5,
  }
}

export function CropOverlay({
  rect, crop, onChange, onCommit, aspect, OW, OH,
}: {
  rect: { left: number; top: number; width: number; height: number }
  crop: Crop
  onChange: (c: Crop) => void
  onCommit: () => void
  aspect: number | null
  OW: number
  OH: number
}) {
  const drag = useRef<{ kind: string; x: number; y: number; c: Crop } | null>(null)
  const L = crop.left * rect.width
  const T = crop.top * rect.height
  const R = crop.right * rect.width
  const B = crop.bottom * rect.height

  const move = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    const dx = (e.clientX - d.x) / rect.width
    const dy = (e.clientY - d.y) / rect.height
    let { left, top, right, bottom } = d.c
    const k = d.kind
    if (k === 'move') {
      const w = right - left
      const h = bottom - top
      left = Math.min(1 - w, Math.max(0, left + dx))
      top = Math.min(1 - h, Math.max(0, top + dy))
      right = left + w
      bottom = top + h
    } else {
      if (k.includes('l')) left = Math.min(right - 0.02, Math.max(0, left + dx))
      if (k.includes('r')) right = Math.max(left + 0.02, Math.min(1, right + dx))
      if (k.includes('t')) top = Math.min(bottom - 0.02, Math.max(0, top + dy))
      if (k.includes('b')) bottom = Math.max(top + 0.02, Math.min(1, bottom + dy))
      if (aspect) {
        // keep the aspect: adjust the dimension that the handle doesn't drive
        const wPx = (right - left) * OW
        const hPx = (bottom - top) * OH
        if (k === 't' || k === 'b' || (k.length === 2 && Math.abs(dy) > Math.abs(dx))) {
          const nw = (hPx * aspect) / OW
          if (k.includes('l')) left = right - nw
          else right = left + nw
        } else {
          const nh = wPx / aspect / OH
          if (k.includes('t')) top = bottom - nh
          else bottom = top + nh
        }
        if (left < 0 || right > 1 || top < 0 || bottom > 1) return
      }
    }
    onChange(fitCrop({ ...d.c, left, top, right, bottom }, d.c.angle, OW, OH))
  }

  const handle = (kind: string, x: number, y: number) => (
    <div
      key={kind}
      onPointerDown={(e) => {
        e.stopPropagation()
        ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
        drag.current = { kind, x: e.clientX, y: e.clientY, c: crop }
      }}
      onPointerMove={move}
      onPointerUp={() => {
        drag.current = null
        onCommit()
      }}
      className="absolute size-4 -translate-x-1/2 -translate-y-1/2 cursor-pointer rounded-sm border-2 border-white bg-black/40 touch-none"
      style={{ left: x, top: y }}
    />
  )

  return (
    <div className="absolute" style={rect}>
      <svg className="pointer-events-none absolute inset-0 size-full">
        <path
          fillRule="evenodd"
          fill="rgba(0,0,0,0.55)"
          d={`M0 0H${rect.width}V${rect.height}H0Z M${L} ${T}V${B}H${R}V${T}Z`}
        />
        <rect x={L} y={T} width={R - L} height={B - T} fill="none" stroke="white" strokeWidth={1} />
        {[1, 2].map((i) => (
          <g key={i} stroke="rgba(255,255,255,0.35)">
            <line x1={L + ((R - L) * i) / 3} x2={L + ((R - L) * i) / 3} y1={T} y2={B} />
            <line y1={T + ((B - T) * i) / 3} y2={T + ((B - T) * i) / 3} x1={L} x2={R} />
          </g>
        ))}
      </svg>
      <div
        className="absolute cursor-move touch-none"
        style={{ left: L, top: T, width: R - L, height: B - T }}
        onPointerDown={(e) => {
          ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
          drag.current = { kind: 'move', x: e.clientX, y: e.clientY, c: crop }
        }}
        onPointerMove={move}
        onPointerUp={() => {
          drag.current = null
          onCommit()
        }}
      />
      {handle('tl', L, T)}
      {handle('tr', R, T)}
      {handle('bl', L, B)}
      {handle('br', R, B)}
      {handle('t', (L + R) / 2, T)}
      {handle('b', (L + R) / 2, B)}
      {handle('l', L, (T + B) / 2)}
      {handle('r', R, (T + B) / 2)}
    </div>
  )
}
