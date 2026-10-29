import { useEffect, useRef } from 'react'

export function Histogram({ hist, clipped, onToggleClip, showClip }: {
  hist: Uint32Array | null
  clipped: [number, number] | null
  showClip?: boolean
  onToggleClip?: () => void
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current
    if (!c || !hist) return
    const ctx = c.getContext('2d')!
    const W = c.width
    const H = c.height
    ctx.clearRect(0, 0, W, H)
    const n = hist.length / 3
    let max = 1
    for (let i = 2; i < hist.length; i++) if (i % n > 1 && i % n < n - 2) max = Math.max(max, hist[i])
    ctx.globalCompositeOperation = 'lighter'
    const colors = ['rgba(230,70,70,0.75)', 'rgba(70,200,90,0.75)', 'rgba(70,110,240,0.75)']
    for (let ch = 0; ch < 3; ch++) {
      ctx.fillStyle = colors[ch]
      ctx.beginPath()
      ctx.moveTo(0, H)
      for (let i = 0; i < n; i++) {
        const v = Math.sqrt(Math.min(1, hist[ch * n + i] / max))
        ctx.lineTo((i / (n - 1)) * W, H - v * (H - 2))
      }
      ctx.lineTo(W, H)
      ctx.closePath()
      ctx.fill()
    }
    ctx.globalCompositeOperation = 'source-over'
  }, [hist])
  return (
    <div className="relative">
      <canvas ref={ref} width={512} height={140} className="h-[90px] w-full rounded bg-[#151515]" />
      {clipped && (
        <button
          onClick={onToggleClip}
          title="클리핑 표시"
          className="absolute inset-x-1 top-1 flex justify-between text-[10px]"
        >
          <span className={clipped[1] > 0.002 ? 'text-sky-400' : 'text-subtle'}>{showClip ? '■' : '▲'} 암부 {(clipped[1] * 100).toFixed(1)}%</span>
          <span className={clipped[0] > 0.002 ? 'text-red-400' : 'text-subtle'}>명부 {(clipped[0] * 100).toFixed(1)}% {showClip ? '■' : '▲'}</span>
        </button>
      )}
    </div>
  )
}
