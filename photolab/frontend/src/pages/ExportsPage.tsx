import { useQuery } from '@tanstack/react-query'
import { Download, FolderOpen, Share2 } from 'lucide-react'
import { api, post } from '@/api/client'
import { Button } from '@/components/ui'

interface ExportFile {
  path: string
  name: string
  folder: string
  size: number
  time: string
}

export function ExportsPage() {
  const q = useQuery({ queryKey: ['exports'], queryFn: () => api<ExportFile[]>('/exports?limit=120') })
  const files = q.data ?? []
  const groups = files.reduce<Record<string, ExportFile[]>>((acc, f) => {
    ;(acc[f.folder] ||= []).push(f)
    return acc
  }, {})
  const url = (f: ExportFile, dl = false) => `/api/exports/file?${dl ? 'download=true&' : ''}path=${encodeURIComponent(f.path)}`
  const share = async (f: ExportFile) => {
    try {
      const blob = await (await fetch(url(f))).blob()
      const file = new File([blob], f.name, { type: blob.type })
      if (navigator.canShare?.({ files: [file] })) await navigator.share({ files: [file] })
      else window.open(url(f, true))
    } catch {
      /* user cancelled */
    }
  }
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl p-4 pb-24">
        <div className="mb-4 flex items-center justify-between">
          <h1 className="text-[16px] font-semibold">내보낸 사진</h1>
          <Button variant="ghost" onClick={() => post('/exports/open-folder').catch(() => undefined)}>
            <FolderOpen className="size-4" /> 폴더 열기
          </Button>
        </div>
        {!files.length && <p className="text-muted">아직 내보낸 사진이 없어요. 라이브러리나 현상 화면에서 ‘내보내기’를 눌러 보세요.</p>}
        {Object.entries(groups).map(([folder, fs]) => (
          <section key={folder} className="mb-6">
            <h2 className="mb-2 text-[12px] font-semibold text-muted">{folder}</h2>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">
              {fs.map((f) => (
                <div key={f.path} className="overflow-hidden rounded-lg bg-panel-2">
                  {/\.(jpe?g|png)$/i.test(f.name) ? (
                    <img src={url(f)} loading="lazy" className="aspect-square w-full object-cover" alt="" />
                  ) : (
                    <div className="flex aspect-square items-center justify-center text-[11px] text-subtle">TIFF</div>
                  )}
                  <div className="flex items-center gap-1 p-1.5">
                    <span className="flex-1 truncate text-[11px] text-muted">{f.name}</span>
                    <button onClick={() => share(f)} className="text-subtle hover:text-fg" title="공유">
                      <Share2 className="size-3.5" />
                    </button>
                    <a href={url(f, true)} download className="text-subtle hover:text-fg" title="받기">
                      <Download className="size-3.5" />
                    </a>
                  </div>
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}
