import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/modules/auth/store'
import AppShell from '@/shared/layout/AppShell.vue'
import LoginPage from '@/modules/auth/pages/LoginPage.vue'
import RegisterPage from '@/modules/auth/pages/RegisterPage.vue'
import ForbiddenPage from '@/modules/system/pages/ForbiddenPage.vue'
import NotFoundPage from '@/modules/system/pages/NotFoundPage.vue'
import DashboardPage from '@/modules/dashboard/pages/DashboardPage.vue'
import { ApiError } from '@/shared/http/errors'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginPage, meta: { guestOnly: true, title: '登录' } },
    { path: '/register', name: 'register', component: RegisterPage, meta: { guestOnly: true, title: '注册账号' } },
    {
      path: '/', component: AppShell, meta: { requiresAuth: true },
      children: [
        { path: '', name: 'dashboard', component: DashboardPage, meta: { requiresAuth: true, title: '工作台' } },
        {
          path: 'merchant', component: () => import('@/modules/merchant/pages/MerchantFramePage.vue'),
          meta: { requiresAuth: true, merchantOnly: true },
          children: [
            { path: '', name: 'merchant-home', component: () => import('@/modules/merchant/pages/MerchantHomePage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '商家概览' } },
            { path: 'profile', name: 'merchant-profile', component: () => import('@/modules/merchant/pages/MerchantProfilePage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '商家资料' } },
            { path: 'shops', name: 'merchant-shops', component: () => import('@/modules/merchant/pages/MerchantShopsPage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '店铺列表' } },
            { path: 'applications', name: 'merchant-applications', component: () => import('@/modules/merchant/pages/MerchantApplicationsPage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '审批中心' } },
            { path: 'orders', name: 'merchant-orders', component: () => import('@/modules/merchant/pages/MerchantOrdersPage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '订单列表' } },
            { path: 'orders/:id', name: 'merchant-order-detail', component: () => import('@/modules/merchant/pages/MerchantOrderDetailPage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '订单详情' } },
          ],
        },
      ],
    },
    { path: '/403', name: 'forbidden', component: ForbiddenPage, meta: { title: '无权访问' } },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFoundPage, meta: { title: '页面不存在' } },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth) {
    if (!auth.token) return { name: 'login', query: { redirect: safeInternalRedirect(to.fullPath) } }
    try {
      await auth.loadCurrentUser(true)
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) return { name: 'forbidden' }
      return { name: 'login', query: { reason: 'unavailable', redirect: safeInternalRedirect(to.fullPath) } }
    }
    if (auth.identityState === 'conflict') return { name: 'forbidden' }
    if (!auth.user) return { name: 'login', query: { redirect: safeInternalRedirect(to.fullPath) } }
    if (to.matched.some((record) => record.meta.merchantOnly) && (auth.user.role !== 'MERCHANT_ADMIN' || auth.user.merchant_id === null)) return { name: 'forbidden' }
  }
  if (to.meta.guestOnly && auth.token) {
    try {
      const user = await auth.loadCurrentUser(true)
      if (user) return safeInternalRedirect(to.query.redirect) ?? { name: 'dashboard' }
    } catch {
      if (auth.identityState === 'conflict') return { name: 'forbidden' }
    }
  }
})

router.afterEach((to) => {
  document.title = typeof to.meta.title === 'string' ? `${to.meta.title} · 商家工作台` : '商家工作台'
})

/** Accept only same-origin application paths; never pass arbitrary URLs to the router. */
export function safeInternalRedirect(value: unknown): string | null {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return null
  try {
    const url = new URL(value, window.location.origin)
    if (url.origin !== window.location.origin || url.username || url.password) return null
    if (['/login', '/register', '/403'].includes(url.pathname)) return null
    return `${url.pathname}${url.search}${url.hash}`
  } catch {
    return null
  }
}
