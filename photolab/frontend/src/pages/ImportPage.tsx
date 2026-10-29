import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUp, CheckCircle2, CreditCard, FolderOpen, FolderPlus, HardDrive, RefreshCw, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api, post, qs } from '@/api/client'
import { useAddRoot, useRemoveRoot, useRoots, waitJob } from '@/api/hooks'
import type { Job } from '@/api/types'
import { Button, cn, Field, inputCls, Modal, Toggle } from '@/components/ui'

export function ImportPage() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 p-4 pb-24">
        <Roots />
        <CardImport />
        <LightroomImport />
      </div>
    </div>
  )
}

function Card({ title, icon, children, desc }: { title: string; icon: React.ReactNode; children: React.ReactNode; desc?: string }) {
  return (
    <section className="rounded-xl border border-line bg-panel p-4">
      <h2 className="flex items-center gap-2 text-[15px] font-semibold">
        {icon}
        {title}
      </h2>
      {desc && <p className="mt-1 text-[12.5px] text-muted">{desc}</p>}
      <div className="mt-3">{children}</div>
    </section>
  )
}

function Roots() {
  const roots = useRoots()
  const add = useAddRoot()
  const remove = useRemoveRoot()
  const qc = useQueryClient()
  const [picker, setPicker] = useState(false)
  return (
    <Card
      title="사진 폴더"
      icon={<FolderOpen className="size-5 text-accent" />}
      desc="폴더를 추가하면 그 안의 사진을 그대로 보여줘요. 파일을 옮기거나 바꾸지 않고, 편집 내용은 PhotoLab에만 저장돼요."
    >
      <div className="space-y-1.5">
        {roots.data?.map((r) => (
          <div key={r.id} className="flex items-center gap-2 rounded-lg bg-panel-2 px-3 py-2">
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">{r.name}</div>
              <div className="truncate text-[11px] text-subtle">
                {r.path} · {r.count.toLocaleString()}장
              </div>
            </div>
            <Button
              variant="ghost"
              title="다시 살펴보기 (새로 추가된 사진 찾기)"
              onClick={async () => {
                await post(`/roots/${r.id}/scan`)
                qc.invalidateQueries({ queryKey: ['jobs'] })
              }}
            >
              <RefreshCw className="size-4" />
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                if (confirm(`'${r.name}'을(를) 목록에서 뺄까요? 파일은 그대로 두고, 이 폴더 사진의 PhotoLab 편집 내용만 지워져요.`)) remove.mutate(r.id)
              }}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        ))}
      </div>
      <Button className="mt-3" variant="primary" onClick={() => setPicker(true)}>
        <FolderPlus className="size-4" /> 폴더 추가
      </Button>
      <FolderPicker
        open={picker}
        onClose={() => setPicker(false)}
        onPick={(path) => {
          add.mutate({ path })
          setPicker(false)
        }}
      />
      {add.error && <p className="mt-2 text-[12px] text-bad">{(add.error as Error).message}</p>}
    </Card>
  )
}

export function FolderPicker({ open, onClose, onPick, title = '폴더 고르기' }: { open: boolean; onClose: () => void; onPick: (p: string) => void; title?: string }) {
  const [path, setPath] = useState<string | null>(null)
  const q = useQuery({
    queryKey: ['browse', path],
    queryFn: () => api<{ path: string | null; parent: string | null; dirs: { name: string; path: string }[] }>(`/fs/browse${qs({ path })}`),
    enabled: open,
  })
  return (
    <Modal open={open} onClose={onClose} title={title}>
      <div className="mb-2 flex items-center gap-2">
        <Button variant="ghost" disabled={!q.data?.path} onClick={() => setPath(q.data?.parent ?? null)}>
          <ArrowUp className="size-4" />
        </Button>
        <input className={inputCls} value={path ?? ''} placeholder="드라이브를 고르세요" onChange={(e) => setPath(e.target.value || null)} />
      </div>
      <div className="h-72 overflow-y-auto rounded-md border border-line">
        {q.data?.dirs.map((d) => (
          <button key={d.path} className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-panel-3" onClick={() => setPath(d.path)}>
            {q.data?.path ? <FolderOpen className="size-4 text-subtle" /> : <HardDrive className="size-4 text-subtle" />}
            {d.name}
          </button>
        ))}
        {q.error && <p className="p-3 text-[12px] text-bad">{(q.error as Error).message}</p>}
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          취소
        </Button>
        <Button variant="primary" disabled={!q.data?.path} onClick={() => q.data?.path && onPick(q.data.path)}>
          이 폴더 선택
        </Button>
      </div>
    </Modal>
  )
}

