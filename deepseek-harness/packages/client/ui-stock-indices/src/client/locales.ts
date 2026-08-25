/** `stock_indices` namespace dictionaries. */

/** Dictionary namespace owned by this plugin. */
export const NS = 'stock_indices'

/** Simplified Chinese dictionary (the key-set source of truth). */
export const zh = {
  'widget.title': 'A股主要指数行情',
  'widget.aria': '指数行情',
  'index.sh000001': '上证指数',
  'index.sh000001.short': '上证',
  'index.sz399006': '创业板指',
  'index.sz399006.short': '创指',
  'index.sh000688': '科创50',
  'index.sh000688.short': '科创50',
  'status.loading': '获取中...',
  'status.error': '获取失败',
  'status.updated': '更新于 {time}',
  'action.refresh': '刷新行情',
  'field.points': '最新点位',
  'field.changeAmount': '涨跌额',
  'field.changeRate': '涨跌幅',
  'field.turnover': '成交额',
  'field.volume': '成交量',
} as const

/** English dictionary, key-identical to the Chinese source of truth. */
export const en: Record<StockIndicesKey, string> = {
  'widget.title': 'China A-Share Major Indices',
  'widget.aria': 'Stock Indices',
  'index.sh000001': 'SSE Composite',
  'index.sh000001.short': 'SSE',
  'index.sz399006': 'ChiNext Index',
  'index.sz399006.short': 'ChiNext',
  'index.sh000688': 'STAR 50 Index',
  'index.sh000688.short': 'STAR 50',
  'status.loading': 'Loading...',
  'status.error': 'Failed to fetch',
  'status.updated': 'Updated at {time}',
  'action.refresh': 'Refresh',
  'field.points': 'Current',
  'field.changeAmount': 'Change',
  'field.changeRate': 'Change %',
  'field.turnover': 'Turnover',
  'field.volume': 'Volume',
}

/** Key domain of the `stock_indices` namespace (zh is the source of truth). */
export type StockIndicesKey = keyof typeof zh
