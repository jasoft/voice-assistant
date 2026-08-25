/**
 * Stock indices plugin, browser half: contributes a session-header utility widget
 * displaying real-time SSE Composite, ChiNext, and STAR 50 indices.
 */
import type { ClientContext } from '@deepseek-ai/dsh-client-runtime/client'
import { StockIndicesWidget } from './StockIndicesWidget.tsx'
import type {} from '@deepseek-ai/dsh-client-locale/client'
import { en, NS, zh, type StockIndicesKey } from './locales.ts'

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap {
    /** Stock indices ticker copy. */
    'stock_indices': StockIndicesKey
  }
}

export type { StockIndicesWidgetProps } from './StockIndicesWidget.tsx'

/** Required services for locale registration and header-slot contribution. */
export const inject = ['slots', 'locale']

/**
 * Client plugin body: register the dictionaries and the header utilities widget.
 * @param ctx - client root context.
 */
export function apply(ctx: ClientContext): void {
  ctx.effect(() => ctx.locale.register(NS, { zh, en }), 'ui-stock-indices: dictionaries')
  ctx.slots.inject(
    'conversation.session.header.utilities',
    () => ctx.slots.register({
      name: 'conversation.session.header.utilities',
      id: 'stock-indices',
      order: 10,
      locale: NS,
    }, StockIndicesWidget),
  )
}
