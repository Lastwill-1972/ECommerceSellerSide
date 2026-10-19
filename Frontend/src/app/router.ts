import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/modules/auth/store'
import AppShell from '@/shared/layout/AppShell.vue'
import LoginPage from '@/modules/auth/pages/LoginPage.vue'
import RegisterPage from '@/modules/auth/pages/RegisterPage.vue'
import ForbiddenPage from '@/modules/system/pages/ForbiddenPage.vue'
import NotFoundPage from '@/modules/system/pages/NotFoundPage.vue'
import DashboardPage from '@/modules/dashboard/pages/DashboardPage.vue'
import { ApiError } from '@/shared/http/errors'

let expiredSessionPending = false
if (typeof window !== 'undefined') {
  window.addEventListener('auth:expired', () => { expiredSessionPending = true })
}

const EmployeeHomePage = () => import('@/modules/orders/EmployeeHomePage.vue')
const OrderListPage = () => import('@/modules/orders/OrderListPage.vue')
const OrderCreatePage = () => import('@/modules/orders/OrderCreatePage.vue')
const OrderDetailPage = () => import('@/modules/orders/OrderDetailPage.vue')
const RoleFrame = () => import('@/modules/workspace/RoleFrame.vue')
const ShopReadPage = () => import('@/modules/workspace/pages/ShopReadPage.vue')

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginPage, meta: { guestOnly: true, title: '登录' } },
    { path: '/register', name: 'register', component: RegisterPage, meta: { guestOnly: true, title: '注册账号' } },
    {
      path: '/', component: AppShell, meta: { requiresAuth: true },
      children: [
        { path: '', name: 'dashboard', component: DashboardPage, meta: { requiresAuth: true, title: '工作台' } },
        { path: 'applications', component: RoleFrame, meta: { requiresAuth: true, allowedRoles: ['NONE'] }, children: [
          { path: '', name: 'identity-applications', component: () => import('@/modules/workspace/pages/ApplicationsPage.vue'), meta: { requiresAuth: true, allowedRoles: ['NONE'], title: '身份申请中心' } },
        ] },
        { path: 'shop', component: RoleFrame, meta: { requiresAuth: true, allowedRoles: ['SHOP_OWNER'] }, children: [
          { path: 'audit', name: 'shop-audit', component: () => import('@/modules/audit/AuditPage.vue'), meta: { title: '审计查询' } },
          { path: '', name: 'shop-home', component: ShopReadPage, meta: { title: '店铺概览' } },
          { path: 'employees', name: 'shop-employees', component: () => import('@/modules/workspace/pages/ShopPeoplePage.vue'), meta: { title: '本店员工' } },
          { path: 'applications', name: 'shop-applications', component: () => import('@/modules/workspace/pages/ShopPeoplePage.vue'), meta: { title: '员工入职审批' } },
          { path: 'roles', name: 'shop-roles', component: () => import('@/modules/workspace/pages/ShopPeoplePage.vue'), meta: { title: '店铺岗位管理' } },
          { path: 'warehouse', name: 'shop-warehouse', component: ShopReadPage, meta: { title: '仓库授权申请' } },
          { path: 'catalog', name: 'shop-catalog', component: ShopReadPage, meta: { title: '商家商品只读' } },
          { path: 'inventory', name: 'shop-inventory', component: ShopReadPage, meta: { title: '共享库存只读' } },
          { path: 'orders', name: 'shop-orders', component: OrderListPage, meta: { title: '订单列表' } },
          { path: 'orders/create', name: 'shop-order-create', component: OrderCreatePage, meta: { title: '创建订单' } },
          { path: 'orders/:id', name: 'shop-order-detail', component: OrderDetailPage, meta: { title: '订单详情' } },
          { path: 'owner-transfer', name: 'shop-owner-transfer', component: () => import('@/modules/workspace/pages/OwnerTransferPage.vue'), meta: { title: '提议负责人交接' } },
        ] },
        { path: 'employee', component: RoleFrame, meta: { requiresAuth: true, allowedRoles: ['SHOP_EMPLOYEE'] }, children: [
          { path: '', name: 'employee-home', component: EmployeeHomePage, meta: { title: '员工概览' } },
          { path: 'catalog', name: 'employee-catalog', component: ShopReadPage, meta: { title: '商品只读' } },
          { path: 'inventory', name: 'employee-inventory', component: ShopReadPage, meta: { title: '库存只读' } },
          { path: 'orders', name: 'employee-orders', component: OrderListPage, meta: { title: '订单列表' } },
          { path: 'orders/create', name: 'employee-order-create', component: OrderCreatePage, meta: { title: '创建订单' } },
          { path: 'orders/:id', name: 'employee-order-detail', component: OrderDetailPage, meta: { title: '订单详情' } },
        ] },
        {
          path: 'merchant', component: () => import('@/modules/merchant/pages/MerchantFramePage.vue'),
          meta: { requiresAuth: true, merchantOnly: true },
          children: [
            { path: 'audit', name: 'merchant-audit', component: () => import('@/modules/audit/AuditPage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '审计查询' } },
            { path: '', name: 'merchant-home', component: () => import('@/modules/merchant/pages/MerchantHomePage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '商家概览' } },
            { path: 'profile', name: 'merchant-profile', component: () => import('@/modules/merchant/pages/MerchantProfilePage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '商家资料' } },
            { path: 'shops', name: 'merchant-shops', component: () => import('@/modules/merchant/pages/MerchantShopsPage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '店铺列表' } },
            { path: 'applications', name: 'merchant-applications', component: () => import('@/modules/merchant/pages/MerchantApplicationsPage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '审批中心' } },
            { path: 'orders', name: 'merchant-orders', component: () => import('@/modules/merchant/pages/MerchantOrdersPage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '订单列表' } },
            { path: 'orders/:id', name: 'merchant-order-detail', component: () => import('@/modules/merchant/pages/MerchantOrderDetailPage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '订单详情' } },
            { path: 'catalog', name: 'merchant-catalog', component: () => import('@/modules/merchant/pages/MerchantCatalogPage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '商品管理' } },
            { path: 'inventory', name: 'merchant-inventory', component: () => import('@/modules/merchant/pages/MerchantInventoryPage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '库存管理' } },
            { path: 'owner-transfer', name: 'merchant-owner-transfer', component: () => import('@/modules/workspace/pages/OwnerTransferPage.vue'), meta: { requiresAuth: true, merchantOnly: true, title: '负责人交接管理' } },
          ],
        },
      ],
    },
    { path: '/403', name: 'forbidden', component: ForbiddenPage, meta: { title: '无权访问' } },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFoundPage, meta: { title: '页面不存在' } },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

/** Roles that operate inside a concrete merchant/shop scope and therefore need real non-empty ids. */
const SHOP_SCOPED_ROLES = ['SHOP_OWNER', 'SHOP_EMPLOYEE']

function hasShopScope(user: { role?: string | null; merchant_id?: number | string | null; shop_id?: number | string | null } | null): boolean {
  if (!user) return false
  return user.role === 'SHOP_OWNER' || user.role === 'SHOP_EMPLOYEE'
    ? (user.merchant_id !== null && user.merchant_id !== undefined && user.merchant_id !== '' &&
        user.shop_id !== null && user.shop_id !== undefined && user.shop_id !== '')
    : false
}

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth) {
    if (!auth.token) {
      const reason = expiredSessionPending ? 'expired' : undefined
      expiredSessionPending = false
      return { name: 'login', query: { ...(reason ? { reason } : {}), redirect: safeInternalRedirect(to.fullPath) } }
    }
    try {
      await auth.loadCurrentUser(true)
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) return { name: 'forbidden' }
      const reason = error instanceof ApiError && error.status === 401 ? 'expired' : 'unavailable'
      expiredSessionPending = false
      return { name: 'login', query: { reason, redirect: safeInternalRedirect(to.fullPath) } }
    }
    expiredSessionPending = false
    if (auth.identityState === 'conflict') return { name: 'forbidden' }
    if (!auth.user) {
      const reason = !auth.token || expiredSessionPending ? 'expired' : 'unavailable'
      expiredSessionPending = false
      return { name: 'login', query: { reason, redirect: safeInternalRedirect(to.fullPath) } }
    }
    if (to.matched.some((record) => record.meta.merchantOnly) && (auth.user.role !== 'MERCHANT_ADMIN' || auth.user.merchant_id === null)) return { name: 'forbidden' }
    const allowed = to.meta.allowedRoles as string[] | undefined
    if (allowed) {
      if (!allowed.includes(auth.user.role)) return { name: 'forbidden' }
      if (SHOP_SCOPED_ROLES.includes(auth.user.role) && !hasShopScope(auth.user)) return { name: 'forbidden' }
    }
    // Platform administrators never enter merchant/shop/employee business areas.
    if (auth.user.role === 'PLATFORM_ADMIN' && to.matched.some((record) => Array.isArray(record.meta.allowedRoles) || record.meta.merchantOnly)) return { name: 'forbidden' }
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
