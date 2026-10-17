<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { TopRight } from '@element-plus/icons-vue'
import { ApiError } from '@/shared/http/errors'
import { useAuthStore } from '@/modules/auth/store'

const auth = useAuthStore()
const refreshing = ref(false)
const roleLabels = {
  PLATFORM_ADMIN: '平台管理员', MERCHANT_ADMIN: '商家管理员',
  SHOP_OWNER: '店铺店主', SHOP_EMPLOYEE: '店铺员工', NONE: '尚未分配业务身份',
} as const
const roleLabel = computed(() => auth.user ? roleLabels[auth.user.role] : '身份未确认')
const accountMark = computed(() => auth.user?.username.slice(0, 1).toUpperCase() ?? '—')
const entryInfo = computed(() => {
  switch (auth.user?.role) {
    case 'PLATFORM_ADMIN': return { title: '平台管理', detail: '本阶段仅提供账号身份查看，平台管理界面尚未开放' }
    case 'MERCHANT_ADMIN': return { title: '商家经营', detail: '管理商家资料、店铺申请、共享仓库授权和订单信息' }
    case 'SHOP_OWNER': return { title: '店铺管理', detail: '店铺经营与员工管理入口将在后续阶段开放' }
    case 'SHOP_EMPLOYEE': return { title: '店铺工作', detail: '店铺业务操作入口将在后续阶段开放' }
    default: return { title: '业务身份待分配', detail: '账号已创建，待建立商家或店铺关系后才能开通业务入口' }
  }
})

async function refreshIdentity() {
  refreshing.value = true
  try {
    const user = await auth.refreshIdentity()
    if (user) ElMessage.success('身份信息已更新')
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) ElMessage.error('账号身份关系存在冲突，请联系管理员处理。')
    else ElMessage.error(error instanceof ApiError ? error.message : '暂时无法刷新身份信息。')
  } finally {
    refreshing.value = false
  }
}
</script>