function CardImport() {
  const roots = useRoots()
  const cards = useQuery({ queryKey: ['cards'], queryFn: () => api<{ path: string; dcim: string; count: number }[]>('/import/cards'), refetchInterval: 5000 })
  const [source, setSource] = useState('')
  const [rootId, setRootId] = useState<number | null>(null)
  const [pattern, setPattern] = useState('%Y/%y%m%d')
  const [backup, setBackup] = useState('')
  const [videos, setVideos] = useState(false)
  const [plan, setPlan] = useState<{ total: number; new: number; bytes: number; folders: { folder: string; count: number }[] } | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [msg, setMsg] = useState('')
  const [picker, setPicker] = useState<'source' | 'backup' | null>(null)
  const rid = rootId ?? roots.data?.[0]?.id ?? null
  const body = { source, root_id: rid, pattern, backup: backup || null, videos }
  return (
    <Card
      title="메모리카드에서 가져오기"
      icon={<CreditCard className="size-5 text-accent" />}
      desc="카드의 사진을 날짜별 폴더로 복사해요 (카드에서 지우지 않아요). 이미 가져온 사진은 건너뛰고, 백업 폴더를 정하면 한 번 더 복사해요."
    >
      {cards.data?.length ? (
        <div className="mb-3 flex flex-wrap gap-2">
          {cards.data.map((c) => (
            <button
              key={c.path}
              onClick={() => setSource(c.dcim)}
              className={cn('rounded-lg border px-3 py-2 text-left', source === c.dcim ? 'border-accent bg-accent-soft' : 'border-line-strong')}
            >
              <div className="font-medium">{c.path} 카드</div>
              <div className="text-[11px] text-muted">{c.count.toLocaleString()}개 파일</div>
            </button>
          ))}
        </div>
      ) : (
        <p className="mb-3 text-[12px] text-subtle">꽂힌 카메라 카드가 없어요. 폴더를 직접 골라도 돼요.</p>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="가져올 곳 (카드 / 폴더)">
          <div className="flex gap-1">
            <input className={inputCls} value={source} onChange={(e) => setSource(e.target.value)} />
            <Button onClick={() => setPicker('source')}>…</Button>
          </div>
        </Field>
        <Field label="넣을 사진 폴더">
          <select className={inputCls} value={rid ?? ''} onChange={(e) => setRootId(Number(e.target.value))}>
            {roots.data?.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="하위 폴더 규칙" hint="%Y 연도 · %y 두 자리 연도 · %m 월 · %d 일  (예: 2024/241112)">
          <input className={inputCls} value={pattern} onChange={(e) => setPattern(e.target.value)} />
        </Field>
        <Field label="백업 폴더 (선택)" hint="다른 드라이브를 권장해요">
          <div className="flex gap-1">
            <input className={inputCls} value={backup} onChange={(e) => setBackup(e.target.value)} />
            <Button onClick={() => setPicker('backup')}>…</Button>
          </div>
        </Field>
      </div>
      <div className="mt-3">
        <Toggle checked={videos} onChange={setVideos} label="동영상도 가져오기" />
      </div>
      {plan && (
        <div className="mt-3 rounded-lg bg-panel-2 p-3 text-[12.5px]">
          새 파일 <b>{plan.new.toLocaleString()}</b>개 ({(plan.bytes / 1e9).toFixed(1)} GB) · 이미 있음 {(plan.total - plan.new).toLocaleString()}개
          <div className="mt-1 text-[11px] text-muted">{plan.folders.map((f) => `${f.folder} (${f.count})`).join(' · ')}</div>
        </div>
      )}
      {job && (
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded bg-panel-3">
            <div className="h-full bg-accent" style={{ width: `${job.total ? (job.done / job.total) * 100 : 3}%` }} />
          </div>
          <p className="mt-1 text-[12px] text-muted">{job.message}</p>
        </div>
      )}
      {msg && <p className="mt-2 flex items-center gap-1 text-[12.5px] text-good"><CheckCircle2 className="size-4" />{msg}</p>}
      <div className="mt-3 flex gap-2">
        <Button disabled={!source || !rid} onClick={async () => setPlan(await post('/import/plan', body))}>
          미리 보기
        </Button>
        <Button
          variant="primary"
          disabled={!source || !rid || !!job}
          onClick={async () => {
            setMsg('')
            try {
              const r = await post<{ job_id: number }>('/import', body)
              const j = await waitJob(r.job_id, setJob)
              setMsg(j.result ?? '완료')
            } catch (e) {
              setMsg((e as Error).message)
            } finally {
              setJob(null)
            }
          }}
        >
          가져오기
        </Button>
      </div>
      <FolderPicker
        open={picker !== null}
        onClose={() => setPicker(null)}
        onPick={(p) => {
          if (picker === 'source') setSource(p)
          else setBackup(p)
          setPicker(null)
        }}
      />
    </Card>
  )
}

function LightroomImport() {
  const roots = useRoots()
  const [catalog, setCatalog] = useState('')
  const [found, setFound] = useState<string[]>([])
  const [preview, setPreview] = useState<Record<string, number> | null>(null)
  const [edits, setEdits] = useState(true)
  const [ratings, setRatings] = useState(true)
  const [overwrite, setOverwrite] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [picker, setPicker] = useState(false)
  return (
    <Card
      title="라이트룸 카탈로그에서 옮겨오기"
      icon={<span className="flex size-5 items-center justify-center rounded bg-[#31a8ff] text-[10px] font-bold text-[#001e36]">Lr</span>}
      desc="라이트룸 Classic의 별점·픽/제외·색 라벨과 보정값(노출, 톤, 색, 커브, 자르기, 렌즈 교정, 마스크 일부)을 가져와요. 사진은 촬영 시각으로 짝지어서 파일 이름이 달라도 찾아요. 카탈로그는 복사본만 읽어요."
    >
      <Field label="카탈로그 파일 (.lrcat)">
        <div className="flex gap-1">
          <input className={inputCls} value={catalog} onChange={(e) => setCatalog(e.target.value)} placeholder="C:\…\카탈로그.lrcat" />
          <Button onClick={() => setPicker(true)}>찾기…</Button>
        </div>
      </Field>
      {found.length > 0 && (
        <div className="mt-2 space-y-1">
          {found.map((f) => (
            <button key={f} onClick={() => (setCatalog(f), setFound([]))} className="block w-full truncate rounded px-2 py-1 text-left text-[12px] hover:bg-panel-3">
              {f}
            </button>
          ))}
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-4">
        <Toggle checked={edits} onChange={setEdits} label="보정값" />
        <Toggle checked={ratings} onChange={setRatings} label="별점·플래그·라벨" />
        <Toggle checked={overwrite} onChange={setOverwrite} label="이미 PhotoLab에서 편집한 사진도 덮어쓰기" />
      </div>
      {preview && (
        <div className="mt-3 rounded-lg bg-panel-2 p-3 text-[12.5px]">
          카탈로그 {preview.catalog_photos.toLocaleString()}장 중 <b>{preview.matched.toLocaleString()}장</b> 짝 찾음 · 보정 {preview.edited}장 · 별점 {preview.rated}장 · 픽 {preview.picked}장 · 제외{' '}
          {preview.rejected}장
        </div>
      )}
      {job && <p className="mt-2 text-[12px] text-muted">{job.message}</p>}
      {msg && <p className="mt-2 flex items-center gap-1 text-[12.5px] text-good"><CheckCircle2 className="size-4" />{msg}</p>}
      {err && <p className="mt-2 text-[12px] text-bad">{err}</p>}
      <div className="mt-3 flex gap-2">
        <Button
          disabled={!catalog}
          onClick={async () => {
            setErr('')
            try {
              setPreview(await post('/lightroom/preview', { catalog }))
            } catch (e) {
              setErr((e as Error).message)
            }
          }}
        >
          확인해 보기
        </Button>
        <Button
          variant="primary"
          disabled={!catalog || !!job || !roots.data?.length}
          onClick={async () => {
            setErr('')
            setMsg('')
            try {
              const r = await post<{ job_id: number }>('/lightroom/import', { catalog, edits, ratings, overwrite })
              const j = await waitJob(r.job_id, setJob)
              setMsg(j.result ?? '완료')
            } catch (e) {
              setErr((e as Error).message)
            } finally {
              setJob(null)
            }
          }}
        >
          가져오기
        </Button>
      </div>
      <FolderPicker
        open={picker}
        title="카탈로그가 있는 폴더 고르기"
        onClose={() => setPicker(false)}
        onPick={async (p) => {
          setPicker(false)
          const r = await api<string[]>(`/lightroom/find${qs({ root: p })}`)
          if (r.length === 1) setCatalog(r[0])
          else setFound(r)
          if (!r.length) setErr('그 폴더 아래에서 .lrcat 파일을 찾지 못했어요.')
        }}
      />
    </Card>
  )
}
