import { defineComponent, h } from 'vue'

export default defineComponent({
  props: { title: { type: String, default: '' } },
  setup(props) {
    return () => h('p', props.title)
  },
})
