/* eslint vue/one-component-per-file: "off", vue/require-default-prop: "off" */
import { computed, defineComponent, h, inject, provide } from 'vue'
import { vi } from 'vitest'

export const elementMessages = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}
export const messageBoxConfirm = vi.fn(() => Promise.resolve())
export const messageBoxClose = vi.fn(() => Promise.resolve())
/** Message box actions as exercised in tests; Element Plus defines confirm/cancel/close. */
export const messageBox = { confirm: messageBoxConfirm, close: messageBoxClose }

const passthrough = (tag: string) => defineComponent({
  name: `Test${tag}`,
  inheritAttrs: false,
  setup(_props, { attrs, slots }) { return () => h(tag, attrs, slots.default?.()) },
})

export const ElButton = defineComponent({
  name: 'TestElButton',
  props: { disabled: Boolean, loading: Boolean, type: String, link: Boolean },
  emits: ['click'],
  setup(props, { slots, emit }) {
    return () => h('button', { disabled: props.disabled || props.loading, onClick: (event: Event) => emit('click', event) }, slots.default?.())
  },
})

export const ElInput = defineComponent({
  name: 'TestElInput',
  props: { modelValue: { type: String, default: '' }, type: String, disabled: Boolean },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () => h(props.type === 'textarea' ? 'textarea' : 'input', {
      value: props.modelValue,
      disabled: props.disabled,
      onInput: (event: Event) => emit('update:modelValue', (event.target as HTMLInputElement).value),
    })
  },
})

export const ElSelect = defineComponent({
  name: 'TestElSelect',
  props: { modelValue: { type: [String, Number], default: '' }, disabled: Boolean },
  emits: ['update:modelValue', 'change'],
  setup(props, { slots, emit }) {
    return () => h('select', {
      value: props.modelValue,
      disabled: props.disabled,
      onChange: (event: Event) => {
        const value = (event.target as HTMLSelectElement).value
        emit('update:modelValue', value); emit('change', value)
      },
    }, slots.default?.())
  },
})

export const ElOption = defineComponent({
  name: 'TestElOption',
  props: { label: String, value: { type: [String, Number], default: '' } },
  setup(props, { slots }) { return () => h('option', { value: props.value }, slots.default?.() ?? props.label) },
})

export const ElDialog = defineComponent({
  name: 'TestElDialog',
  props: { modelValue: Boolean, title: String },
  emits: ['update:modelValue', 'closed'],
  setup(props, { slots }) { return () => props.modelValue ? h('section', { class: 'test-dialog', 'aria-label': props.title }, [slots.default?.(), slots.footer?.()]) : null },
})

const tableRowsKey = Symbol('table-rows')
export const ElTable = defineComponent({
  name: 'TestElTable',
  props: { data: { type: Array, default: () => [] } },
  setup(props, { slots }) {
    provide(tableRowsKey, computed(() => props.data))
    return () => h('div', { class: 'test-table' }, slots.default?.())
  },
})
export const ElTableColumn = defineComponent({
  name: 'TestElTableColumn',
  props: { prop: String, label: String },
  setup(props, { slots }) {
    const rows = inject<{ value: unknown[] }>(tableRowsKey, { value: [] })
    return () => h('div', { class: 'test-table-column', 'data-label': props.label }, rows.value.map((row) =>
      slots.default ? slots.default({ row }) : h('span', String((row as Record<string, unknown>)[props.prop ?? ''] ?? '')),
    ))
  },
})

export const ElPagination = defineComponent({
  name: 'TestElPagination',
  props: { currentPage: Number, pageSize: Number, total: Number },
  emits: ['current-change'],
  setup(props, { emit }) {
    return () => h('div', { class: 'test-pagination' }, [
      h('button', { onClick: () => emit('current-change', Math.max(1, (props.currentPage ?? 1) - 1)) }, '上一页'),
      h('button', { onClick: () => emit('current-change', (props.currentPage ?? 1) + 1) }, '下一页'),
    ])
  },
})

export const ElAlert = defineComponent({
  name: 'TestElAlert',
  props: { title: String },
  setup(props, { slots }) { return () => h('aside', [h('strong', props.title ?? ''), slots.default?.()]) },
})

export const ElEmpty = defineComponent({ name: 'TestElEmpty', props: { description: String }, setup: (props) => () => h('p', props.description) })
export const ElSkeleton = defineComponent({ name: 'TestElSkeleton', setup: () => () => h('div', 'loading') })
export const ElCard = defineComponent({
  name: 'TestElCard',
  inheritAttrs: false,
  setup(_props, { attrs, slots }) { return () => h('article', attrs, [slots.header?.(), slots.default?.()]) },
})
export const ElForm = defineComponent({ name: 'TestElForm', setup: (_props, { slots, attrs }) => () => h('form', attrs, slots.default?.()) })
export const ElFormItem = defineComponent({ name: 'TestElFormItem', props: { label: String }, setup: (props, { slots }) => () => h('label', [h('span', props.label ?? ''), slots.default?.()]) })
export const ElTag = passthrough('span')

export const elementPlusMock = {
  ElMessage: elementMessages,
  ElMessageBox: messageBox,
  ElAlert, ElButton, ElCard, ElDialog, ElEmpty, ElForm, ElFormItem, ElInput, ElOption,
  ElPagination, ElSelect, ElSkeleton, ElTable, ElTableColumn, ElTag,
}
