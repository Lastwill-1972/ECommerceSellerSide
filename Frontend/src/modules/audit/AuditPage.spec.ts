import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '@/modules/auth/store'
import AuditPage from './AuditPage.vue'
import type { AuditEvent } from './api'
import type { Page } from '@/shared/types/api'
import { ApiError } from '@/shared/http/errors'

const m = vi.hoisted(() => ({ auditEvents: vi.fn() }))
vi.mock('./api', async (original) => ({ ...await original<typeof import('./api')>(), auditEvents: m.auditEvents }))
// Isolated stand-in for the paging sub-component so page changes can be driven deterministically.
vi.mock('@/modules/workspace/components/PageButtons.vue', () => ({
  default: {
    name: 'PageButtons',
    props: { page: { type: Object, default: null }, busy: { type: Boolean, default: false } },
    emits: ['change'],
    template: '<div data-testid="page-buttons"><span data-testid="page-total">{{ page ? page.total : 0 }}</span></div>',
  },
}))
vi.mock('element-plus', async () => (await import('@/modules/merchant/test-support/elementPlus')).elementPlusMock)

const SHOP_ID = '2098765432109876543'
const USER_ID = '1098765432109876544'

let wrapper: VueWrapper | undefined

function seedAuth(role: 'MERCHANT_ADMIN' | 'SHOP_OWNER' | 'SHOP_EMPLOYEE' | 'NONE' | 'PLATFORM_ADMIN') {
  const auth = useAuthStore()
  auth.token = 'tok'
  auth.identityState = 'ready'
  auth.user = {
    id: USER_ID,
    username: 'u',
    email: 'u@example.com',
    role,
    merchant_id: '10',
    shop_id: SHOP_ID,
  } as never
  return auth
}

function event(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: '9007199254740993',
    actor_user_id: USER_ID,
    event_type: 'ORDER_DETAIL_READ',
    target_type: 'ORDER',
    target_id: '9007199254740997',
    outcome: 'SUCCESS',
    event_data: { note: 'ok' },
    created_at: '2026-10-17T10:00:00',
    ...overrides,
  }
}

function pageOf(items: AuditEvent[], extra: Partial<Page<AuditEvent>> = {}): Page<AuditEvent> {
  return { items, limit: 20, offset: 0, total: items.length, ...extra }
}

function mountPage() {
  wrapper = mount(AuditPage)
  return wrapper
}

function button(label: string) {
  const found = wrapper!.findAll('button').find((b) => b.text() === label)
  expect(found, `button ${label}`).toBeTruthy()
  return found!
}

/** Native contract: inputs live inside their visible <label> captions. */
function input(caption: string) {
  const label = wrapper!.findAll('label').find((l) => l.text().startsWith(caption))
  expect(label, `label ${caption}`).toBeTruthy()
  const field = label!.find('input')
  expect(field.exists()).toBe(true)
  return field
}

function alertText() {
  const alert = wrapper!.find('[role="alert"]')
  expect(alert.exists()).toBe(true)
  return alert.text()
}

async function submit() {
  await wrapper!.find('form').trigger('submit')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  m.auditEvents.mockResolvedValue(pageOf([event()]))
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
})

