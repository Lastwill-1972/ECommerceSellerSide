import { defineComponent, h } from 'vue'

export default defineComponent({
  props: { modelValue: { type: [String, Number], default: undefined } },
  emits: ['update:modelValue', 'change'],
  setup(props, { slots, emit }) {
    return () => h('select', {
      value: props.modelValue,
      onChange: (event: Event) => emit('change', (event.target as HTMLSelectElement).value),
    }, slots.default?.())
  },
})
