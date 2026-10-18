import { createApp } from 'vue'
import { createPinia } from 'pinia'
import {
  ElButton, ElDropdown, ElDropdownItem, ElDropdownMenu, ElForm, ElFormItem,
  ElIcon, ElInput, ElMessage, provideGlobalConfig,
} from 'element-plus'
import {
  ArrowDown, ArrowRight, Box, CircleCheck, CircleCheckFilled, DataBoard, Goods,
  Grid, InfoFilled, Key, Lock, Message, Refresh, Right, Shop, SwitchButton,
  TopRight, User, UserFilled, Tickets, List,
} from '@element-plus/icons-vue'
import 'element-plus/dist/index.css'
import './styles/main.css'
import './styles/merchant.css'
import App from './App.vue'
import { router } from './app/router'
import { ApiError } from './shared/http/errors'
import { setHttpToastHandler } from './shared/http/client'

const app = createApp(App)
app.use(createPinia())
app.use(router)
provideGlobalConfig({ size: 'large' }, app, true)
for (const component of [ElButton, ElDropdown, ElDropdownItem, ElDropdownMenu, ElForm, ElFormItem, ElIcon, ElInput]) app.component(component.name!, component)
for (const [name, component] of Object.entries({
  ArrowDown, ArrowRight, Box, CircleCheck, CircleCheckFilled, DataBoard, Goods,
  Grid, InfoFilled, Key, Lock, Message, Refresh, Right, Shop, SwitchButton,
  TopRight, User, UserFilled, Tickets, List,
})) app.component(name, component)
setHttpToastHandler((error: ApiError) => ElMessage.error(error.message))
app.mount('#app')
window.addEventListener('auth:expired', () => { void router.replace({ name: 'login', query: { reason: 'expired' } }) })