<template>
  <div v-if="!auth.user" class="dashboard-unavailable">
    <span class="unavailable-icon"><el-icon><Refresh /></el-icon></span>
    <span class="section-kicker">IDENTITY STATUS</span>
    <h1>{{ auth.identityState === 'conflict' ? '身份关系需要处理' : '暂时无法确认身份' }}</h1>
    <p>{{ auth.identityState === 'conflict' ? '服务端检测到账号身份关系冲突，请联系管理员检查账号配置。' : '服务端暂时没有返回可用的身份信息。工作台内容已隐藏，请稍后重试。' }}</p>
    <el-button class="refresh-button" :loading="refreshing" @click="refreshIdentity"><el-icon><Refresh /></el-icon>重新验证</el-button>
  </div>
  <div v-else class="dashboard-page">
    <div class="welcome-row">
      <div><div class="page-eyebrow">OVERVIEW <span></span> 今日工作空间</div><h1>你好，{{ auth.user?.username }} <span class="wave">✦</span></h1><p>欢迎来到你的商家工作台。当前账号身份与权限以服务端实时校验结果为准。</p></div>
      <el-button class="refresh-button" :loading="refreshing" @click="refreshIdentity"><el-icon><Refresh /></el-icon>刷新身份</el-button>
    </div>

    <section class="hero-card">
      <div class="hero-copy"><span class="hero-label"><i></i> 工作空间已就绪</span><h2>把每一步经营<br />都做得更 <em>从容</em></h2><p>身份信息由服务端确认。随着业务功能逐步开放，更多经营入口将在这里呈现。</p><div class="hero-tag"><el-icon><CircleCheckFilled /></el-icon> 身份信息已获取</div></div>
      <div class="hero-art" aria-hidden="true"><div class="hero-sun"></div><div class="hero-ring ring-a"></div><div class="hero-ring ring-b"></div><div class="hero-tile tile-one"><span>商</span><i>ACCOUNT</i></div><div class="hero-tile tile-two"><span class="tile-check">✓</span><b>身份已确认</b><small>SECURE SESSION</small></div><div class="hero-spark spark-a">✦</div><div class="hero-spark spark-b">✳</div><div class="hero-spark spark-c">·</div></div>
      <div class="hero-bottom"><span>简洁的工作空间，让经营清楚可见</span><span>01 <i></i> 01</span></div>
    </section>

    <div class="section-heading"><div><span class="section-kicker">YOUR ACCOUNT</span><h2>账户概览</h2></div><span class="verified-label"><el-icon><CircleCheckFilled /></el-icon> 已验证</span></div>
    <section class="account-grid">
      <article class="account-card"><div class="card-icon icon-lilac"><el-icon><UserFilled /></el-icon></div><div><span class="metric-label">登录账号</span><strong class="metric-value">{{ auth.user?.username }}</strong><span class="metric-detail">账号 ID · {{ auth.user?.id }}</span></div><div class="card-arrow"><el-icon><TopRight /></el-icon></div></article>
      <article class="account-card"><div class="card-icon icon-mint"><el-icon><Message /></el-icon></div><div><span class="metric-label">联系邮箱</span><strong class="metric-value email-value">{{ auth.user?.email }}</strong><span class="metric-detail">用于账户联系与识别</span></div><div class="card-arrow"><el-icon><TopRight /></el-icon></div></article>
      <article class="account-card"><div class="card-icon icon-peach"><el-icon><Key /></el-icon></div><div><span class="metric-label">当前身份</span><strong class="metric-value">{{ roleLabel }}</strong><span class="metric-detail">由后端权限服务提供</span></div><div class="card-arrow"><el-icon><TopRight /></el-icon></div></article>
    </section>

    <div class="lower-grid">
      <section class="entry-panel"><div class="panel-heading"><div><span class="section-kicker">QUICK ACCESS</span><h2>工作入口</h2></div><span class="coming-label">{{ auth.user?.role === 'MERCHANT_ADMIN' ? '已开放' : '本阶段未开放' }}</span></div><RouterLink v-if="auth.user?.role === 'MERCHANT_ADMIN'" class="entry-row merchant-entry" :to="{ name: 'merchant-home' }"><span class="entry-icon"><el-icon><Grid /></el-icon></span><div class="entry-copy"><strong>{{ entryInfo.title }}</strong><span>{{ entryInfo.detail }}</span></div><span class="entry-status"><i></i> 进入工作区</span><el-icon class="entry-arrow"><TopRight /></el-icon></RouterLink><div v-else class="entry-row entry-disabled"><span class="entry-icon"><el-icon><Grid /></el-icon></span><div class="entry-copy"><strong>{{ entryInfo.title }}</strong><span>{{ entryInfo.detail }}</span></div><span class="entry-status"><i></i> 暂未开放</span></div><div class="entry-foot"><el-icon><InfoFilled /></el-icon> {{ auth.user?.role === 'MERCHANT_ADMIN' ? '商家页面由服务端身份关系授权。' : '当前版本仅提供账号登录、身份查看与退出。' }}</div></section>
      <section class="identity-panel"><div class="panel-heading"><div><span class="section-kicker">IDENTITY</span><h2>身份信息</h2></div><span class="identity-badge"><el-icon><Lock /></el-icon> 服务端确认</span></div><div class="identity-user"><span class="identity-avatar">{{ accountMark }}</span><div><strong>{{ auth.user?.username }}</strong><span>{{ roleLabel }}</span></div></div><div class="identity-facts"><div><span>商家编号</span><strong>{{ auth.user?.merchant_id ?? '—' }}</strong></div><div><span>店铺编号</span><strong>{{ auth.user?.shop_id ?? '—' }}</strong></div></div><p class="identity-footnote"><el-icon><InfoFilled /></el-icon> 页面菜单仅帮助导航，实际访问权限始终由后端验证。</p></section>
    </div>

    <div v-if="auth.user?.role === 'PLATFORM_ADMIN'" class="platform-note"><el-icon><InfoFilled /></el-icon><span>平台管理员身份已确认。本阶段工作台仅展示账户状态，平台管理界面尚未开放。</span></div>
  </div>
</template>
