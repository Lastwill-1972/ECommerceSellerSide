<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/modules/auth/store'
import { useRoute } from 'vue-router'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const merchantLinks = [
  { name: 'merchant-home', label: '商家概览', icon: 'DataBoard' },
  { name: 'merchant-profile', label: '商家资料', icon: 'User' },
  { name: 'merchant-shops', label: '店铺列表', icon: 'Shop' },
  { name: 'merchant-applications', label: '审批中心', icon: 'Tickets' },
  { name: 'merchant-orders', label: '订单列表', icon: 'List' },
] as const
const pageTitle = computed(() => typeof route.meta.title === 'string' ? route.meta.title : '工作台')
const inMerchant = computed(() => route.matched.some((record) => record.meta.merchantOnly))
watch(() => [auth.token, auth.identityState] as const, ([token, state]) => {
  if (state === 'conflict') void router.replace({ name: 'forbidden' })
  else if (!token && router.currentRoute.value.name !== 'login') {
    void router.replace({ name: 'login', query: { reason: 'expired' } })
  }
})
const roleName = computed(() => ({
  PLATFORM_ADMIN: '平台管理员', MERCHANT_ADMIN: '商家管理员', SHOP_OWNER: '店铺店主',
  SHOP_EMPLOYEE: '店铺员工', NONE: '尚未分配业务身份',
}[auth.user?.role ?? 'NONE']))

function signOut() {
  auth.signOut()
  void router.replace({ name: 'login' })
}
</script>

<template>
  <div class="app-frame">
    <aside class="sidebar">
      <RouterLink class="brand" :to="{ name: 'dashboard' }">
        <span class="brand-mark">商</span>
        <span><strong>拾集商家</strong><small>SELLER CONSOLE</small></span>
      </RouterLink>
      <div class="nav-caption">工作空间</div>
      <RouterLink class="nav-link" :class="{ active: !inMerchant }" :to="{ name: 'dashboard' }">
        <el-icon><DataBoard /></el-icon><span>工作台</span><i></i>
      </RouterLink>
      <template v-if="auth.user?.role === 'MERCHANT_ADMIN'">
        <RouterLink v-for="item in merchantLinks" :key="item.name" class="nav-link merchant-nav" :class="{ active: route.name === item.name || (item.name === 'merchant-orders' && route.name === 'merchant-order-detail') }" :to="{ name: item.name }">
          <el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span><i></i>
        </RouterLink>
      </template>
      <div class="sidebar-note">
        <div class="note-icon"><el-icon><Lock /></el-icon></div>
        <strong>安全访问</strong>
        <p>账号权限由服务端实时校验，页面显示只用于导航。</p>
      </div>
      <div class="sidebar-footer"><span class="status-dot" :class="{ 'status-dot-pending': auth.identityState !== 'ready' }"></span>{{ auth.identityState === 'ready' ? '身份已校验' : '身份待确认' }}</div>
    </aside>

    <section class="main-column">
      <header class="topbar">
        <div class="breadcrumb"><RouterLink :to="{ name: 'dashboard' }">工作空间</RouterLink><el-icon><ArrowRight /></el-icon><strong>{{ pageTitle }}</strong></div>
        <div class="top-actions">
          <span class="top-date">商家服务 · 账户中心</span>
          <el-dropdown trigger="click">
            <button class="profile-trigger" type="button" aria-label="账户菜单">
              <span class="avatar">{{ auth.user?.username.slice(0, 1).toUpperCase() }}</span>
              <span class="profile-name">{{ auth.user?.username }}<small>{{ roleName }}</small></span>
              <el-icon><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="signOut"><el-icon><SwitchButton /></el-icon>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>
      <main class="page-content"><RouterView /></main>
      <footer class="page-footer"><span>拾集商家工作台</span><span>为每一份生意，认真服务</span></footer>
    </section>
  </div>
</template>
