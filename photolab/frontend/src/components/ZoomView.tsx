import { useEffect, useRef, useState } from 'react'
import { api } from '@/api/client'
import { previewUrl } from '@/api/hooks'
import type { DevelopData, Photo, RenderInfo } from '@/api/types'
import { Spinner } from './ui'

/** Fit-to-screen preview; click toggles a 1:1 GPU render of the clicked area (drag to pan). */
export function ZoomView({ photo }: { photo: Photo }) {
  const box = useRef<HTMLDivElement>(null)
  const [zoom, setZoom] = useState<{ cx: number; cy: number } | null>(null)
  const [crop, setCrop] = useState<[number, number] | null>(null)
  const [tile, setTile] = useState<string | null>(null)
  const [tileW, setTileW] = useState(0)
  const [loading, setLoading] = useState(false)
  const params = useRef<DevelopData['params'] | null>(null)
  const drag = useRef<{ x: number; y: number; cx: number; cy: number; moved: boolean } | null>(null)

  useEffect(() => () => {
    if (tile) URL.revokeObjectURL(tile)
  }, [tile])

  useEffect(() => {
    if (!zoom || !box.current) return
    let cancelled = false
    const t = setTimeout(async () => {
      setLoading(true)
      try {
        if (!params.current) params.current = (await api<DevelopData>(`/develop/${photo.id}`)).params
        let cp = crop
        if (!cp) {
          const r = await fetch(`/api/develop/${photo.id}/render`, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ params: params.current, w: 32, h: 32, hist: false }),
          })
          const info = JSON.parse(atob(r.headers.get('X-Info') || '')) as RenderInfo
          cp = info.crop_px
          setCrop(cp)
        }
        const el = box.current!
        const dpr = Math.min(window.devicePixelRatio || 1, 2)
        const vw = el.clientWidth * dpr
        const vh = el.clientHeight * dpr
        const rw = Math.min(1, vw / cp[0])
        const rh = Math.min(1, vh / cp[1])
        const x0 = Math.max(0, Math.min(1 - rw, zoom.cx - rw / 2))
        const y0 = Math.max(0, Math.min(1 - rh, zoom.cy - rh / 2))
        const r = await fetch(`/api/develop/${photo.id}/render`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            params: params.current,
            w: Math.round(rw * cp[0]),
            h: Math.round(rh * cp[1]),
            region: [x0, y0, x0 + rw, y0 + rh],
            hist: false,
            quality: 92,
          }),
        })
        if (!cancelled && r.ok) {
          setTileW(Math.round(rw * cp[0]) / dpr)
          setTile(URL.createObjectURL(await r.blob()))
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }, 60)
    return () => {
      cancelled = true
      clearTimeout(t)
    }
  }, [zoom, photo.id, crop])

  const onClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (drag.current?.moved) return
    if (zoom) {
      setZoom(null)
      setTile(null)
      return
    }
    const img = box.current?.querySelector('img')
    if (!img) return
    const r = img.getBoundingClientRect()
    const cx = (e.clientX - r.left) / r.width
    const cy = (e.clientY - r.top) / r.height
    if (cx < 0 || cx > 1 || cy < 0 || cy > 1) return
    setZoom({ cx, cy })
  }

  return (
    <div
      ref={box}
      className="absolute inset-0 flex cursor-zoom-in items-center justify-center overflow-hidden"
      onClick={onClick}
      onPointerDown={(e) => {
        if (zoom) drag.current = { x: e.clientX, y: e.clientY, cx: zoom.cx, cy: zoom.cy, moved: false }
      }}
      onPointerMove={(e) => {
        const d = drag.current
        if (!d || !zoom || !crop || !box.current) return
        const dx = e.clientX - d.x
        const dy = e.clientY - d.y
        if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true
        if (d.moved) setZoom({ cx: d.cx - dx / crop[0], cy: d.cy - dy / crop[1] })
      }}
      onPointerUp={() => {
        setTimeout(() => (drag.current = null), 0)
      }}
    >
      <img
        src={previewUrl(photo)}
        className="max-h-full max-w-full object-contain"
        style={{ visibility: zoom && tile ? 'hidden' : 'visible' }}
        draggable={false}
        alt=""
      />
      {zoom && tile && <img src={tile} className="absolute max-h-none max-w-none cursor-grab" style={{ width: tileW || undefined }} draggable={false} alt="" />}
      {loading && <Spinner className="absolute right-3 top-3" />}
    </div>
  )
}
