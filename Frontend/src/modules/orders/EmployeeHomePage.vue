<script setup lang="ts">
import { computed, onScopeDispose, ref } from 'vue'
import { useIdentityScope } from '@/modules/workspace/useIdentityScope'

// Identity-only view: uses the authenticated `me` payload, no extra business API calls.
// No shop object is fabricated here: employee home reads `me` only and never calls GET /shops.
const scope = useIdentityScope(['SHOP_EMPLOYEE'], () => { busy.value = false; error.value = '' })
const user = computed(() => scope.auth.user)
const missingBinding = computed(() => !scope.auth.user?.shop_id || !scope.auth.user?.merchant_id)
const busy = ref(false), error = ref('')
const retryHint = '身份刷新未完成，身份信息可能已不可用；请稍后点击“重新验证身份”重试。'
// refreshIdentity always bumps the scope epoch, so the pre-refresh capture can never stay current.
// Instead track a dedicated refresh sequence plus a liveness flag that the scope reset re-arms.
let refreshSequence = 0, alive = true
onScopeDispose(() => { alive = false })
async function refreshIdentity() {
  if (busy.value) return
  const ticket = ++refreshSequence, requestToken = scope.auth.token, requestUserId = scope.auth.user?.id
  alive = true; busy.value = true; error.value = ''
  try {
    const refreshed = await scope.auth.refreshIdentity()
    if (ticket !== refreshSequence || !alive) return
    if (!refreshed) throw new Error('服务端未返回当前身份。')
    // Verify the result against the token/id captured before the refresh.
    if (requestToken && scope.auth.token !== requestToken) return
    if (refreshed.id !== requestUserId || scope.auth.user?.id !== refreshed.id) throw new Error('刷新后的身份与请求前不一致，请重新验证身份。')
    if (refreshed.role !== 'SHOP_EMPLOYEE') return // Role changes are handled by the existing RoleFrame.
    error.value = ''
  } catch (cause) {
    if (ticket !== refreshSequence || !alive || scope.auth.token !== requestToken) return
    error.value = `${scope.explain(cause)}（${retryHint}）`
  } finally {
    if (ticket === refreshSequence && alive) busy.value = false
  }
}
</script>
<template>
  <div class="merchant-page workspace-page">
    <div class="welcome-row">
      <div>
        <div class="page-eyebrow">EMPLOYEE WORKSPACE</div>
        <h1>员工工作台</h1>
        <p>当前身份 {{ user?.username ?? '—' }}</p>
      </div>
      <el-button :loading="busy" @click="refreshIdentity">刷新身份</el-button>
    </div>
    <p v-if="error" class="workspace-error" role="alert">{{ error }}<el-button link @click="refreshIdentity">重新验证身份</el-button></p>
    <section class="merchant-card">
      <h2>身份关系</h2>
      <p>{{ missingBinding ? '当前员工身份未绑定店铺或商家，请联系本店负责人。' : '以下编号来自服务端当前登录身份（me），仅用于展示。' }}<el-button link :loading="busy" @click="refreshIdentity">重新验证身份</el-button></p>
      <div class="table-wrap"><table class="merchant-table"><thead><tr><th>用户编号</th><th>商家编号</th><th>店铺编号</th></tr></thead><tbody><tr><td>{{ user?.id ?? '—' }}</td><td>{{ user?.merchant_id ?? '—' }}</td><td>{{ user?.shop_id ?? '—' }}</td></tr></tbody></table></div>
    </section>
    <section class="merchant-card">
      <h2>员工固定权限边界</h2>
      <p>员工只可使用：创建订单、查询本店订单、只读查看同商家商品目录，以及查看经商家管理员批准本店使用的共享库存。最终权限以服务端校验为准；自定义岗位名称不会带来任何额外权限。</p>
      <div class="workspace-links">
        <RouterLink to="/employee/catalog">商品目录（只读）</RouterLink>
        <RouterLink to="/employee/inventory">共享库存（只读）</RouterLink>
        <RouterLink to="/employee/orders">订单查询</RouterLink>
        <RouterLink to="/employee/orders/create">创建订单</RouterLink>
      </div>
    </section>
  </div>
</template>
