import { CheckCircle2, Download, FolderOpen } from 'lucide-react'
import { useState } from 'react'
import { post } from '@/api/client'
import { waitJob } from '@/api/hooks'
import type { Job } from '@/api/types'
import { Button, cn, Field, inputCls, Modal, Segmented } from './ui'

const PRESETS = [
  { key: 'instagram', label: 'SNS (인스타)', desc: '긴 변 2160px · sRGB · 화면용 샤픈' },
  { key: 'phone', label: '휴대폰 보관', desc: '긴 변 3000px · JPEG 90' },
  { key: 'full', label: '원본 크기 JPEG', desc: '최고 화질 · 보관용' },
  { key: 'print', label: '인화용 TIFF', desc: '16bit · Adobe RGB · 무손실' },
  { key: 'custom', label: '직접 설정', desc: '' },
]

export function ExportDialog({ open, onClose, ids }: { open: boolean; onClose: () => void; ids: number[] }) {
  const [preset, setPreset] = useState('instagram')
  const [format, setFormat] = useState('jpeg')
  const [quality, setQuality] = useState(92)
  const [longEdge, setLongEdge] = useState<string>('')
  const [space, setSpace] = useState('srgb')
  const [sharpen, setSharpen] = useState('screen')
  const [naming, setNaming] = useState('{name}')
  const [job, setJob] = useState<Job | null>(null)
  const [result, setResult] = useState<{ dir: string; files: string[] } | null>(null)
  const [err, setErr] = useState('')

  const start = async () => {
    setErr('')
    setResult(null)
    try {
      const body =
        preset === 'custom'
          ? { ids, format, quality, long_edge: longEdge ? Number(longEdge) : null, color_space: space, sharpen, naming }
          : { ids, preset, naming }
      const r = await post<{ job_id: number }>('/export', body)
      const done = await waitJob(r.job_id, setJob)
      setResult(JSON.parse(done.result || '{}'))
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setJob(null)
    }
  }

  const close = () => {
    if (job) return
    setResult(null)
    onClose()
  }

  return (
    <Modal open={open} onClose={close} title={`${ids.length}장 내보내기`}>
      {result ? (
        <div className="space-y-3">
          <p className="flex items-center gap-2 text-good">
            <CheckCircle2 className="size-5" /> {result.files.length}장을 내보냈어요.
          </p>
          <p className="break-all text-[12px] text-muted">{result.dir}</p>
          <div className="max-h-56 space-y-1 overflow-y-auto">
            {result.files.map((f) => (
              <a
                key={f}
                className="flex items-center gap-2 rounded px-2 py-1 text-[12.5px] hover:bg-panel-3"
                href={`/api/exports/file?download=true&path=${encodeURIComponent(f)}`}
                download
              >
                <Download className="size-3.5 text-subtle" />
                <span className="truncate">{f.split(/[\\/]/).pop()}</span>
              </a>
            ))}
          </div>
          <div className="flex justify-end gap-2">
            <Button onClick={() => post(`/exports/open-folder?path=${encodeURIComponent(result.dir)}`).catch(() => undefined)}>
              <FolderOpen className="size-4" /> 폴더 열기
            </Button>
            <Button variant="primary" onClick={close}>
              확인
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2">
            {PRESETS.map((p) => (
              <button
                key={p.key}
                onClick={() => setPreset(p.key)}
                className={cn(
                  'rounded-lg border p-2.5 text-left transition-colors',
                  preset === p.key ? 'border-accent bg-accent-soft' : 'border-line-strong hover:bg-panel-3',
                )}
              >
                <div className="text-[13px] font-medium">{p.label}</div>
                {p.desc && <div className="mt-0.5 text-[11px] text-muted">{p.desc}</div>}
              </button>
            ))}
          </div>
          {preset === 'custom' && (
            <div className="grid grid-cols-2 gap-3">
              <Field label="형식">
                <Segmented
                  value={format}
                  onChange={setFormat}
                  options={[
                    { value: 'jpeg', label: 'JPEG' },
                    { value: 'tiff', label: 'TIFF 16bit' },
                    { value: 'png', label: 'PNG' },
                  ]}
                />
              </Field>
              {format === 'jpeg' && (
                <Field label={`화질 ${quality}`}>
                  <input type="range" className="pl-slider" min={60} max={100} value={quality} onChange={(e) => setQuality(Number(e.target.value))} />
                </Field>
              )}
              <Field label="긴 변 크기 (px)" hint="비우면 원본 크기">
                <input className={inputCls} inputMode="numeric" value={longEdge} onChange={(e) => setLongEdge(e.target.value.replace(/\D/g, ''))} placeholder="예: 2048" />
              </Field>
              <Field label="색공간">
                <select className={inputCls} value={space} onChange={(e) => setSpace(e.target.value)}>
                  <option value="srgb">sRGB (웹·폰)</option>
                  <option value="adobergb">Adobe RGB (인화)</option>
                  <option value="p3">Display P3</option>
                </select>
              </Field>
              <Field label="출력 샤픈">
                <select className={inputCls} value={sharpen} onChange={(e) => setSharpen(e.target.value)}>
                  <option value="none">없음</option>
                  <option value="screen">화면용</option>
                  <option value="glossy">유광 인화</option>
                  <option value="matte">무광 인화</option>
                </select>
              </Field>
            </div>
          )}
          <Field label="파일 이름" hint="{name} 원래 이름 · {date} 촬영일 · {time} 시각 · {seq} 순번 · {folder} 폴더명">
            <input className={inputCls} value={naming} onChange={(e) => setNaming(e.target.value)} />
          </Field>
          {job && (
            <div>
              <div className="h-1.5 overflow-hidden rounded bg-panel-3">
                <div className="h-full bg-accent transition-all" style={{ width: `${job.total ? (job.done / job.total) * 100 : 5}%` }} />
              </div>
              <p className="mt-1 text-[12px] text-muted">{job.message}</p>
            </div>
          )}
          {err && <p className="text-[12px] text-bad">{err}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={close} disabled={!!job}>
              취소
            </Button>
            <Button variant="primary" onClick={start} disabled={!!job || !ids.length}>
              <Download className="size-4" /> 내보내기
            </Button>
          </div>
        </div>
      )}
    </Modal>
  )
}
