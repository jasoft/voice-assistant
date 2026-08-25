import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { IconLoadingOutline16, IconRefreshOutline14, useDismissOnOutsidePointer } from '@deepseek-ai/dsh-client-ui-primitives'
import type { PropsLocale, PropsRuntime, TranslateNS } from '@deepseek-ai/dsh-client-ui-slots'
import { NS } from './locales.ts'
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import css from './StockIndicesWidget.module.css'

export type StockIndicesWidgetProps =
  PropsRuntime<'conversation.session.header.utilities'> & PropsLocale<typeof NS>

export interface IndexData {
  sym: string
  code: string
  name: string
  shortName: string
  point: number
  changeAmount: number
  changeRate: number
  volume: string
  turnover: string
}

const INDEX_CONFIGS = [
  { sym: 'sh000001', code: '000001', nameKey: 'index.sh000001', shortKey: 'index.sh000001.short' },
  { sym: 'sz399006', code: '399006', nameKey: 'index.sz399006', shortKey: 'index.sz399006.short' },
  { sym: 'sh000688', code: '000688', nameKey: 'index.sh000688', shortKey: 'index.sh000688.short' },
] as const

function formatVolume(valStr: string): string {
  const vol = parseFloat(valStr)
  if (isNaN(vol) || vol <= 0) return '-'
  if (vol >= 100_000_000) return `${(vol / 100_000_000).toFixed(2)}亿手`
  if (vol >= 10_000) return `${(vol / 10_000).toFixed(2)}万手`
  return `${vol}手`
}

function formatTurnover(valStr: string): string {
  const amount = parseFloat(valStr)
  if (isNaN(amount) || amount <= 0) return '-'
  // Tencent compact turnover is in 万元
  if (amount >= 10_000) return `${(amount / 10_000).toFixed(2)}亿元`
  return `${amount.toFixed(2)}万元`
}

export function parseQuotes(text: string, t: TranslateNS<typeof NS>): IndexData[] {
  const results: IndexData[] = []
  const lines = text.trim().split('\n')
  const map = new Map<string, string[]>()

  for (const line of lines) {
    const match = line.match(/v_s_([a-z0-9]+)="([^"]+)"/)
    if (match && match[1] && match[2]) {
      map.set(match[1], match[2].split('~'))
    }
  }

  for (const cfg of INDEX_CONFIGS) {
    const parts = map.get(cfg.sym)
    if (parts && parts.length >= 6) {
      results.push({
        sym: cfg.sym,
        code: cfg.code,
        name: t(cfg.nameKey),
        shortName: t(cfg.shortKey),
        point: parseFloat(parts[3] ?? '0'),
        changeAmount: parseFloat(parts[4] ?? '0'),
        changeRate: parseFloat(parts[5] ?? '0'),
        volume: formatVolume(parts[6] ?? '0'),
        turnover: formatTurnover(parts[7] ?? '0'),
      })
    }
  }

  return results
}

export function StockIndicesWidget({ t }: StockIndicesWidgetProps) {
  const [indices, setIndices] = useState<IndexData[]>([])
  const [loading, setLoading] = useState(false)
  const [lastUpdate, setLastUpdate] = useState<string>('')
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  const fetchQuotes = useCallback(async () => {
    try {
      setLoading(true)
      const res = await fetch('https://qt.gtimg.cn/q=s_sh000001,s_sz399006,s_sh000688', {
        cache: 'no-store',
      })
      const buffer = await res.arrayBuffer()
      const decoder = new TextDecoder('gbk')
      const text = decoder.decode(buffer)
      const parsed = parseQuotes(text, t)
      if (parsed.length > 0) {
        setIndices(parsed)
        const now = new Date()
        setLastUpdate(
          `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`,
        )
      }
    } catch {
      // Keep existing data on error
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    void fetchQuotes()
    const timer = setInterval(() => {
      void fetchQuotes()
    }, 15_000)
    return () => clearInterval(timer)
  }, [fetchQuotes])

  useDismissOnOutsidePointer(rootRef, open, setOpen)

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      setOpen(false)
      triggerRef.current?.focus()
    }
  }

  const getTrendClass = (rate: number, isBadge = false) => {
    if (rate > 0) return isBadge ? css.upBadge : css.up
    if (rate < 0) return isBadge ? css.downBadge : css.down
    return isBadge ? css.flatBadge : css.flat
  }

  const formatRate = (rate: number) => {
    const sign = rate > 0 ? '+' : ''
    return `${sign}${rate.toFixed(2)}%`
  }

  const formatAmount = (amount: number) => {
    const sign = amount > 0 ? '+' : ''
    return `${sign}${amount.toFixed(2)}`
  }

  return (
    <div ref={rootRef} className={css.root} onKeyDown={onKeyDown}>
      <button
        ref={triggerRef}
        type="button"
        className={css.tickerGroup}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={t('widget.aria')}
        onClick={() => setOpen((prev: boolean) => !prev)}
      >
        {indices.length === 0 ? (
          <div className={css.loading}>
            <IconLoadingOutline16 size={12} className={css.spin} />
            <span>{t('status.loading')}</span>
          </div>
        ) : (
          indices.map((item: IndexData, idx: number) => (
            <div key={item.code} className={css.tickerItem}>
              {idx > 0 && <span className={css.tickerSep}>|</span>}
              <span className={css.name}>{item.shortName}</span>
              <span className={`${css.point} ${getTrendClass(item.changeRate)}`}>
                {item.point.toFixed(2)}
              </span>
              <span className={`${css.rate} ${getTrendClass(item.changeRate, true)}`}>
                {formatRate(item.changeRate)}
              </span>
            </div>
          ))
        )}
      </button>

      {open && (
        <div className={css.popover} role="dialog" aria-label={t('widget.title')}>
          <div className={css.popoverHeader}>
            <span className={css.popoverTitle}>{t('widget.title')}</span>
            <div className={css.popoverMeta}>
              {lastUpdate && (
                <span className={css.updateTime}>
                  {t('status.updated', { time: lastUpdate })}
                </span>
              )}
              <button
                type="button"
                className={css.refreshButton}
                title={t('action.refresh')}
                onClick={() => void fetchQuotes()}
                disabled={loading}
              >
                <IconRefreshOutline14 size={14} className={loading ? css.spin : undefined} />
              </button>
            </div>
          </div>

          <div className={css.cardList}>
            {indices.map((item: IndexData) => (
              <div key={item.code} className={css.card}>
                <div className={css.cardHeader}>
                  <span className={css.cardName}>{item.name}</span>
                  <span className={css.cardCode}>{item.code}</span>
                </div>
                <div className={css.cardMain}>
                  <span className={`${css.cardPoint} ${getTrendClass(item.changeRate)}`}>
                    {item.point.toFixed(2)}
                  </span>
                  <div className={`${css.cardChanges} ${getTrendClass(item.changeRate)}`}>
                    <span>{formatAmount(item.changeAmount)}</span>
                    <span>({formatRate(item.changeRate)})</span>
                  </div>
                </div>
                <div className={css.cardDetails}>
                  <div className={detailItemClass}>
                    <span className={css.detailLabel}>{t('field.turnover')}:</span>
                    <span className={css.detailValue}>{item.turnover}</span>
                  </div>
                  <div className={detailItemClass}>
                    <span className={css.detailLabel}>{t('field.volume')}:</span>
                    <span className={css.detailValue}>{item.volume}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

const detailItemClass = css.detailItem
