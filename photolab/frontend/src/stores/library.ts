import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface LibraryState {
  rootId: number | null
  folder: string | null // null = whole root
  minRating: number
  flag: string | null
  edited: boolean | null
  label: string | null
  q: string
  sort: 'time' | 'name' | 'rating'
  thumbSize: number
  // selection
  current: number | null
  selected: number[]
  set: (p: Partial<LibraryState>) => void
  select: (ids: number[], current?: number | null) => void
}

export const useLibrary = create<LibraryState>()(
  persist(
    (set) => ({
      rootId: null,
      folder: null,
      minRating: 0,
      flag: null,
      edited: null,
      label: null,
      q: '',
      sort: 'time',
      thumbSize: 180,
      current: null,
      selected: [],
      set: (p) => set(p),
      select: (ids, current) => set((s) => ({ selected: ids, current: current === undefined ? s.current : current })),
    }),
    {
      name: 'photolab-library',
      partialize: (s) => ({
        rootId: s.rootId,
        folder: s.folder,
        minRating: s.minRating,
        flag: s.flag,
        edited: s.edited,
        label: s.label,
        sort: s.sort,
        thumbSize: s.thumbSize,
        current: s.current,
      }),
    },
  ),
)

/** Settings copied with "copy settings" for pasting onto other photos. */
interface ClipState {
  clip: { from: number; groups: string[] } | null
  setClip: (c: ClipState['clip']) => void
}
export const useClipboard = create<ClipState>()((set) => ({ clip: null, setClip: (clip) => set({ clip }) }))
