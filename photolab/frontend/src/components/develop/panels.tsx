import { Pipette, Wand2 } from 'lucide-react'
import { useState } from 'react'
import type { DevelopData, Params, Pt } from '@/api/types'
import { cn, IconButton, Section, Segmented, Slider, Toggle } from '../ui'
import { ToneCurve } from './ToneCurve'

export type Upd = (path: string, value: unknown, label?: string) => void
export type Commit = (label: string) => void

export function getIn(obj: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((o, k) => (o == null ? o : (o as Record<string, unknown>)[k]), obj)
}

export function setIn<T>(obj: T, path: string, value: unknown): T {
  const keys = path.split('.')
  const clone = (o: unknown) => (Array.isArray(o) ? [...o] : { ...(o as object) })
  const root = clone(obj) as Record<string, unknown>
  let cur = root
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i]
    cur[k] = clone(cur[k])
    cur = cur[k] as Record<string, unknown>
  }
  cur[keys[keys.length - 1]] = value
  return root as T
}

const LABELS: Record<string, string> = {
  exposure: '노출', contrast: '대비', highlights: '밝은 영역', shadows: '어두운 영역', whites: '흰색 계열',
  blacks: '검정 계열', texture: '텍스처', clarity: '부분 대비', dehaze: '디헤이즈', vibrance: '생동감',
  saturation: '채도',
}

function P({
  p, d, path, label, min = -100, max = 100, step = 1, upd, commit, track, unsigned, suffix,
}: {
  p: Params
  d: Params
  path: string
  label: string
  min?: number
  max?: number
  step?: number
  upd: Upd
  commit: Commit
  track?: string
  unsigned?: boolean
  suffix?: string
}) {
  return (
    <Slider
      label={label}
      value={Number(getIn(p, path) ?? 0)}
      def={Number(getIn(d, path) ?? 0)}
      min={min}
      max={max}
      step={step}
      track={track}
      unsigned={unsigned}
      suffix={suffix}
      onChange={(v) => upd(path, v)}
      onCommit={() => commit(label)}
    />
  )
}

// ------------------------------------------------------------------ Basic

const TEMP_MIN = 2000
const TEMP_MAX = 50000
// Lightroom-like non-linear temperature slider (more room for 2500-9000K)
const tToS = (t: number) => Math.log(t / TEMP_MIN) / Math.log(TEMP_MAX / TEMP_MIN)
const sToT = (s: number) => Math.round((TEMP_MIN * Math.pow(TEMP_MAX / TEMP_MIN, s)) / 10) * 10

