import { useEffect, useRef, useState } from 'react'
import type { Params, RenderInfo } from '@/api/types'

export interface RenderOut {
  url: string | null
  info: RenderInfo | null
  hist: Uint32Array | null
  busy: boolean
  error: string | null
}

export interface RenderReq {
  params: Params
  w: number
  h: number
  region?: [number, number, number, number] | null
  mask_overlay?: number | null
  nonce?: number
}

/**
 * Latest-wins live renderer: at most one request in flight; while it runs, newer
 * parameter sets replace each other and only the last one is sent next.
 */
export function useLiveRender(photoId: number | null, req: RenderReq | null): RenderOut {
  const [out, setOut] = useState<RenderOut>({ url: null, info: null, hist: null, busy: false, error: null })
  const inflight = useRef(false)
  const pending = useRef<RenderReq | null>(null)
  const lastUrl = useRef<string | null>(null)
  const idRef = useRef(photoId)
  idRef.current = photoId

  useEffect(() => {
    return () => {
      if (lastUrl.current) URL.revokeObjectURL(lastUrl.current)
    }
  }, [])

  useEffect(() => {
    if (!photoId || !req || req.w < 8 || req.h < 8) return
    pending.current = req
    const pump = async () => {
      if (inflight.current || !pending.current) return
      const r = pending.current
      pending.current = null
      inflight.current = true
      const forId = idRef.current
      setOut((o) => ({ ...o, busy: true }))
      try {
        const res = await fetch(`/api/develop/${forId}/render`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            params: r.params,
            w: Math.round(r.w),
            h: Math.round(r.h),
            region: r.region ?? null,
            hist: true,
            mask_overlay: r.mask_overlay ?? null,
          }),
        })
        if (!res.ok) {
          let msg = `${res.status}`
          try {
            msg = (await res.json()).detail ?? msg
          } catch {
            /* ignore */
          }
          throw new Error(msg)
        }
        const blob = await res.blob()
        if (forId !== idRef.current) return
        const info = JSON.parse(atob(res.headers.get('X-Info') || 'e30=')) as RenderInfo
        const h64 = res.headers.get('X-Hist')
        let hist: Uint32Array | null = null
        if (h64) {
          const bin = Uint8Array.from(atob(h64), (c) => c.charCodeAt(0))
          hist = new Uint32Array(bin.buffer)
        }
        const url = URL.createObjectURL(blob)
        if (lastUrl.current) {
          const old = lastUrl.current
          setTimeout(() => URL.revokeObjectURL(old), 2000)
        }
        lastUrl.current = url
        setOut({ url, info, hist, busy: false, error: null })
      } catch (e) {
        setOut((o) => ({ ...o, busy: false, error: (e as Error).message }))
      } finally {
        inflight.current = false
        if (pending.current) pump()
      }
    }
    pump()
  }, [photoId, req])

  return out
}
