export interface Root {
  id: number
  path: string
  name: string
  count: number
  scanned_at: string | null
}

export interface Folder {
  folder: string
  count: number
  first: string | null
}

export type Label = 'red' | 'yellow' | 'green' | 'blue' | 'purple'

export interface Photo {
  id: number
  root_id: number
  folder: string
  filename: string
  ext: string
  time: string | null
  rating: number
  flag: number
  label: Label | null
  edited: boolean
  rev: number
  orientation: number
  copy: string | null
  master_id: number | null
  pair: boolean
}

export interface PhotoDetail extends Photo {
  path: string
  size: number
  make: string | null
  model: string | null
  lens: string | null
  focal_length: number | null
  aperture: number | null
  shutter: string | null
  iso: number | null
  exposure_bias: number | null
}

export interface Job {
  id: number
  kind: string
  title: string
  status: 'queued' | 'running' | 'done' | 'error' | 'cancelled'
  done: number
  total: number
  message: string | null
  result: string | null
  created_at: string
  finished_at: string | null
}

export type Pt = [number, number]

export interface GradeRegion {
  hue: number
  sat: number
  lum: number
}

export interface MaskComponent {
  type: 'linear' | 'radial' | 'brush' | 'ai' | 'range'
  mode?: 'add' | 'subtract' | 'intersect'
  invert?: boolean
  // linear
  x0?: number
  y0?: number
  x1?: number
  y1?: number
  // radial
  cx?: number
  cy?: number
  rx?: number
  ry?: number
  angle?: number
  feather?: number
  // brush
  strokes?: { pts: Pt[]; size: number; feather: number; flow: number; erase?: boolean }[]
  // ai
  kind?: 'subject' | 'sky' | 'person' | 'background'
}

export interface Mask {
  id: string
  name: string
  visible: boolean
  invert?: boolean
  amount: number
  components: MaskComponent[]
  adjust: Record<string, number>
}

export interface Params {
  version: number
  profile: string
  profile_amount: number
  wb: 'as_shot' | 'custom' | 'auto'
  temp: number | null
  tint: number | null
  exposure: number
  contrast: number
  highlights: number
  shadows: number
  whites: number
  blacks: number
  texture: number
  clarity: number
  dehaze: number
  vibrance: number
  saturation: number
  curve: Pt[]
  curve_r: Pt[]
  curve_g: Pt[]
  curve_b: Pt[]
  parametric: { highlights: number; lights: number; darks: number; shadows: number; split_shadows: number; split_midtones: number; split_highlights: number }
  hsl: { hue: number[]; sat: number[]; lum: number[] }
  bw: boolean
  bw_mix: number[]
  grading: { shadows: GradeRegion; midtones: GradeRegion; highlights: GradeRegion; global: GradeRegion; blending: number; balance: number }
  sharpen: { amount: number; radius: number; detail: number; masking: number }
  nr: { luminance: number; lum_detail: number; lum_contrast: number; color: number; color_detail: number; color_smooth: number }
  ai_denoise: { enabled: boolean; amount: number }
  lens: { profile: boolean; distortion: number; vignetting: number; ca: boolean; manual_distortion: number }
  transform: { vertical: number; horizontal: number; rotate: number; aspect: number; scale: number; x: number; y: number }
  crop: { left: number; top: number; right: number; bottom: number; angle: number; aspect: number | null }
  rotate90: number
  flip: boolean
  vignette: { amount: number; midpoint: number; roundness: number; feather: number; highlights: number }
  grain: { amount: number; size: number; roughness: number }
  masks: Mask[]
}

export interface DevelopData {
  params: Params
  defaults: Params
  info: {
    is_raw: boolean
    width: number
    height: number
    as_shot?: { temp: number; tint: number }
    lens_profile: string | null
    oriented: [number, number]
  }
  profiles: { name: string; group: string }[]
  history: { id: number; label: string; at: string }[]
  snapshots: { id: number; name: string; at: string }[]
  rev: number
}

export interface RenderInfo {
  w: number
  h: number
  crop_px: [number, number]
  oriented: [number, number]
  as_shot: { temp: number; tint: number }
  wb: { temp: number; tint: number }
  clipped: [number, number] | null
  missing_ai: string[]
  missing_denoise?: boolean
}

export interface Preset {
  id: number
  name: string
  group: string
  params: Partial<Params>
}
