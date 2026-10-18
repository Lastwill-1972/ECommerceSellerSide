import { defineComponent, h } from 'vue'

export default defineComponent({
  props: { disabled: Boolean, loading: Boolean, type: { type: String, default: 'default' } },
  emits: ['click'],
  setup(props, { slots, emit }) {
    return () => h('button', { disabled: props.disabled, onClick: () => emit('click') }, slots.default?.())
  },
})
