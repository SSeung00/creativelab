import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, del, patch, post, qs } from './client'
import type { DevelopData, Folder, Job, Photo, PhotoDetail, Preset, Root } from './types'

export interface PhotoFilter {
  root_id?: number | null
  folder?: string | null
  min_rating?: number
  flag?: string | null
  edited?: boolean | null
  label?: string | null
  q?: string
  sort?: string
}

export const useRoots = () => useQuery({ queryKey: ['roots'], queryFn: () => api<Root[]>('/roots') })

export const useFolders = (rootId: number | null | undefined) =>
  useQuery({
    queryKey: ['folders', rootId],
    queryFn: () => api<Folder[]>(`/folders${qs({ root_id: rootId })}`),
    enabled: !!rootId,
  })

export const usePhotos = (f: PhotoFilter) =>
  useQuery({
    queryKey: ['photos', f],
    queryFn: () => api<Photo[]>(`/photos${qs({ ...f })}`),
    enabled: f.root_id != null,
    staleTime: 30_000,
  })

export const usePhotoDetail = (id: number | null) =>
  useQuery({ queryKey: ['photo', id], queryFn: () => api<PhotoDetail>(`/photos/${id}`), enabled: !!id })

export function usePatchPhotos() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { ids: number[]; rating?: number; flag?: number; label?: string | null; clear_label?: boolean }) =>
      patch('/photos', body),
    onMutate: async (body) => {
      // Optimistic: culling must feel instant.
      qc.setQueriesData<Photo[]>({ queryKey: ['photos'] }, (old) =>
        old?.map((p) =>
          body.ids.includes(p.id)
            ? {
                ...p,
                ...(body.rating !== undefined ? { rating: body.rating } : {}),
                ...(body.flag !== undefined ? { flag: body.flag } : {}),
                ...(body.clear_label ? { label: null } : body.label !== undefined ? { label: body.label as Photo['label'] } : {}),
              }
            : p,
        ),
      )
    },
  })
}

export const useJobs = (active = false) =>
  useQuery({
    queryKey: ['jobs', active],
    queryFn: () => api<Job[]>(`/jobs${qs({ active })}`),
    refetchInterval: (q) => {
      const data = q.state.data as Job[] | undefined
      return data?.some((j) => j.status === 'running' || j.status === 'queued') ? 700 : 4000
    },
  })

export function waitJob(id: number, onProgress?: (j: Job) => void): Promise<Job> {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const j = await api<Job>(`/jobs/${id}`)
        onProgress?.(j)
        if (j.status === 'done' || j.status === 'cancelled') resolve(j)
        else if (j.status === 'error') reject(new Error(j.message || '작업 실패'))
        else setTimeout(tick, 600)
      } catch (e) {
        reject(e)
      }
    }
    tick()
  })
}

export const useDevelop = (id: number | null) =>
  useQuery({
    queryKey: ['develop', id],
    queryFn: () => api<DevelopData>(`/develop/${id}`),
    enabled: !!id,
    staleTime: 0,
    gcTime: 60_000,
  })

export const usePresets = () => useQuery({ queryKey: ['presets'], queryFn: () => api<Preset[]>('/develop/presets/all') })

export function useAddRoot() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { path: string; name?: string }) => post<{ root: Root; job_id: number }>('/roots', body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['roots'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useRemoveRoot() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => del(`/roots/${id}`),
    onSuccess: () => qc.invalidateQueries(),
  })
}

export const thumbUrl = (p: Pick<Photo, 'id' | 'rev'>) => `/api/photos/${p.id}/thumb?v=${p.rev + 1}`
export const previewUrl = (p: Pick<Photo, 'id' | 'rev'>, size = 2048) => `/api/photos/${p.id}/preview?v=${p.rev + 1}&size=${size}`

export interface AuthStatus {
  local: boolean
  pin_set: boolean
  authed: boolean
  retry_after: number
}
export const useAuthStatus = () =>
  useQuery({ queryKey: ['auth'], queryFn: () => api<AuthStatus>('/auth/status'), staleTime: Infinity })

export interface AiScore {
  sharp: number
  blurry: boolean
  eyes: boolean
  faces: number
  group: number | null
  gsize: number
  best: boolean
  hi: number
}
export const useAiScores = (rootId: number | null | undefined) =>
  useQuery({
    queryKey: ['ai-scores', rootId],
    queryFn: () => api<Record<string, AiScore>>(`/ai/scores${qs({ root_id: rootId })}`),
    enabled: !!rootId,
  })
