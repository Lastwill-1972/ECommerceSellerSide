<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { updateMerchantName } from '@/modules/merchant/api'
import { useMerchantScope } from '@/modules/merchant/useMerchantScope'
import { ApiError } from '@/shared/http/errors'
const name = ref('')
const confirmedName = ref<string | null>(null)
const busy = ref(false)
const error = ref('')
const scope = useMerchantScope(() => { name.value = ''; confirmedName.value = null; busy.value = false; error.value = '' })
async function save() {
  const clean = name.value.trim()
  if (!clean || [...clean].length > 255) { error.value = '请输入 1–255 个字符的商家名称。'; return }
  if (busy.value) return
  busy.value = true; error.value = ''
  const request = scope.capture()
  try {
    const result = await updateMerchantName(clean)
    if (scope.current(request) && result.id !== scope.auth.user?.merchant_id) throw new Error('服务器返回的商家编号与当前身份不一致，已隐藏该响应。')
    if (scope.current(request)) { confirmedName.value = result.name; name.value = result.name; ElMessage.success('商家名称已更新。') }
  } catch (reason) {
    if (!scope.current(request)) return
    if (reason instanceof ApiError && reason.status === 422 && reason.fields.name) error.value = `商家名称：${reason.fields.name.join(' ')}`
    else { const message = await scope.explain(reason); if (scope.current(request)) error.value = message }
  }
  finally { if (scope.current(request)) busy.value = false }
}
</script>
<template>
  <div class="merchant-page"><div class="welcome-row"><div><div class="page-eyebrow">MERCHANT PROFILE</div><h1>商家资料</h1><p>当前商家编号：{{ scope.auth.user?.merchant_id }}</p></div></div>
    <section class="merchant-card profile-form"><label for="merchant-name">商家名称</label><p v-if="confirmedName" class="confirmed-name">当前服务端确认名称：<strong>{{ confirmedName }}</strong></p><el-input id="merchant-name" v-model="name" placeholder="未读取，请输入新名称" @keyup.enter="save"/><small>系统没有商家资料读取接口。成功保存后显示 PATCH 响应中的名称。{{ [...name].length }} / 255 个 Unicode 字符</small><p v-if="error" class="inline-error" role="alert">{{ error }}</p><el-button type="primary" :loading="busy" :disabled="busy" @click="save">保存名称</el-button></section>
  </div>
</template>
