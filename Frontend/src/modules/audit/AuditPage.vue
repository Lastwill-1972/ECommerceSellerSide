<script setup lang="ts">
import { ref } from 'vue'
import * as api from './api'
import { useIdentityScope } from '@/modules/workspace/useIdentityScope'
import PageButtons from '@/modules/workspace/components/PageButtons.vue'
import { formatNaiveDateTime } from '@/shared/http/precision'
import type { Page } from '@/shared/types/api'

const ALLOWED = ['MERCHANT_ADMIN', 'SHOP_OWNER'] as const
const eventType = ref(''), targetType = ref(''), startAt = ref(''), endAt = ref('')
const page = ref<Page<api.AuditEvent> | null>(null)
const loading = ref(false), loaded = ref(false), error = ref(''), offset = ref(0)
let sequence = 0

// Any auth epoch/token/user/merchant/shop/role change clears filters and results immediately.
function reset() {
  sequence++
  eventType.value = ''; targetType.value = ''; startAt.value = ''; endAt.value = ''
  page.value = null; offset.value = 0
  loading.value = false; loaded.value = false; error.value = ''
}

// Declared after its refs and the reset callback it depends on.
const scope = useIdentityScope([...ALLOWED], reset)

function optionalText(label: string, value: string): string | null {
  const text = value.trim()
  if (text.length === 0) return ''
  if (!api.validFilterText(value)) { error.value = `${label}去除首尾空白后需为1至100个字符。`; return null }
  return text
}

/** Validate the five filters locally; the server still owns range and format rulings. */
function filters() {
  const type = optionalText('事件类型', eventType.value)
  if (type === null) return null
  const target = optionalText('目标类型', targetType.value)
  if (target === null) return null
  const start = startAt.value, end = endAt.value
  if (start && !api.validNaiveDateTime(start)) { error.value = '开始时间格式不正确，请输入不带时区的本地时间。'; return null }
  if (end && !api.validNaiveDateTime(end)) { error.value = '结束时间格式不正确，请输入不带时区的本地时间。'; return null }
  // Same-length naive ISO text compares chronologically as plain text; no Date conversion.
  if (start && end && start > end) { error.value = '开始时间不得晚于结束时间。'; return null }
  return { event_type: type, target_type: target, start_at: start, end_at: end }
}

async function load(next = offset.value, refresh = false) {
  if (scope.auth.user?.role !== 'MERCHANT_ADMIN' && scope.auth.user?.role !== 'SHOP_OWNER') return
  const query = filters()
  if (!query) return
  const target = Math.max(0, Math.min(100000, next)), captured = scope.capture(), ticket = ++sequence
  loading.value = true; error.value = ''
  if (refresh) page.value = null
  try {
    const result = await api.auditEvents({ limit: 20, offset: target, ...query })
    if (!scope.current(captured) || ticket !== sequence) return
    page.value = result; offset.value = result.offset; loaded.value = true
  } catch (cause) {
    if (!scope.current(captured) || ticket !== sequence) return
    page.value = null; loaded.value = false; error.value = scope.explain(cause)
  } finally {
    if (scope.current(captured) && ticket === sequence) loading.value = false
  }
}

function submit() {
  if (loading.value) return
  offset.value = 0
  void load(0, true)
}

function clearFilters() {
  eventType.value = ''; targetType.value = ''; startAt.value = ''; endAt.value = ''
  error.value = ''; page.value = null; loaded.value = false; offset.value = 0
}

function changePage(next: number) { if (!loading.value) void load(next) }

function eventDataText(data: Record<string, unknown>) {
  try { return JSON.stringify(data) ?? '' } catch { return '（事件数据无法显示）' }
}
</script>

