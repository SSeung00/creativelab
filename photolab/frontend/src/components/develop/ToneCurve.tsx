import { useEffect, useMemo, useRef, useState } from 'react'
import type { Pt } from '@/api/types'
import { cn, Segmented } from '../ui'

/** Natural cubic spline through points (0..255), same as the backend. */
export function splineEval(points: Pt[], n = 256): number[] {
  const pts = [...points].sort((a, b) => a[0] - b[0])
  const xs = pts.map((p) => p[0])
  const ys = pts.map((p) => p[1])
  const k = pts.length
  const out: number[] = []
  if (k === 2) {
    for (let i = 0; i < n; i++) {
      const x = (i / (n - 1)) * 255
      const t = (x - xs[0]) / Math.max(1e-6, xs[1] - xs[0])
      out.push(Math.min(255, Math.max(0, x <= xs[0] ? ys[0] : x >= xs[1] ? ys[1] : ys[0] + t * (ys[1] - ys[0]))))
    }
    return out
  }
  const h = xs.slice(1).map((x, i) => Math.max(1e-6, x - xs[i]))
  const A: number[][] = Array.from({ length: k }, () => new Array(k).fill(0))
  const r = new Array(k).fill(0)
  A[0][0] = A[k - 1][k - 1] = 1
  for (let i = 1; i < k - 1; i++) {
    A[i][i - 1] = h[i - 1]
    A[i][i] = 2 * (h[i - 1] + h[i])
    A[i][i + 1] = h[i]
    r[i] = 3 * ((ys[i + 1] - ys[i]) / h[i] - (ys[i] - ys[i - 1]) / h[i - 1])
  }
  // Gaussian elimination (tiny system)
  for (let i = 0; i < k; i++) {
    let p = i
    for (let j = i + 1; j < k; j++) if (Math.abs(A[j][i]) > Math.abs(A[p][i])) p = j
    ;[A[i], A[p]] = [A[p], A[i]]
    ;[r[i], r[p]] = [r[p], r[i]]
    for (let j = i + 1; j < k; j++) {
      const f = A[j][i] / A[i][i]
      for (let c = i; c < k; c++) A[j][c] -= f * A[i][c]
      r[j] -= f * r[i]
    }
  }
  const c = new Array(k).fill(0)
  for (let i = k - 1; i >= 0; i--) {
    let s = r[i]
    for (let j = i + 1; j < k; j++) s -= A[i][j] * c[j]
    c[i] = s / A[i][i]
  }
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 255
    let y: number
    if (x <= xs[0]) y = ys[0]
    else if (x >= xs[k - 1]) y = ys[k - 1]
    else {
      let s = 0
      while (s < k - 2 && x > xs[s + 1]) s++
      const dx = x - xs[s]
      const b = (ys[s + 1] - ys[s]) / h[s] - (h[s] * (2 * c[s] + c[s + 1])) / 3
      const d = (c[s + 1] - c[s]) / (3 * h[s])
      y = ys[s] + b * dx + c[s] * dx * dx + d * dx ** 3
    }
    out.push(Math.min(255, Math.max(0, y)))
  }
  return out
}

type Ch = 'curve' | 'curve_r' | 'curve_g' | 'curve_b'
const CH_COLOR: Record<Ch, string> = { curve: '#e5e5e5', curve_r: '#ef5350', curve_g: '#66bb6a', curve_b: '#42a5f5' }

