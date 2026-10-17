<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ApiError } from '@/shared/http/errors'
import { useAuthStore } from '@/modules/auth/store'
import { safeInternalRedirect } from '@/app/router'
import { User, Lock } from '@element-plus/icons-vue'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const formRef = ref()
const loading = ref(false)
const form = reactive({ identifier: '', password: '' })
const fieldErrors = reactive<Record<string, string>>({})
const unavailable = computed(() => route.query.reason === 'unavailable')
const expired = computed(() => route.query.reason === 'expired')
const rules = {
  identifier: [{ required: true, message: '请输入用户名或邮箱', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function submit() {
  if (loading.value) return
  try { await formRef.value?.validate() } catch { return }
  for (const key of Object.keys(fieldErrors)) delete fieldErrors[key]
  loading.value = true
  try {
    await auth.signIn({ identifier: form.identifier.trim(), password: form.password })
    ElMessage.success('登录成功，欢迎回来')
    await router.replace(safeInternalRedirect(route.query.redirect) ?? { name: 'dashboard' })
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 401) ElMessage.error('用户名、邮箱或密码不正确。')
      else if (error.status === 422) {
        for (const [field, messages] of Object.entries(error.fields)) {
          const target = field === 'password' ? 'password' : 'identifier'
          fieldErrors[target] = messages.join('，')
        }
        if (Object.keys(fieldErrors).length === 0) ElMessage.error(error.message)
      }
      else ElMessage.error(error.message)
    } else ElMessage.error('登录失败，请稍后重试。')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-left">
      <RouterLink class="brand auth-brand" to="/login">
        <span class="brand-mark">商</span><span><strong>拾集商家</strong><small>SELLER CONSOLE</small></span>
      </RouterLink>
      <div class="auth-intro">
        <span class="eyebrow"><i></i> 一站式经营工作台</span>
        <h1>让生意经营<br /><em>更清晰一点</em></h1>
        <p>从一个账号开始，轻松进入你的商家工作空间。</p>
        <div class="auth-illustration" aria-hidden="true">
          <div class="orbit orbit-one"></div><div class="orbit orbit-two"></div>
          <div class="illustration-card card-back"><span></span><span></span><span></span></div>
          <div class="illustration-card card-front"><div class="mini-icon">↗</div><div class="mini-lines"><i></i><i></i></div><div class="mini-chart"><b></b><b></b><b></b><b></b><b></b><b></b><b></b></div></div>
          <div class="float-dot dot-a"></div><div class="float-dot dot-b"></div>
        </div>
      </div>
      <div class="auth-left-footer"><span>安全 · 简洁 · 高效</span><span>© 2026 拾集商家</span></div>
    </div>
    <div class="auth-right">
      <div class="auth-form-wrap">
        <div class="mobile-brand brand"><span class="brand-mark">商</span><span><strong>拾集商家</strong><small>SELLER CONSOLE</small></span></div>
        <div class="form-heading"><span class="eyebrow">WELCOME BACK</span><h2>欢迎回来</h2><p>登录你的账号，继续管理经营事务。</p></div>
        <el-alert v-if="expired" title="登录状态已失效，请重新登录。" type="warning" :closable="false" show-icon class="form-alert" />
        <el-alert v-if="unavailable" title="暂时无法验证登录状态，请重新登录后再试。" type="warning" :closable="false" show-icon class="form-alert" />
        <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @keyup.enter="submit" @submit.prevent="submit">
          <el-form-item label="用户名 / 邮箱" prop="identifier" :error="fieldErrors.identifier">
            <el-input v-model="form.identifier" autocomplete="username" placeholder="请输入用户名或邮箱" :prefix-icon="User" @input="delete fieldErrors.identifier" />
          </el-form-item>
          <el-form-item label="登录密码" prop="password" :error="fieldErrors.password">
            <el-input v-model="form.password" type="password" autocomplete="current-password" placeholder="请输入密码" :prefix-icon="Lock" show-password @input="delete fieldErrors.password" />
          </el-form-item>
          <div class="form-meta"><span>账号身份由服务端实时确认</span><el-icon><CircleCheck /></el-icon></div>
          <el-button class="primary-button auth-submit" type="primary" :loading="loading" @click="submit">登 录 <el-icon><Right /></el-icon></el-button>
        </el-form>
        <div class="auth-switch">还没有账号？ <RouterLink to="/register">创建商家账号 <el-icon><ArrowRight /></el-icon></RouterLink></div>
        <div class="auth-help"><el-icon><InfoFilled /></el-icon> 新账号默认没有业务身份，需根据商家或店铺关系完成审批后获得权限。</div>
      </div>
      <div class="auth-right-footer">请妥善保管登录凭据，并避免在公共设备上保存登录状态。</div>
    </div>
  </div>
</template>
