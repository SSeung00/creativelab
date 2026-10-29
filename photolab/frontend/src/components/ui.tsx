import { clsx, type ClassValue } from 'clsx'
import { ChevronDown, ChevronRight, Loader2, X } from 'lucide-react'
import { useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { twMerge } from 'tailwind-merge'

export const cn = (...c: ClassValue[]) => twMerge(clsx(c))

type Variant = 'default' | 'primary' | 'ghost' | 'danger'

export function Button({ variant = 'default', className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      {...rest}
      className={cn(
        'inline-flex h-8 items-center justify-center gap-1.5 rounded-md px-3 text-[13px] font-medium transition-colors disabled:pointer-events-none disabled:opacity-40',
        variant === 'default' && 'border border-line-strong bg-panel-2 text-fg hover:bg-panel-3',
        variant === 'primary' && 'bg-accent text-accent-fg hover:brightness-110',
        variant === 'ghost' && 'text-muted hover:bg-panel-3 hover:text-fg',
        variant === 'danger' && 'border border-bad/40 text-bad hover:bg-bad/10',
        className,
      )}
    />
  )
}

export function IconButton({ active, className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <button
      {...rest}
      className={cn(
        'inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted transition-colors hover:bg-panel-3 hover:text-fg disabled:opacity-30',
        active && 'bg-accent-soft text-accent hover:bg-accent-soft hover:text-accent',
        className,
      )}
    />
  )
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn('size-4 animate-spin text-muted', className)} />
}

function fmt(v: number, step: number) {
  const d = step < 0.1 ? 2 : step < 1 ? 1 : 0
  const s = v.toFixed(d)
  return v > 0 && !s.startsWith('+') ? `+${s}` : s
}

/** Lightroom-style slider: label, editable value, double-click to reset. */
export function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  def = 0,
  onChange,
  onCommit,
  track,
  unsigned,
  suffix,
  format,
}: {
  label: string
  value: number
  min: number
  max: number
  step?: number
  def?: number
  onChange: (v: number) => void
  onCommit?: (v: number) => void
  track?: string
  unsigned?: boolean
  suffix?: string
  format?: (v: number) => string
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const reset = () => {
    onChange(def)
    onCommit?.(def)
  }
  return (
    <div className="group grid grid-cols-[minmax(68px,auto)_1fr_48px] items-center gap-2 py-[1px]">
      <button type="button" className="truncate text-left text-[12px] text-muted hover:text-fg" onDoubleClick={reset} title="더블클릭: 초기화">
        {label}
      </button>
      <input
        type="range"
        className="pl-slider"
        style={track ? ({ '--track': track } as React.CSSProperties) : undefined}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        onPointerUp={(e) => onCommit?.(Number((e.target as HTMLInputElement).value))}
        onKeyUp={(e) => onCommit?.(Number((e.target as HTMLInputElement).value))}
        onDoubleClick={reset}
      />
      {editing ? (
        <input
          autoFocus
          className="h-5 w-full rounded bg-panel-3 px-1 text-right text-[12px] tabular-nums outline-none"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => setEditing(false)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              const n = Number(text)
              if (!Number.isNaN(n)) {
                const v = Math.min(max, Math.max(min, n))
                onChange(v)
                onCommit?.(v)
              }
              setEditing(false)
            } else if (e.key === 'Escape') setEditing(false)
          }}
        />
      ) : (
        <button
          type="button"
          className={cn('text-right text-[12px] tabular-nums', value !== def ? 'text-fg' : 'text-subtle')}
          onClick={() => {
            setText(String(value))
            setEditing(true)
          }}
        >
          {format ? format(value) : unsigned ? value.toFixed(step < 1 ? 1 : 0) : fmt(value, step)}
          {suffix}
        </button>
      )}
    </div>
  )
}

export function Section({
  title,
  children,
  defaultOpen = true,
  right,
  id,
}: {
  title: string
  children: ReactNode
  defaultOpen?: boolean
  right?: ReactNode
  id?: string
}) {
  const key = id ? `pl-sec-${id}` : null
  const [open, setOpen] = useState(() => {
    if (!key) return defaultOpen
    try {
      const v = localStorage.getItem(key)
      return v === null ? defaultOpen : v === '1'
    } catch {
      return defaultOpen
    }
  })
  const toggle = () => {
    setOpen((o) => {
      try {
        if (key) localStorage.setItem(key, o ? '0' : '1')
      } catch {
        /* ignore */
      }
      return !o
    })
  }
  return (
    <section className="border-b border-line">
      <div className="flex h-9 items-center gap-1 px-3">
        <button type="button" className="flex flex-1 items-center gap-1.5 text-left text-[12px] font-semibold tracking-wide text-fg/90" onClick={toggle}>
          {open ? <ChevronDown className="size-3.5 text-subtle" /> : <ChevronRight className="size-3.5 text-subtle" />}
          {title}
        </button>
        {right}
      </div>
      {open && <div className="px-3 pb-3">{children}</div>}
    </section>
  )
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-[12px] text-muted">
      <span
        className={cn('relative h-4 w-7 rounded-full transition-colors', checked ? 'bg-accent' : 'bg-panel-3')}
        onClick={(e) => {
          e.preventDefault()
          onChange(!checked)
        }}
      >
        <span className={cn('absolute top-0.5 size-3 rounded-full bg-white transition-all', checked ? 'left-3.5' : 'left-0.5')} />
      </span>
      {label}
    </label>
  )
}

export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  className,
}: {
  value: T
  options: { value: T; label: ReactNode; title?: string }[]
  onChange: (v: T) => void
  className?: string
}) {
  return (
    <div className={cn('inline-flex rounded-md bg-panel-3 p-0.5', className)}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          title={o.title}
          className={cn(
            'h-6 rounded px-2 text-[12px] text-muted transition-colors',
            value === o.value && 'bg-panel text-fg shadow-sm',
          )}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Modal({
  open,
  onClose,
  title,
  children,
  wide,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  wide?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 sm:items-center" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={ref}
        className={cn(
          'max-h-[92dvh] w-full overflow-auto rounded-t-xl border border-line bg-panel shadow-2xl sm:rounded-xl',
          wide ? 'sm:max-w-3xl' : 'sm:max-w-lg',
        )}
      >
        <div className="sticky top-0 z-10 flex h-12 items-center justify-between border-b border-line bg-panel px-4">
          <h2 className="text-[14px] font-semibold">{title}</h2>
          <IconButton onClick={onClose} aria-label="닫기">
            <X className="size-4" />
          </IconButton>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  )
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[12px] text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-subtle">{hint}</span>}
    </label>
  )
}

export const inputCls =
  'h-8 w-full rounded-md border border-line-strong bg-panel-2 px-2.5 text-[13px] text-fg outline-none focus:border-accent'

export function Stars({ value, onChange, size = 12 }: { value: number; onChange?: (v: number) => void; size?: number }) {
  return (
    <span className="inline-flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((i) => (
        <button
          key={i}
          type="button"
          disabled={!onChange}
          onClick={(e) => {
            e.stopPropagation()
            onChange?.(value === i ? 0 : i)
          }}
          className={cn('leading-none', i <= value ? 'text-accent' : 'text-subtle/50', onChange && 'hover:text-fg')}
          style={{ fontSize: size }}
        >
          ★
        </button>
      ))}
    </span>
  )
}

export const LABEL_COLORS: Record<string, string> = {
  red: 'var(--label-red)',
  yellow: 'var(--label-yellow)',
  green: 'var(--label-green)',
  blue: 'var(--label-blue)',
  purple: 'var(--label-purple)',
}
