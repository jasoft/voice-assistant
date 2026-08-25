/**
 * ui-stock-indices plugin halves: browser entry, dictionary, utility slot registration,
 * node entry, and invariant companion.
 */
import { Context } from '@deepseek-ai/cordis'
import { describe, expect, it } from 'vitest'
import InvariantRegistry from '@deepseek-ai/dsh-invariants'
import { SlotRegistry } from '@deepseek-ai/dsh-client-runtime/client'
import { stubSettingsScope } from '@deepseek-ai/dsh-client-test-runtime'
import { apply as applyLocale, inject as localeInject } from '@deepseek-ai/dsh-client-locale/client'
import { apply, inject } from '../src/client/index.ts'
import { parseQuotes } from '../src/client/StockIndicesWidget.tsx'
import { apply as applyNode } from '../src/index.ts'
import * as StockIndicesInvariant from '../src/invariant.ts'
import { en, NS, zh } from '../src/client/locales.ts'

/** Slot ledger reader: entry ids currently registered in the header utilities list. */
function utilityEntryIds(ctx: Context): (string | undefined)[] {
  return ctx.slots
    .entries('conversation.session.header.utilities')
    .map(entry => entry.options.id)
}

/** Boot the browser half over a real slot tree that declares the header utilities list. */
async function bench(): Promise<{ ctx: Context; fiber: ReturnType<Context['plugin']> }> {
  const ctx = new Context()
  await ctx.plugin(SlotRegistry).await()
  ctx.slots.register({
    name: 'root',
    children: {
      'conversation.session.header.utilities': { kind: 'list', scope: 'session' },
    },
  } as never, () => null)
  ctx.provide('connection', { api: { settings: {} }, isLoopback: false } as never)
  ctx.provide('remote', { $on: () => () => {} } as never)
  ctx.provide('settingsScope', { bind: () => stubSettingsScope().scope } as never)
  await ctx.plugin({ inject: localeInject, apply: applyLocale }).await()
  const fiber = ctx.plugin({ inject: [...inject], apply })
  await fiber.await()
  return { ctx, fiber }
}

describe('ui-stock-indices browser half', () => {
  it('declares the services it binds', () => {
    expect(inject).toEqual(['slots', 'locale'])
  })

  it('registers the header utilities widget, and fiber teardown removes it (HMR safety)', async () => {
    const { ctx, fiber } = await bench()
    expect(utilityEntryIds(ctx)).toContain('stock-indices')
    await fiber.dispose()
    expect(utilityEntryIds(ctx)).not.toContain('stock-indices')
  })

  it('registers both dictionaries under its own namespace and releases them with the fiber', async () => {
    const { ctx, fiber } = await bench()
    const translate = ctx.locale.bind(NS)
    expect(translate('widget.title')).toBe(zh['widget.title'])
    ctx.locale.setLocale('en')
    expect(translate('widget.title')).toBe(en['widget.title'])

    await fiber.dispose()
    expect(translate('widget.title')).not.toBe(en['widget.title'])
  })

  it('keeps the English dictionary key-identical to the Chinese source of truth', () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(zh).sort())
  })

  it('parses Tencent quotes response text correctly', () => {
    const raw = `
v_s_sh000001="1~上证指数~000001~3905.20~1.48~0.04~446895868~88342348~~692962.17~ZS~";
v_s_sz399006="51~创业板指~399006~3545.58~-49.99~-1.43~168896802~49125597~~187188.30~ZS~";
v_s_sh000688="1~科创50~000688~1653.56~0.59~0.04~6975365~7757315~~56591.95~ZS~";
    `
    const translate = (key: string) => (zh as Record<string, string>)[key] ?? key
    const parsed = parseQuotes(raw, translate as never)
    expect(parsed).toHaveLength(3)

    expect(parsed[0]).toEqual({
      sym: 'sh000001',
      code: '000001',
      name: '上证指数',
      shortName: '上证',
      point: 3905.2,
      changeAmount: 1.48,
      changeRate: 0.04,
      volume: '4.47亿手',
      turnover: '8834.23亿元',
    })

    expect(parsed[1]?.changeRate).toBe(-1.43)
    expect(parsed[2]?.shortName).toBe('科创50')
  })
})

describe('ui-stock-indices node half', () => {
  it('contributes no host behavior', () => {
    expect(applyNode).not.toThrow()
  })
})

describe('ui-stock-indices invariant companion', () => {
  it('reserves package ownership under its declared companion name', async () => {
    const ctx = new Context()
    await ctx.plugin(InvariantRegistry, { enabled: true })
    const fiber = ctx.plugin(StockIndicesInvariant)
    await fiber.await()
    expect(StockIndicesInvariant.name).toBe('client-ui-stock-indices-invariant')
    expect(StockIndicesInvariant.inject).toEqual(['invariants'])
    await fiber.dispose()
  })
})
