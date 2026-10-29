import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cpu, Smartphone } from 'lucide-react'
import { useState } from 'react'
import { api, put } from '@/api/client'
import { useAuthStatus } from '@/api/hooks'
import { Button, Field, inputCls } from '@/components/ui'

interface RemoteStatus {
  installed: boolean
  running: boolean
  dns_name: string | null
  url: string | null
  serving: boolean
  serve_cmd: string
  pin_set: boolean
  qr_svg: string | null
}

export function SettingsPage() {
  const auth = useAuthStatus()
  const sys = useQuery({ queryKey: ['system'], queryFn: () => api<{ gpu: { name: string; free_gb: number; total_gb: number } | null; torch: string }>('/system') })
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl space-y-6 p-4 pb-24">
        <section className="rounded-xl border border-line bg-panel p-4">
          <h2 className="flex items-center gap-2 text-[15px] font-semibold">
            <Cpu className="size-5 text-accent" /> 처리 장치
          </h2>
          <p className="mt-2 text-[13px] text-muted">
            {sys.data?.gpu
              ? `${sys.data.gpu.name} · 여유 VRAM ${sys.data.gpu.free_gb} / ${sys.data.gpu.total_gb} GB`
              : 'GPU를 찾지 못해 CPU로 처리해요 (느릴 수 있어요).'}
          </p>
        </section>
        {auth.data?.local ? <PhoneAccess /> : <p className="text-muted">휴대폰 접속 설정은 PC에서 바꿀 수 있어요.</p>}
      </div>
    </div>
  )
}

function PhoneAccess() {
  const qc = useQueryClient()
  const st = useQuery({ queryKey: ['remote'], queryFn: () => api<RemoteStatus>('/remote/status') })
  const [pin, setPin] = useState('')
  const setPinM = useMutation({
    mutationFn: (p: string | null) => put('/auth/pin', { pin: p }),
    onSuccess: () => {
      setPin('')
      qc.invalidateQueries({ queryKey: ['remote'] })
    },
  })
  const s = st.data
  return (
    <section className="rounded-xl border border-line bg-panel p-4">
      <h2 className="flex items-center gap-2 text-[15px] font-semibold">
        <Smartphone className="size-5 text-accent" /> 휴대폰에서 보기
      </h2>
      <p className="mt-1 text-[12.5px] text-muted">RunLab·MoneyLab처럼 Tailscale로 연결해요. 사진은 PC에 그대로 있고, 휴대폰은 화면만 받아요.</p>
      <ol className="mt-3 space-y-3 text-[13px]">
        <li>
          <b>1. PIN 정하기</b> {s?.pin_set && <span className="text-good">· 설정됨</span>}
          <div className="mt-1 flex gap-2">
            <Field label="">
              <input className={inputCls} inputMode="numeric" type="password" placeholder="숫자 4자리 이상" value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ''))} />
            </Field>
            <Button disabled={pin.length < 4} onClick={() => setPinM.mutate(pin)}>
              저장
            </Button>
            {s?.pin_set && (
              <Button variant="ghost" onClick={() => setPinM.mutate(null)}>
                끄기
              </Button>
            )}
          </div>
        </li>
        <li>
          <b>2. Tailscale</b>{' '}
          {s ? (s.running ? <span className="text-good">· 연결됨 ({s.dns_name})</span> : <span className="text-bad">· {s.installed ? '로그인 필요' : '설치 필요'}</span>) : '…'}
        </li>
        <li>
          <b>3. 이 PC에서 한 번 실행</b> {s?.serving && <span className="text-good">· 켜져 있음</span>}
          <pre className="mt-1 overflow-x-auto rounded bg-panel-2 p-2 text-[12px]">{s?.serve_cmd}</pre>
        </li>
        {s?.url && (
          <li>
            <b>4. 휴대폰으로 열기</b>
            <div className="mt-2 flex items-center gap-4">
              {s.qr_svg && <div className="size-32 overflow-hidden rounded bg-white" dangerouslySetInnerHTML={{ __html: s.qr_svg }} />}
              <a className="break-all text-accent" href={s.url}>
                {s.url}
              </a>
            </div>
          </li>
        )}
      </ol>
    </section>
  )
}
