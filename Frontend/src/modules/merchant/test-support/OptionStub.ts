import { defineComponent, h } from 'vue'

export default defineComponent({
  props: {
    label: { type: String, default: '' },
    value: { type: [String, Number], default: undefined },
  },
  setup(props, { slots }) {
    return () => h('option', { value: props.value }, slots.default?.() ?? props.label)
  },
})