<template>
  <div class="merchant-page workspace-page audit-page">
    <div class="welcome-row">
      <div>
        <div class="page-eyebrow">AUDIT TRAIL</div>
        <h1>审计事件查询</h1>
        <p>仅展示当前权限范围内可见的审计事件。审计可能独立记录并存在丢失或缺项，不构成完整、不可篡改的凭证；列表为空或缺少事件不能证明账号未执行过相关操作。</p>
        <p>事件中的目标编号不能用于推断租户归属，请勿凭目标编号猜测其他商家或店铺的数据，本页不会据此跳转任何商家或店铺资源。</p>
      </div>
      <button type="button" class="el-button" :disabled="loading" @click="submit">刷新</button>
    </div>

    <p v-if="error" class="workspace-error" role="alert">{{ error }} <button type="button" class="el-button" :disabled="loading" @click="load(offset, true)">重试</button></p>
    <p v-if="!scope.auth.user" class="workspace-error" role="alert">当前未登录，无法查询审计事件。</p>
    <p v-else-if="!ALLOWED.includes(scope.auth.user.role as typeof ALLOWED[number])" class="workspace-error" role="alert">当前身份无权查询审计事件，仅商家管理员或店主可见。</p>

    <section class="merchant-card">
      <form class="workspace-inline audit-filters" @submit.prevent="submit">
        <label>事件类型
          <input v-model="eventType" aria-label="事件类型筛选" placeholder="可选，1-100字符" maxlength="100" :disabled="loading" />
        </label>
        <label>目标类型
          <input v-model="targetType" aria-label="目标类型筛选" placeholder="可选，1-100字符" maxlength="100" :disabled="loading" />
        </label>
        <label>开始时间
          <input v-model="startAt" type="datetime-local" step="1" aria-label="开始时间筛选" :disabled="loading" />
        </label>
        <label>结束时间
          <input v-model="endAt" type="datetime-local" step="1" aria-label="结束时间筛选" :disabled="loading" />
        </label>
        <div class="audit-actions">
          <button type="submit" class="el-button el-button--primary" :disabled="loading">查询</button>
          <button type="button" class="el-button" :disabled="loading" @click="clearFilters">清空筛选</button>
        </div>
      </form>

      <p v-if="loading" class="list-state">正在加载…</p>
      <p v-else-if="!loaded && !page" class="list-state">设置筛选条件后点击查询；分页每页20条。</p>
      <p v-else-if="!page?.items.length" class="list-state">当前筛选条件下没有可见的审计事件，这不代表相关操作从未发生。</p>
      <div v-else class="table-wrap">
        <table class="merchant-table">
          <thead><tr><th>事件编号</th><th>操作人编号</th><th>事件类型</th><th>目标类型</th><th>目标编号</th><th>结果</th><th>事件数据</th><th>发生时间</th></tr></thead>
          <tbody>
            <tr v-for="row in page.items" :key="row.id">
              <td>{{ row.id }}</td>
              <td>{{ row.actor_user_id }}</td>
              <td>{{ row.event_type }}</td>
              <td>{{ row.target_type }}</td>
              <td>{{ row.target_id }}</td>
              <td>{{ row.outcome }}</td>
              <td><pre class="audit-data" aria-label="事件数据">{{ eventDataText(row.event_data) }}</pre></td>
              <td>{{ formatNaiveDateTime(row.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <PageButtons :page="page" :busy="loading" @change="changePage" />
    </section>
  </div>
</template>

<style scoped>
.audit-page { min-width: 0; }
.audit-filters { flex-wrap: wrap; gap: 12px; align-items: flex-end; }
.audit-filters label { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.audit-filters input { min-width: 0; max-width: 100%; }
.audit-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.audit-data { margin: 0; max-width: 360px; max-height: 160px; overflow: auto; white-space: pre-wrap; word-break: break-all; font-size: 12px; }
.table-wrap { max-width: 100%; overflow-x: auto; }
@media (max-width: 720px) {
  .audit-filters label { flex: 1 1 100%; }
  .audit-filters input { width: 100%; }
}
</style>
