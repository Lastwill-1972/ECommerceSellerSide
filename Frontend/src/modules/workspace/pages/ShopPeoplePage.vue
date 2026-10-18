<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElDialog, ElMessageBox } from 'element-plus'
import * as api from '../api'
import { useIdentityScope } from '../useIdentityScope'
import PageButtons from '../components/PageButtons.vue'
import { formatNaiveDateTime } from '@/shared/http/precision'
import type { Page } from '@/shared/types/api'
const route = useRoute(), mode = computed(() => route.name === 'shop-applications' ? 'applications' : route.name === 'shop-roles' ? 'roles' : 'employees')
const title = computed(() => ({ applications: '员工入职审批', roles: '店铺岗位管理', employees: '本店员工' })[mode.value])
const staff = ref<Page<api.Employee> | null>(null), rolePage = ref<Page<api.EmployeeRole> | null>(null), applications = ref<Page<api.EmployeeApplication> | null>(null)
const loading = ref(false), busy = ref(false), error = ref(''), notice = ref(''), offset = ref(0), roleOffset = ref(0), status = ref('')
const roleName = ref(''), choices = ref<Record<string, string>>({}), selected = ref<api.EmployeeApplication | null>(null), decision = ref<'APPROVE' | 'REJECT'>('APPROVE'), reason = ref('')
let sequence = 0
const scope = useIdentityScope(['SHOP_OWNER'], () => { sequence++; staff.value = null; rolePage.value = null; applications.value = null; selected.value = null; choices.value = {}; reason.value = ''; roleName.value = ''; status.value = ''; error.value = ''; notice.value = ''; busy.value = false; loading.value = false; offset.value = 0; roleOffset.value = 0 })
async function load(next = offset.value) {
  const shopId = scope.auth.user?.shop_id; if (!shopId) return
  const captured = scope.capture(), ticket = ++sequence; staff.value = null; rolePage.value = null; applications.value = null; loading.value = true; error.value = ''; offset.value = next
  try {
    if (mode.value === 'applications') {
      const result = await api.employeeApplications(20, next, status.value || undefined)
      if (!scope.current(captured) || ticket !== sequence) return
      if (result.items.some(item => item.shop_id !== shopId)) throw new Error('其他店铺的申请已被隐藏。')
      applications.value = result
    } else {
      const [people, roles] = await Promise.all([mode.value === 'employees' ? api.employees(shopId, 20, next) : Promise.resolve(null), api.roles(shopId, 20, mode.value === 'roles' ? next : roleOffset.value)])
      if (!scope.current(captured) || ticket !== sequence) return
      if (people?.items.some(item => item.shop_id !== shopId) || roles.items.some(item => item.shop_id !== shopId)) throw new Error('其他店铺的员工或岗位已被隐藏。')
      staff.value = people; rolePage.value = roles; choices.value = {}
    }
  } catch (cause) { if (scope.current(captured) && ticket === sequence) error.value = scope.explain(cause) }
  finally { if (scope.current(captured) && ticket === sequence) loading.value = false }
}
async function mutate(action: () => Promise<unknown>, message: string) {
  if (busy.value || scope.auth.user?.role !== 'SHOP_OWNER') return
  const captured = scope.capture(); busy.value = true; error.value = ''; notice.value = ''
  try { await action(); if (!scope.current(captured)) return; notice.value = message; selected.value = null; reason.value = ''; roleName.value = ''; await load() }
  catch (cause) { if (scope.current(captured)) error.value = scope.explain(cause) }
  finally { if (scope.current(captured)) busy.value = false }
}
function createRole() {
  if (!api.validText(roleName.value, 100)) { error.value = '岗位名称必填，最多100个字符。'; return }
  const shop = scope.auth.user?.shop_id, name = roleName.value.trim(); if (shop) void mutate(() => api.createRole(shop, name), '岗位已创建。岗位名称不会改变实际权限。')
}
function assign(row: api.Employee) {
  const shop = scope.auth.user?.shop_id, chosen = choices.value[row.user_id]
  if (!shop || row.shop_id !== shop || !rolePage.value?.items.some(role => role.id === chosen && role.shop_id === shop)) { error.value = '只能选择本店当前岗位页中的岗位。'; return }
  void mutate(() => api.assignRole(shop, row.user_id, chosen!), '岗位已更新。下方员工关系以重新查询结果为准。')
}
async function remove(row: api.Employee) {
  if (busy.value || row.shop_id !== scope.auth.user?.shop_id) return
  const captured = scope.capture(); busy.value = true; error.value = ''
  try {
    await ElMessageBox.confirm(`移除员工 ${row.user_id} 将解除本店在职关系，不会删除用户账号或历史申请。`, '确认移除员工', { confirmButtonText: '确认移除', cancelButtonText: '取消', type: 'warning' })
    if (!scope.current(captured)) return
    await api.removeEmployee(row.shop_id, row.user_id)
    if (!scope.current(captured)) return
    notice.value = '员工已移除（204），账号和历史申请保留。'; await load()
  } catch (cause) { if (cause !== 'cancel' && cause !== 'close' && scope.current(captured)) error.value = scope.explain(cause) }
  finally { if (scope.current(captured)) busy.value = false }
}
function openReview(row: api.EmployeeApplication, action: 'APPROVE' | 'REJECT') { if (!busy.value) { selected.value = row; decision.value = action; reason.value = ''; error.value = '' } }
function review() {
  const row = selected.value; if (!row || row.shop_id !== scope.auth.user?.shop_id) return
  if (decision.value === 'REJECT' && !api.validText(reason.value, 2000)) { error.value = '驳回原因必填，最多2000个字符。'; return }
  const action = decision.value, rejection = reason.value.trim()
  void mutate(() => api.reviewEmployee(row.id, action, action === 'REJECT' ? rejection : undefined), '审批已完成。下方状态以重新查询结果为准。')
}
watch(status, () => { void load(0) })
watch(scope.revision, () => { if (scope.auth.user?.role === 'SHOP_OWNER') void load(0) }, { flush: 'post' })
onMounted(() => { void load() })
</script>
<template>
  <div class="merchant-page workspace-page">
    <div class="welcome-row"><div><div class="page-eyebrow">SHOP TEAM</div><h1>{{ title }}</h1><p>仅管理当前店铺 {{ scope.auth.user?.shop_id ?? '—' }}。岗位仅用于分类，所有员工实际权限相同。</p></div><el-button :loading="loading" :disabled="busy" @click="load()">刷新</el-button></div>
    <p v-if="error" class="workspace-error" role="alert">{{ error }} <el-button link @click="load()">重试</el-button></p><p v-if="notice" class="workspace-success" role="status">{{ notice }}</p>
    <section class="merchant-card">
      <form v-if="mode === 'roles'" class="workspace-inline" @submit.prevent="createRole"><label>新岗位名称<input v-model="roleName" aria-label="新岗位名称" :disabled="busy" /></label><el-button native-type="submit" type="primary" :loading="busy">创建岗位</el-button></form>
      <label v-if="mode === 'applications'">申请状态 <select v-model="status" aria-label="申请状态" :disabled="busy"><option value="">全部状态</option><option value="PENDING">待审核</option><option value="APPROVED">已批准</option><option value="REJECTED">已驳回</option></select></label>
      <p v-if="loading" class="list-state">正在加载…</p>
      <template v-else-if="mode === 'applications'"><p v-if="!applications?.items.length" class="list-state">暂无本店员工申请。</p><div v-else class="table-wrap"><table class="merchant-table"><thead><tr><th>申请编号</th><th>用户编号</th><th>状态</th><th>驳回原因</th><th>操作</th></tr></thead><tbody><tr v-for="row in applications.items" :key="row.id"><td>{{ row.id }}</td><td>{{ row.applicant_user_id }}</td><td>{{ api.statusName(row.status) }}</td><td>{{ row.rejection_reason ?? '—' }}</td><td><template v-if="row.status === 'PENDING'"><el-button link :disabled="busy" @click="openReview(row, 'APPROVE')">批准</el-button><el-button link :disabled="busy" @click="openReview(row, 'REJECT')">驳回</el-button></template></td></tr></tbody></table></div><PageButtons :page="applications" :busy="busy || loading" @change="load" /></template>
      <template v-else-if="mode === 'roles'"><p v-if="!rolePage?.items.length" class="list-state">暂无岗位。</p><div v-else class="table-wrap"><table class="merchant-table"><thead><tr><th>岗位编号</th><th>名称</th><th>创建时间</th></tr></thead><tbody><tr v-for="row in rolePage.items" :key="row.id"><td>{{ row.id }}</td><td>{{ row.name }}</td><td>{{ formatNaiveDateTime(row.created_at) }}</td></tr></tbody></table></div><PageButtons :page="rolePage" :busy="busy || loading" @change="load" /></template>
      <template v-else><p v-if="!staff?.items.length" class="list-state">暂无在职员工。</p><div v-else class="table-wrap"><table class="merchant-table"><thead><tr><th>用户编号</th><th>当前岗位</th><th>入职时间</th><th>分配本店岗位</th><th>操作</th></tr></thead><tbody><tr v-for="row in staff.items" :key="row.id"><td>{{ row.user_id }}</td><td>{{ row.role_name }} · {{ row.role_id }}</td><td>{{ formatNaiveDateTime(row.joined_at) }}</td><td><select v-model="choices[row.user_id]" :aria-label="`员工 ${row.user_id} 的新岗位`" :disabled="busy"><option value="">选择岗位</option><option v-for="role in rolePage?.items" :key="role.id" :value="role.id">{{ role.name }}</option></select></td><td><el-button link :disabled="busy || !choices[row.user_id]" @click="assign(row)">保存岗位</el-button><el-button link type="danger" :disabled="busy" @click="remove(row)">移除员工</el-button></td></tr></tbody></table></div><PageButtons :page="staff" :busy="busy || loading" @change="load" /><p>岗位选项页（不影响权限）</p><PageButtons :page="rolePage" :busy="busy || loading" @change="roleOffset = $event; load()" /></template>
    </section>
    <ElDialog :model-value="!!selected" :title="decision === 'APPROVE' ? '批准员工入职' : '驳回员工申请'" width="min(500px, calc(100vw - 32px))" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy" @update:model-value="value => { if (!value && !busy) selected = null }"><p>申请 {{ selected?.id }} · 用户 {{ selected?.applicant_user_id }}</p><label v-if="decision === 'REJECT'">驳回原因<textarea v-model="reason" aria-label="驳回原因" :disabled="busy" /></label><p v-if="error" class="workspace-error" role="alert">{{ error }}</p><template #footer><el-button :disabled="busy" @click="selected = null">取消</el-button><el-button type="primary" :loading="busy" @click="review">确认审批</el-button></template></ElDialog>
  </div>
</template>