describe('AuditPage — identity gating and manual query', () => {
  it('MERCHANT_ADMIN: no request on mount, 查询 sends limit 20 and renders exact BIGINT ids', async () => {
    seedAuth('MERCHANT_ADMIN')
    mountPage()
    await flushPromises()

    expect(m.auditEvents).not.toHaveBeenCalled()
    expect(wrapper!.text()).toContain('点击查询')

    await submit()

    expect(m.auditEvents).toHaveBeenCalledTimes(1)
    expect(m.auditEvents.mock.calls[0][0]).toEqual({
      limit: 20,
      offset: 0,
      event_type: '',
      target_type: '',
      start_at: '',
      end_at: '',
    })
    const text = wrapper!.text()
    expect(text).toContain('9007199254740993')
    expect(text).toContain(USER_ID)
    expect(text).toContain('9007199254740997')
    expect(text).toContain('2026-10-17')
    expect(wrapper!.find('table').exists()).toBe(true)
  })

  it('SHOP_OWNER: same manual query path issues exactly one request after clicking 查询', async () => {
    seedAuth('SHOP_OWNER')
    mountPage()
    await flushPromises()
    expect(m.auditEvents).not.toHaveBeenCalled()

    await submit()
    await flushPromises()

    expect(m.auditEvents).toHaveBeenCalledTimes(1)
    expect(m.auditEvents.mock.calls[0][0]).toMatchObject({ limit: 20, offset: 0 })
    expect(wrapper!.text()).toContain('ORDER_DETAIL_READ')
  })

  it('NONE / SHOP_EMPLOYEE: warns and never queries, even after 查询 and 刷新 clicks', async () => {
    for (const role of ['SHOP_EMPLOYEE', 'NONE', 'PLATFORM_ADMIN'] as const) {
      m.auditEvents.mockClear()
      seedAuth(role)
      mountPage()
      await flushPromises()

      expect(alertText()).toContain('无权查询审计事件')

      await submit()
      await button('刷新').trigger('click')
      await flushPromises()

      expect(m.auditEvents).not.toHaveBeenCalled()
      wrapper!.unmount()
      wrapper = undefined
    }
  })

  it('signed-out: shows the not-logged-in warning and sends nothing', async () => {
    const auth = useAuthStore()
    auth.token = ''
    auth.identityState = 'unknown'
    auth.user = null
    mountPage()
    await flushPromises()

    await submit()
    expect(m.auditEvents).not.toHaveBeenCalled()
    expect(alertText()).toContain('当前未登录')
  })
})

describe('AuditPage — filter validation', () => {
  it('sends trimmed filter text and naive datetime values exactly as typed', async () => {
    seedAuth('MERCHANT_ADMIN')
    mountPage()
    await input('事件类型').setValue('  ORDER_DETAIL_READ  ')
    await input('目标类型').setValue('ORDER')
    await input('开始时间').setValue('2026-10-17T00:00:00')
    await input('结束时间').setValue('2026-10-18T23:59:59')
    await submit()

    expect(m.auditEvents).toHaveBeenCalledTimes(1)
    expect(m.auditEvents.mock.calls[0][0]).toEqual({
      limit: 20,
      offset: 0,
      event_type: 'ORDER_DETAIL_READ',
      target_type: 'ORDER',
      start_at: '2026-10-17T00:00',
      end_at: '2026-10-18T23:59:59',
    })
  })

  it('rejects start later than end locally without calling the API', async () => {
    seedAuth('MERCHANT_ADMIN')
    mountPage()
    await input('开始时间').setValue('2026-10-18T00:00:00')
    await input('结束时间').setValue('2026-10-17T00:00:00')
    await submit()

    expect(m.auditEvents).not.toHaveBeenCalled()
    expect(alertText()).toContain('开始时间不得晚于结束时间')
  })

  it('rejects filter text longer than 100 characters', async () => {
    seedAuth('MERCHANT_ADMIN')
    mountPage()
    await input('目标类型').setValue('x'.repeat(101))
    await submit()

    expect(m.auditEvents).not.toHaveBeenCalled()
    expect(alertText()).toContain('1至100个字符')
  })

  it('keeps datetime inputs timezone-free and blocks a timezone-carrying value', async () => {
    seedAuth('MERCHANT_ADMIN')
    mountPage()

    const start = input('开始时间')
    expect(start.attributes('type')).toBe('datetime-local')
    expect(start.attributes('step')).toBe('1')

    // Native datetime-local rejects timezone-carrying text instead of silently converting it.
    await start.setValue('2026-10-17T00:00:00Z')
    expect(start.element.value).toBe('')

    // A blank filter plus whitespace-only text is accepted as "unset" and never sent.
    await input('事件类型').setValue('   ')
    await submit()
    expect(m.auditEvents).toHaveBeenCalledTimes(1)
    expect(m.auditEvents.mock.calls[0][0]).toMatchObject({ event_type: '', start_at: '' })
  })
})