export function BasicPanel({
  p, d, upd, commit, data, wbNow, picking, setPicking, onAuto,
}: {
  p: Params
  d: Params
  upd: Upd
  commit: Commit
  data: DevelopData
  wbNow: { temp: number; tint: number } | null
  picking: boolean
  setPicking: (v: boolean) => void
  onAuto: () => void
}) {
  const groups = data.profiles.reduce<Record<string, string[]>>((acc, x) => {
    ;(acc[x.group] ||= []).push(x.name)
    return acc
  }, {})
  const temp = p.wb === 'custom' && p.temp ? p.temp : wbNow?.temp ?? data.info.as_shot?.temp ?? 5500
  const tint = p.wb === 'custom' && p.tint != null ? p.tint : wbNow?.tint ?? data.info.as_shot?.tint ?? 0
  const setWB = (t: number, ti: number) => {
    upd('wb', 'custom')
    upd('temp', t)
    upd('tint', ti)
  }
  return (
    <Section title="기본" id="basic">
      <div className="mb-2 flex items-center gap-2">
        <span className="w-[68px] text-[12px] text-muted">프로파일</span>
        <select
          className="h-7 flex-1 rounded-md border border-line-strong bg-panel-2 px-2 text-[12px]"
          value={p.profile}
          onChange={(e) => {
            upd('profile', e.target.value)
            commit('프로파일')
          }}
        >
          {Object.entries(groups).map(([g, names]) => (
            <optgroup key={g} label={g}>
              {names.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </optgroup>
          ))}
          {!data.profiles.some((x) => x.name === p.profile) && <option value={p.profile}>{p.profile}</option>}
        </select>
      </div>
      {data.info.is_raw && (
        <>
          <div className="mb-1 flex items-center gap-2">
            <span className="w-[68px] text-[12px] text-muted">흰색 균형</span>
            <select
              className="h-7 flex-1 rounded-md border border-line-strong bg-panel-2 px-2 text-[12px]"
              value={p.wb}
              onChange={(e) => {
                const v = e.target.value as Params['wb']
                upd('wb', v)
                if (v !== 'custom') {
                  upd('temp', null)
                  upd('tint', null)
                } else {
                  upd('temp', temp)
                  upd('tint', tint)
                }
                commit('흰색 균형')
              }}
            >
              <option value="as_shot">촬영 설정</option>
              <option value="auto">자동</option>
              <option value="custom">사용자 지정</option>
            </select>
            <IconButton active={picking} onClick={() => setPicking(!picking)} title="흰색 균형 스포이드 (W)">
              <Pipette className="size-4" />
            </IconButton>
          </div>
          <Slider
            label="색온도"
            value={tToS(temp)}
            def={tToS(data.info.as_shot?.temp ?? 5500)}
            min={0}
            max={1}
            step={0.001}
            track="linear-gradient(90deg,#3d7bd6,#e8e0c0,#e3a33a)"
            onChange={(s) => setWB(sToT(s), tint)}
            onCommit={() => commit('색온도')}
            format={(s) => String(sToT(s))}
          />
          <Slider
            label="색조"
            value={tint}
            def={data.info.as_shot?.tint ?? 0}
            min={-150}
            max={150}
            track="linear-gradient(90deg,#4caf50,#ddd,#c040c0)"
            onChange={(v) => setWB(temp, v)}
            onCommit={() => commit('색조')}
          />
        </>
      )}
      <div className="my-2 flex items-center justify-between border-t border-line pt-2">
        <span className="text-[11px] font-semibold text-subtle">톤</span>
        <button className="flex items-center gap-1 text-[11px] text-accent hover:brightness-110" onClick={onAuto}>
          <Wand2 className="size-3" /> 자동
        </button>
      </div>
      <P p={p} d={d} upd={upd} commit={commit} path="exposure" label={LABELS.exposure} min={-5} max={5} step={0.05} />
      <P p={p} d={d} upd={upd} commit={commit} path="contrast" label={LABELS.contrast} />
      <P p={p} d={d} upd={upd} commit={commit} path="highlights" label={LABELS.highlights} />
      <P p={p} d={d} upd={upd} commit={commit} path="shadows" label={LABELS.shadows} />
      <P p={p} d={d} upd={upd} commit={commit} path="whites" label={LABELS.whites} />
      <P p={p} d={d} upd={upd} commit={commit} path="blacks" label={LABELS.blacks} />
      <div className="my-2 border-t border-line pt-2 text-[11px] font-semibold text-subtle">외관</div>
      <P p={p} d={d} upd={upd} commit={commit} path="texture" label={LABELS.texture} />
      <P p={p} d={d} upd={upd} commit={commit} path="clarity" label={LABELS.clarity} />
      <P p={p} d={d} upd={upd} commit={commit} path="dehaze" label={LABELS.dehaze} />
      <P p={p} d={d} upd={upd} commit={commit} path="vibrance" label={LABELS.vibrance} />
      <P p={p} d={d} upd={upd} commit={commit} path="saturation" label={LABELS.saturation} />
    </Section>
  )
}

// ------------------------------------------------------------------ Curve

export function CurvePanel({ p, d, upd, commit, hist }: { p: Params; d: Params; upd: Upd; commit: Commit; hist: Uint32Array | null }) {
  const [mode, setMode] = useState<'point' | 'param'>('point')
  return (
    <Section title="톤 곡선" id="curve" defaultOpen={false}>
      <Segmented
        className="mb-2"
        value={mode}
        onChange={setMode}
        options={[
          { value: 'point', label: '점 곡선' },
          { value: 'param', label: '영역' },
        ]}
      />
      {mode === 'point' ? (
        <ToneCurve
          curves={{ curve: p.curve, curve_r: p.curve_r, curve_g: p.curve_g, curve_b: p.curve_b }}
          onChange={(ch, pts: Pt[]) => upd(ch, pts)}
          onCommit={() => commit('톤 곡선')}
          hist={hist}
        />
      ) : (
        <>
          <P p={p} d={d} upd={upd} commit={commit} path="parametric.highlights" label="밝은 영역" />
          <P p={p} d={d} upd={upd} commit={commit} path="parametric.lights" label="밝음" />
          <P p={p} d={d} upd={upd} commit={commit} path="parametric.darks" label="어두움" />
          <P p={p} d={d} upd={upd} commit={commit} path="parametric.shadows" label="어두운 영역" />
        </>
      )}
    </Section>
  )
}

// ------------------------------------------------------------------ HSL

const BANDS = ['빨강', '주황', '노랑', '초록', '아쿠아', '파랑', '보라', '마젠타']
const BAND_COLORS = ['#e53935', '#fb8c00', '#fdd835', '#43a047', '#00acc1', '#1e88e5', '#8e24aa', '#d81b60']

export function ColorPanel({ p, d, upd, commit }: { p: Params; d: Params; upd: Upd; commit: Commit }) {
  const [tab, setTab] = useState<'hue' | 'sat' | 'lum' | 'all'>('hue')
  const tabs: ('hue' | 'sat' | 'lum')[] = tab === 'all' ? ['hue', 'sat', 'lum'] : [tab]
  const names = { hue: '색조', sat: '채도', lum: '휘도' }
  return (
    <Section title="색상 혼합" id="hsl" defaultOpen={false} right={<Toggle checked={p.bw} onChange={(v) => (upd('bw', v), commit('흑백'))} label="흑백" />}>
      {p.bw ? (
        BANDS.map((b, i) => (
          <P key={b} p={p} d={d} upd={upd} commit={commit} path={`bw_mix.${i}`} label={b} track={BAND_COLORS[i]} />
        ))
      ) : (
        <>
          <Segmented
            className="mb-2"
            value={tab}
            onChange={setTab}
            options={[
              { value: 'hue', label: '색조' },
              { value: 'sat', label: '채도' },
              { value: 'lum', label: '휘도' },
              { value: 'all', label: '전체' },
            ]}
          />
          {tabs.map((t) => (
            <div key={t} className={cn(tab === 'all' && 'mb-2')}>
              {tab === 'all' && <div className="mb-1 text-[11px] text-subtle">{names[t]}</div>}
              {BANDS.map((b, i) => (
                <P
                  key={b}
                  p={p}
                  d={d}
                  upd={upd}
                  commit={commit}
                  path={`hsl.${t}.${i}`}
                  label={b}
                  track={
                    t === 'hue'
                      ? `linear-gradient(90deg, ${BAND_COLORS[(i + 7) % 8]}, ${BAND_COLORS[i]}, ${BAND_COLORS[(i + 1) % 8]})`
                      : t === 'sat'
                        ? `linear-gradient(90deg, #888, ${BAND_COLORS[i]})`
                        : `linear-gradient(90deg, #222, ${BAND_COLORS[i]}, #fff)`
                  }
                />
              ))}
            </div>
          ))}
        </>
      )}
    </Section>
  )
}

// ------------------------------------------------------------------ Grading

const HUE_TRACK = 'linear-gradient(90deg,#f44,#fa4,#ff4,#4f4,#4ff,#44f,#f4f,#f44)'

export function GradingPanel({ p, d, upd, commit }: { p: Params; d: Params; upd: Upd; commit: Commit }) {
  const [region, setRegion] = useState<'shadows' | 'midtones' | 'highlights' | 'global'>('midtones')
  const g = p.grading[region]
  const swatch = (r: typeof region) => {
    const x = p.grading[r]
    return `hsl(${x.hue} ${Math.min(100, x.sat * 2)}% 50%)`
  }
  return (
    <Section title="색 보정 (컬러 그레이딩)" id="grading" defaultOpen={false}>
      <div className="mb-2 grid grid-cols-4 gap-1">
        {(['shadows', 'midtones', 'highlights', 'global'] as const).map((r) => (
          <button
            key={r}
            onClick={() => setRegion(r)}
            className={cn('flex flex-col items-center gap-1 rounded-md py-1.5 text-[11px] text-muted', region === r && 'bg-panel-3 text-fg')}
          >
            <span className="size-5 rounded-full border border-line-strong" style={{ background: swatch(r) }} />
            {{ shadows: '어두운', midtones: '중간', highlights: '밝은', global: '전체' }[r]}
          </button>
        ))}
      </div>
      <Slider label="색조" value={g.hue} def={0} min={0} max={360} track={HUE_TRACK} unsigned onChange={(v) => upd(`grading.${region}.hue`, v)} onCommit={() => commit('컬러 그레이딩')} />
      <P p={p} d={d} upd={upd} commit={commit} path={`grading.${region}.sat`} label="채도" min={0} max={100} unsigned />
      <P p={p} d={d} upd={upd} commit={commit} path={`grading.${region}.lum`} label="휘도" />
      <div className="mt-2 border-t border-line pt-2">
        <P p={p} d={d} upd={upd} commit={commit} path="grading.blending" label="혼합" min={0} max={100} unsigned />
        <P p={p} d={d} upd={upd} commit={commit} path="grading.balance" label="균형" />
      </div>
    </Section>
  )
}

// ------------------------------------------------------------------ Detail

export function DetailPanel({ p, d, upd, commit, onAiDenoise }: { p: Params; d: Params; upd: Upd; commit: Commit; onAiDenoise?: () => void }) {
  return (
    <Section title="디테일" id="detail" defaultOpen={false}>
      <div className="mb-1 text-[11px] font-semibold text-subtle">선명하게</div>
      <P p={p} d={d} upd={upd} commit={commit} path="sharpen.amount" label="양" min={0} max={150} unsigned />
      <P p={p} d={d} upd={upd} commit={commit} path="sharpen.radius" label="반경" min={0.5} max={3} step={0.1} unsigned />
      <P p={p} d={d} upd={upd} commit={commit} path="sharpen.detail" label="세부" min={0} max={100} unsigned />
      <P p={p} d={d} upd={upd} commit={commit} path="sharpen.masking" label="마스킹" min={0} max={100} unsigned />
      <div className="mb-1 mt-2 flex items-center justify-between border-t border-line pt-2">
        <span className="text-[11px] font-semibold text-subtle">노이즈 감소</span>
        {onAiDenoise && (
          <Toggle checked={p.ai_denoise.enabled} onChange={(v) => (upd('ai_denoise.enabled', v), commit('AI 노이즈 제거'))} label="AI 노이즈 제거" />
        )}
      </div>
      {p.ai_denoise.enabled ? (
        <P p={p} d={d} upd={upd} commit={commit} path="ai_denoise.amount" label="AI 강도" min={0} max={100} unsigned />
      ) : (
        <>
          <P p={p} d={d} upd={upd} commit={commit} path="nr.luminance" label="광도" min={0} max={100} unsigned />
          <P p={p} d={d} upd={upd} commit={commit} path="nr.lum_detail" label="광도 세부" min={0} max={100} unsigned />
        </>
      )}
      <P p={p} d={d} upd={upd} commit={commit} path="nr.color" label="색상" min={0} max={100} unsigned />
      <P p={p} d={d} upd={upd} commit={commit} path="nr.color_detail" label="색상 세부" min={0} max={100} unsigned />
    </Section>
  )
}

// ------------------------------------------------------------------ Optics / geometry / effects

export function OpticsPanel({ p, d, upd, commit, lensName }: { p: Params; d: Params; upd: Upd; commit: Commit; lensName: string | null }) {
  return (
    <Section title="렌즈 교정" id="optics" defaultOpen={false}>
      <div className="mb-2 flex flex-col gap-1.5">
        <Toggle checked={p.lens.profile} onChange={(v) => (upd('lens.profile', v), commit('렌즈 프로파일'))} label="프로파일 교정 사용" />
        <span className="pl-9 text-[11px] text-subtle">{lensName ?? '맞는 렌즈 프로파일이 없어요'}</span>
        <Toggle checked={p.lens.ca} onChange={(v) => (upd('lens.ca', v), commit('색수차'))} label="색수차 제거 (준비 중)" />
      </div>
      {p.lens.profile && (
        <>
          <P p={p} d={d} upd={upd} commit={commit} path="lens.distortion" label="왜곡" min={0} max={200} unsigned />
          <P p={p} d={d} upd={upd} commit={commit} path="lens.vignetting" label="비네팅" min={0} max={200} unsigned />
        </>
      )}
      <P p={p} d={d} upd={upd} commit={commit} path="lens.manual_distortion" label="수동 왜곡" />
    </Section>
  )
}

export function TransformPanel({ p, d, upd, commit }: { p: Params; d: Params; upd: Upd; commit: Commit }) {
  return (
    <Section title="변형" id="transform" defaultOpen={false}>
      <P p={p} d={d} upd={upd} commit={commit} path="transform.vertical" label="세로" />
      <P p={p} d={d} upd={upd} commit={commit} path="transform.horizontal" label="가로" />
      <P p={p} d={d} upd={upd} commit={commit} path="transform.rotate" label="회전" min={-10} max={10} step={0.1} />
      <P p={p} d={d} upd={upd} commit={commit} path="transform.aspect" label="종횡비" />
      <P p={p} d={d} upd={upd} commit={commit} path="transform.scale" label="비율" min={50} max={150} unsigned />
      <P p={p} d={d} upd={upd} commit={commit} path="transform.x" label="X 오프셋" min={-100} max={100} step={0.5} />
      <P p={p} d={d} upd={upd} commit={commit} path="transform.y" label="Y 오프셋" min={-100} max={100} step={0.5} />
    </Section>
  )
}

export function EffectsPanel({ p, d, upd, commit }: { p: Params; d: Params; upd: Upd; commit: Commit }) {
  return (
    <Section title="효과" id="effects" defaultOpen={false}>
      <div className="mb-1 text-[11px] font-semibold text-subtle">자르기 후 비네팅</div>
      <P p={p} d={d} upd={upd} commit={commit} path="vignette.amount" label="양" />
      <P p={p} d={d} upd={upd} commit={commit} path="vignette.midpoint" label="중간점" min={0} max={100} unsigned />
      <P p={p} d={d} upd={upd} commit={commit} path="vignette.feather" label="페더" min={0} max={100} unsigned />
      <div className="mb-1 mt-2 border-t border-line pt-2 text-[11px] font-semibold text-subtle">그레인</div>
      <P p={p} d={d} upd={upd} commit={commit} path="grain.amount" label="양" min={0} max={100} unsigned />
      <P p={p} d={d} upd={upd} commit={commit} path="grain.size" label="크기" min={0} max={100} unsigned />
    </Section>
  )
}
