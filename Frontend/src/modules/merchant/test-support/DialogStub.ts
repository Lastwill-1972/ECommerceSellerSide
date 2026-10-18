import { defineComponent, h, watch } from 'vue'

export default defineComponent({
  props: { modelValue: Boolean },
  emits: ['closed'],
  setup(props, { slots, emit }) {
    watch(() => props.modelValue, (open, wasOpen) => {
      if (wasOpen && !open) setTimeout(() => emit('closed'), 25)
    })
    return () => props.modelValue ? h('div', { class: 'dialog' }, [slots.default?.(), slots.footer?.()]) : null
  },
})
