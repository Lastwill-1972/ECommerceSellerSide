import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus, { ElMessage } from 'element-plus'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
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
app.use(ElementPlus, { size: 'large' })
for (const [name, component] of Object.entries(ElementPlusIconsVue)) app.component(name, component)
setHttpToastHandler((error: ApiError) => ElMessage.error(error.message))
app.mount('#app')
window.addEventListener('auth:expired', () => { void router.replace({ name: 'login', query: { reason: 'expired' } }) })