describe('AuditPage — paging', () => {
  it('requests 20 rows per page, honours paging offsets, and resets to 0 on 查询', async () => {
    seedAuth('MERCHANT_ADMIN')
    m.auditEvents.mockResolvedValueOnce(pageOf([event()], { total: 60 }))
    mountPage()
    await submit()

    expect(m.auditEvents.mock.calls[0][0]).toMatchObject({ limit: 20, offset: 0 })
    const pager = wrapper!.find('[data-testid="page-buttons"]')
    expect(pager.exists()).toBe(true)

    m.auditEvents.mockResolvedValueOnce(pageOf([event({ id: '2' })], { offset: 20, total: 60 }))
    wrapper!.findComponent({ name: 'PageButtons' }).vm.$emit('change', 20)
    await flushPromises()

    expect(m.auditEvents).toHaveBeenCalledTimes(2)
    expect(m.auditEvents.mock.calls[1][0]).toMatchObject({ limit: 20, offset: 20 })

    m.auditEvents.mockResolvedValueOnce(pageOf([event()], { total: 60 }))
    await submit()
    expect(m.auditEvents.mock.calls[2][0]).toMatchObject({ limit: 20, offset: 0 })
  })
})

describe('AuditPage — empty and error states', () => {
  it('renders the empty explanation (not an error) for a zero-row page and sends no alert', async () => {
    seedAuth('MERCHANT_ADMIN')
    m.auditEvents.mockResolvedValueOnce(pageOf([], { total: 0 }))
    mountPage()
    await submit()

    expect(wrapper!.find('table').exists()).toBe(false)
    expect(wrapper!.text()).toContain('没有可见的审计事件')
    expect(wrapper!.find('[role="alert"]').exists()).toBe(false)
  })

  it('403 shows the scoped authorization message and 重试 re-queries', async () => {
    seedAuth('SHOP_OWNER')
    m.auditEvents.mockRejectedValueOnce(new ApiError('未授权', 403))
    mountPage()
    await submit()

    expect(alertText()).toContain('未获授权')
    expect(wrapper!.find('table').exists()).toBe(false)

    await button('重试').trigger('click')
    await flushPromises()

    expect(m.auditEvents).toHaveBeenCalledTimes(2)
    expect(wrapper!.find('table').exists()).toBe(true)
    expect(wrapper!.find('[role="alert"]').exists()).toBe(false)
  })

  it('generic failure message is surfaced and retry succeeds', async () => {
    seedAuth('MERCHANT_ADMIN')
    m.auditEvents.mockRejectedValueOnce(new Error('网络中断'))
    mountPage()
    await submit()

    expect(alertText()).toContain('网络中断')

    await button('重试').trigger('click')
    await flushPromises()
    expect(m.auditEvents).toHaveBeenCalledTimes(2)
    expect(wrapper!.find('[role="alert"]').exists()).toBe(false)
  })
})

describe('AuditPage — event_data is inert text', () => {
  it('renders HTML payloads as escaped JSON text with no img/script/button execution', async () => {
    const html = '<img src="x" onerror="window.__auditPwned=1"><script>window.__auditPwned=1</script><button>evil</button>'
    seedAuth('MERCHANT_ADMIN')
    m.auditEvents.mockResolvedValueOnce(pageOf([event({ event_data: { html, nested: { tag: '<b>x</b>' } } })]))
    mountPage()
    await submit()

    const cell = wrapper!.find('pre.audit-data')
    expect(cell.exists()).toBe(true)
    expect(cell.attributes('aria-label')).toBe('事件数据')

    const text = cell.text()
    expect(text).toContain('<script>')
    expect(text).toContain('<img')
    expect(text).toContain('<b>x</b>')

    // Nothing from the payload became live DOM.
    expect(cell.find('img').exists()).toBe(false)
    expect(cell.find('script').exists()).toBe(false)
    expect(cell.find('button').exists()).toBe(false)
    expect(wrapper!.find('table img').exists()).toBe(false)
    expect(wrapper!.find('table script').exists()).toBe(false)
    expect(wrapper!.findAll('button').map((b) => b.text())).not.toContain('evil')

    // The only buttons are the page's own controls.
    const labels = wrapper!.findAll('button').map((b) => b.text())
    expect(labels).toEqual(expect.arrayContaining(['刷新', '查询', '清空筛选']))
    expect((window as unknown as Record<string, unknown>).__auditPwned).toBeUndefined()
  })

  it('shows unstringifiable event data as an inert placeholder, not raw output', async () => {
    const circular: Record<string, unknown> = {}
    circular.self = circular
    seedAuth('MERCHANT_ADMIN')
    m.auditEvents.mockResolvedValueOnce(pageOf([event({ event_data: circular })]))
    mountPage()
    await submit()

    expect(wrapper!.find('pre.audit-data').text()).toBe('（事件数据无法显示）')
  })
})