export function ToneCurve({
  curves,
  onChange,
  onCommit,
  hist,
}: {
  curves: Record<Ch, Pt[]>
  onChange: (ch: Ch, pts: Pt[]) => void
  onCommit: () => void
  hist: Uint32Array | null
}) {
  const [ch, setCh] = useState<Ch>('curve')
  const ref = useRef<HTMLCanvasElement>(null)
  const drag = useRef<number | null>(null)
  const pts = curves[ch]
  const curve = useMemo(() => splineEval(pts), [pts])

  useEffect(() => {
    const c = ref.current
    if (!c) return
    const ctx = c.getContext('2d')!
    const S = c.width
    ctx.clearRect(0, 0, S, S)
    ctx.fillStyle = '#161616'
    ctx.fillRect(0, 0, S, S)
    if (hist) {
      const n = hist.length / 3
      const lum = new Array(n).fill(0)
      for (let i = 0; i < n; i++) lum[i] = hist[i] * 0.3 + hist[n + i] * 0.59 + hist[2 * n + i] * 0.11
      const max = Math.max(...lum.slice(1, -1), 1)
      ctx.fillStyle = 'rgba(255,255,255,0.07)'
      ctx.beginPath()
      ctx.moveTo(0, S)
      lum.forEach((v, i) => ctx.lineTo((i / (n - 1)) * S, S - Math.sqrt(Math.min(1, v / max)) * S * 0.9))
      ctx.lineTo(S, S)
      ctx.fill()
    }
    ctx.strokeStyle = '#2e2e2e'
    ctx.lineWidth = 1
    for (let i = 1; i < 4; i++) {
      ctx.beginPath()
      ctx.moveTo((i * S) / 4, 0)
      ctx.lineTo((i * S) / 4, S)
      ctx.moveTo(0, (i * S) / 4)
      ctx.lineTo(S, (i * S) / 4)
      ctx.stroke()
    }
    ctx.strokeStyle = '#3a3a3a'
    ctx.beginPath()
    ctx.moveTo(0, S)
    ctx.lineTo(S, 0)
    ctx.stroke()
    ctx.strokeStyle = CH_COLOR[ch]
    ctx.lineWidth = 2
    ctx.beginPath()
    curve.forEach((y, i) => {
      const X = (i / 255) * S
      const Y = S - (y / 255) * S
      if (i) ctx.lineTo(X, Y)
      else ctx.moveTo(X, Y)
    })
    ctx.stroke()
    for (const [x, y] of pts) {
      ctx.fillStyle = '#fff'
      ctx.beginPath()
      ctx.arc((x / 255) * S, S - (y / 255) * S, 5, 0, Math.PI * 2)
      ctx.fill()
    }
  }, [curve, pts, ch, hist])

  const toVal = (e: React.PointerEvent): Pt => {
    const r = ref.current!.getBoundingClientRect()
    const x = Math.round(Math.min(255, Math.max(0, ((e.clientX - r.left) / r.width) * 255)))
    const y = Math.round(Math.min(255, Math.max(0, (1 - (e.clientY - r.top) / r.height) * 255)))
    return [x, y]
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <Segmented
          value={ch}
          onChange={setCh}
          options={[
            { value: 'curve', label: 'RGB' },
            { value: 'curve_r', label: <span className="text-red-400">R</span> },
            { value: 'curve_g', label: <span className="text-green-400">G</span> },
            { value: 'curve_b', label: <span className="text-blue-400">B</span> },
          ]}
        />
        <button
          className="text-[11px] text-subtle hover:text-fg"
          onClick={() => {
            onChange(ch, [[0, 0], [255, 255]])
            onCommit()
          }}
        >
          초기화
        </button>
      </div>
      <canvas
        ref={ref}
        width={512}
        height={512}
        className={cn('aspect-square w-full touch-none rounded')}
        onPointerDown={(e) => {
          const [x, y] = toVal(e)
          let i = pts.findIndex(([px, py]) => Math.abs(px - x) < 10 && Math.abs(py - y) < 14)
          if (e.detail === 2 && i > 0 && i < pts.length - 1) {
            onChange(ch, pts.filter((_, j) => j !== i))
            onCommit()
            return
          }
          let next = pts
          if (i < 0) {
            next = [...pts, [x, y] as Pt].sort((a, b) => a[0] - b[0])
            i = next.findIndex((p) => p[0] === x && p[1] === y)
            onChange(ch, next)
          }
          drag.current = i
          ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
        }}
        onPointerMove={(e) => {
          if (drag.current == null) return
          const i = drag.current
          let [x, y] = toVal(e)
          const cur = [...pts] as Pt[]
          const lo = i > 0 ? cur[i - 1][0] + 1 : 0
          const hi = i < cur.length - 1 ? cur[i + 1][0] - 1 : 255
          if (i === 0) x = Math.min(x, hi)
          else if (i === cur.length - 1) x = Math.max(x, lo)
          else x = Math.min(hi, Math.max(lo, x))
          cur[i] = [x, y]
          onChange(ch, cur)
        }}
        onPointerUp={() => {
          if (drag.current != null) onCommit()
          drag.current = null
        }}
      />
      <p className="mt-1 text-[11px] text-subtle">클릭: 점 추가 · 끌기: 이동 · 더블클릭: 점 삭제</p>
    </div>
  )
}
