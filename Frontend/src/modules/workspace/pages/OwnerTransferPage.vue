<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listShops } from '@/modules/merchant/api'
import type { Page, Shop } from '@/shared/types/api'
import { transferOwner, validId, validText, type TransferResult } from '../api'
import { useIdentityScope } from '../useIdentityScope'
import PageButtons from '../components/PageButtons.vue'
const route = useRoute(), admin = computed(() => route.name === 'merchant-owner-transfer')
const shops = ref<Page<Shop> | null>(null), shopId = ref(''), offset = ref(0), action = ref<'CONFIRM' | 'FORCE'>('CONFIRM')
const targetId = ref(''), proposalId = ref(''), reason = ref(''), result = ref<TransferResult | null>(null)
const loading = ref(false), busy = ref(false), error = ref(''), notice = ref('')
let sequence = 0
const scope = useIdentityScope(['SHOP_OWNER', 'MERCHANT_ADMIN'], () => { sequence++; shops.value = null; shopId.value = ''; targetId.value = ''; proposalId.value = ''; reason.value = ''; result.value = null; notice.value = ''; error.value = ''; offset.value = 0; loading.value = false; busy.value = false })
async function load(next = offset.value) {
  const captured = scope.capture(), ticket = ++sequence; shops.value = null; loading.value = true; error.value = ''; offset.value = next
  try {
    const page = await listShops(20, next)
    if (!scope.current(captured) || ticket !== sequence) return
    if (page.items.some(row => row.merchant_id !== scope.auth.user?.merchant_id || (!admin.value && (row.id !== scope.auth.user?.shop_id || row.owner_user_id !== scope.auth.user?.id)))) throw new Error('其他商家或店铺的记录已隐藏。')
    shops.value = page
    if (!page.items.some(row => row.id === shopId.value)) shopId.value = admin.value ? '' : page.items[0]?.id ?? ''
  } catch (cause) { if (scope.current(captured) && ticket === sequence) error.value = scope.explain(cause) }
  finally { if (scope.current(captured) && ticket === sequence) loading.value = false }
}
async function submit() {
  if (busy.value || !scope.auth.user || (admin.value ? scope.auth.user.role !== 'MERCHANT_ADMIN' : scope.auth.user.role !== 'SHOP_OWNER')) return
  const shop = shops.value?.items.find(row => row.id === shopId.value)
  if (!shop || shop.merchant_id !== scope.auth.user.merchant_id) { error.value = '请选择本页中真实属于当前商家的店铺。'; return }
  if ((admin.value && action.value === 'CONFIRM' && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(proposalId.value.trim())) || ((!admin.value || action.value === 'FORCE') && !validId(targetId.value.trim()))) { error.value = '请填写准确的用户编号或完整 UUID。'; return }
  if (admin.value && action.value === 'FORCE' && !validText(reason.value, 2000)) { error.value = '强制交接原因必填，最多2000个字符。'; return }
  const captured = scope.capture(), actor = { ...scope.auth.user }, token = scope.auth.token
  const requested = !admin.value ? { action: 'PROPOSE' as const, target_user_id: targetId.value.trim() } : action.value === 'CONFIRM' ? { action: 'CONFIRM' as const, proposal_id: proposalId.value.trim() } : { action: 'FORCE' as const, target_user_id: targetId.value.trim(), reason: reason.value.trim() }
  busy.value = true; error.value = ''; notice.value = ''; result.value = null
  let committed: TransferResult | null = null
  try {
    if (requested.action === 'FORCE') {
      await ElMessageBox.confirm(`将店铺 ${shop.name} 的负责人强制替换为用户 ${requested.target_user_id}。这不代表旧负责人同意；旧权限被撤销，原员工岗位不会自动恢复。原因：${requested.reason}`, '异常强制交接', { confirmButtonText: '确认强制交接', cancelButtonText: '取消', type: 'warning' })
      if (!scope.current(captured)) return
    }
    const response = await transferOwner(shop.id, requested)
    if (!scope.current(captured)) return
    if (response.shop_id !== shop.id) throw new Error('交接响应店铺不匹配，请刷新核对真实结果。')
    committed = response; result.value = response
    if (response.status === 'PROPOSED') { notice.value = '提议已提交，尚未完成交接。请复制编号交给所属商家管理员。'; return }
    ElMessage.success('交接已成功，正在重新确认身份与店铺信息。')
    try {
      const user = await scope.auth.refreshIdentity()
      if (scope.auth.token !== token || user?.id !== actor.id || user.role !== actor.role || user.merchant_id !== actor.merchant_id || user.shop_id !== actor.shop_id) return
      result.value = response; notice.value = '交接已成功，已重新读取身份。新旧负责人的岗位及权限以各自 /auth/me 为准。'; await load(0)
    } catch { if (scope.auth.token === token) ElMessage.warning('交接已成功，但身份刷新失败。请重新验证身份，不要重复提交交接。') }
  } catch (cause) {
    if (cause === 'cancel' || cause === 'close') return
    if (scope.current(captured)) error.value = committed ? '交接已成功，后续查询失败，请刷新核对；不要重复交接。' : scope.explain(cause)
  } finally { if (scope.auth.token === token && scope.auth.user?.id === actor.id) busy.value = false }
}
async function copy() { if (!result.value?.proposal_id) return; try { await navigator.clipboard.writeText(result.value.proposal_id); notice.value = '提议编号已复制，请交给所属商家管理员。' } catch { notice.value = '无法自动复制，请手动选中提议编号复制。' } }
watch(scope.revision, () => { if (scope.auth.user && (admin.value ? scope.auth.user.role === 'MERCHANT_ADMIN' : scope.auth.user.role === 'SHOP_OWNER')) void load(0) }, { flush: 'post' })
watch(action, () => { result.value = null; error.value = ''; notice.value = ''; reason.value = '' })
onMounted(() => { void load() })
</script>
<template>
  <div class="merchant-page workspace-page">
    <div class="welcome-row"><div><div class="page-eyebrow">OWNER HANDOFF</div><h1>{{ admin ? '负责人交接管理' : '提议负责人交接' }}</h1><p>只有服务器能改变负责人关系；没有待交接提议查询接口。</p></div><el-button :loading="loading" :disabled="busy" @click="load()">刷新店铺</el-button></div>
    <p v-if="error" class="workspace-error" role="alert">{{ error }}</p><p v-if="notice" class="workspace-success" role="status">{{ notice }}</p>
    <section class="merchant-card">
      <p v-if="loading">正在读取本商家店铺…</p><p v-else-if="!shops?.items.length">暂无可操作店铺。</p><form v-else class="workspace-form" @submit.prevent="submit">
        <label>目标店铺<select v-model="shopId" aria-label="目标店铺" :disabled="busy || !admin"><option value="">请选择本页店铺</option><option v-for="shop in shops.items" :key="shop.id" :value="shop.id">{{ shop.name }} · {{ shop.id }}（负责人 {{ shop.owner_user_id }}）</option></select></label><PageButtons v-if="admin" :page="shops" :busy="busy || loading" @change="load" />
        <label v-if="admin">操作类型<select v-model="action" aria-label="操作类型" :disabled="busy"><option value="CONFIRM">正常交接：确认店主提议</option><option value="FORCE">异常强制交接：撤销旧权限</option></select></label>
        <label v-if="admin && action === 'CONFIRM'">店主提供的 proposal_id<input v-model="proposalId" aria-label="proposal_id" :disabled="busy" /><small>填写旧店主主动提交并提供的 UUID；不能代替店主提出同意。</small></label>
        <label v-else>目标接任人用户编号<input v-model="targetId" aria-label="目标接任人用户编号" :disabled="busy" inputmode="numeric" /><small>请输入本店员工或符合资格普通用户的准确 ID；没有通用用户搜索接口。</small></label>
        <label v-if="admin && action === 'FORCE'">强制交接原因<textarea v-model="reason" aria-label="强制交接原因" :disabled="busy" /><small>必填，去首尾空白后最多2000个字符。FORCE 不表示旧负责人同意。</small></label>
        <el-button native-type="submit" :type="admin && action === 'FORCE' ? 'danger' : 'primary'" :loading="busy">{{ !admin ? '提交交接提议' : action === 'CONFIRM' ? '确认正常交接' : '强制交接并二次确认' }}</el-button>
      </form>
    </section>
    <section v-if="result" class="merchant-card" role="status"><h2>服务端交接结果：{{ result.status }}</h2><p>店铺 {{ result.shop_id }} · 当前负责人 {{ result.owner_user_id }} · 目标 {{ result.target_user_id ?? '—' }}</p><template v-if="result.proposal_id"><p>交接提议编号（请交给商家管理员）</p><code class="workspace-uuid">{{ result.proposal_id }}</code><el-button @click="copy">复制 proposal_id</el-button><p>此编号表示提议，不表示交接已完成。离开或刷新身份后，本页不会伪造待交接记录。</p></template></section>
  </div>
</template>