describe('AuditPage — stale responses after scope change', () => {
  it('drops an in-flight response when the identity scope epoch changes', async () => {
    const auth = seedAuth('MERCHANT_ADMIN')
    let resolve: (value: Page<AuditEvent>) => void = () => {}
    m.auditEvents.mockReturnValueOnce(new Promise<Page<AuditEvent>>((r) => { resolve = r }))
    mountPage()
    await submit()

    expect(m.auditEvents).toHaveBeenCalledTimes(1)
    await input('事件类型').setValue('ORDER_DETAIL_READ')

    // Scope change: watcher clears filters/results synchronously.
    auth.scopeEpoch += 1
    await flushPromises()
    expect(input('事件类型').element.value).toBe('')

    resolve(pageOf([event({ event_type: 'STALE_SECRET_EVENT', id: '424242' })]))
    await flushPromises()

    const text = wrapper!.text()
    expect(text).not.toContain('STALE_SECRET_EVENT')
    expect(text).not.toContain('424242')
    expect(wrapper!.find('table').exists()).toBe(false)
    expect(m.auditEvents).toHaveBeenCalledTimes(1)
  })

  it('drops a stale rejection so no error from the old scope is shown', async () => {
    const auth = seedAuth('SHOP_OWNER')
    let reject: (reason: unknown) => void = () => {}
    m.auditEvents.mockReturnValueOnce(new Promise<Page<AuditEvent>>((_, r) => { reject = r }))
    mountPage()
    await submit()

    auth.user = { ...auth.user!, shop_id: '3098765432109876543' } as never
    await flushPromises()

    reject(new ApiError('旧范围未授权', 403))
    await flushPromises()

    expect(wrapper!.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper!.text()).not.toContain('旧范围未授权')
    expect(wrapper!.find('table').exists()).toBe(false)
  })

  it('ignores a late response once the role drops out of the allowed set', async () => {
    const auth = seedAuth('MERCHANT_ADMIN')
    let resolve: (value: Page<AuditEvent>) => void = () => {}
    m.auditEvents.mockReturnValueOnce(new Promise<Page<AuditEvent>>((r) => { resolve = r }))
    mountPage()
    await submit()

    auth.user = { ...auth.user!, role: 'SHOP_EMPLOYEE' } as never
    await flushPromises()
    expect(alertText()).toContain('无权查询审计事件')

    resolve(pageOf([event({ event_type: 'STALE_SECRET_EVENT' })]))
    await flushPromises()

    expect(wrapper!.text()).not.toContain('STALE_SECRET_EVENT')
    expect(wrapper!.find('table').exists()).toBe(false)
  })

  it('清空筛选 clears inputs and results without issuing a request', async () => {
    seedAuth('MERCHANT_ADMIN')
    mountPage()
    await input('事件类型').setValue('ORDER_DETAIL_READ')
    await submit()
    expect(wrapper!.find('table').exists()).toBe(true)

    await button('清空筛选').trigger('click')
    await flushPromises()

    expect(input('事件类型').element.value).toBe('')
    expect(wrapper!.find('table').exists()).toBe(false)
    expect(m.auditEvents).toHaveBeenCalledTimes(1)
  })
})
