<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ApiError } from '@/shared/http/errors'
import { useAuthStore } from '@/modules/auth/store'
import { User, Message, Lock, CircleCheck } from '@element-plus/icons-vue'

const auth = useAuthStore()
const router = useRouter()
const formRef = ref()
const loading = ref(false)
const form = reactive({ username: '', email: '', password: '', confirm: '' })
const fieldErrors = reactive<Record<string, string>>({})
const validateConfirm = (_rule: unknown, value: string, done: (error?: Error) => void) => {
  if (value !== form.password) done(new Error('两次输入的密码不一致'))
  else done()
}
const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 100, message: '用户名长度为 3–100 个字符', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9][A-Za-z0-9_.-]*$/, message: '以字母或数字开头，仅可包含字母、数字、_ . -', trigger: 'blur' },
  ],
  email: [{ required: true, message: '请输入邮箱地址', trigger: 'blur' }, { type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
  password: [
    { required: true, message: '请设置登录密码', trigger: 'blur' },
    { min: 12, max: 128, message: '密码长度为 12–128 个字符', trigger: 'blur' },
  ],
  confirm: [{ required: true, message: '请再次输入密码', trigger: 'blur' }, { validator: validateConfirm, trigger: 'blur' }],
}

async function submit() {
  if (loading.value) return
  try { await formRef.value?.validate() } catch { return }
  for (const key of Object.keys(fieldErrors)) delete fieldErrors[key]
  loading.value = true
  try {
    await auth.signUp({ username: form.username.trim(), email: form.email.trim(), password: form.password })
    ElMessage.success('账号创建成功，请登录')
    await router.replace({ name: 'login', query: { created: '1' } })
  } catch (error) {
    if (error instanceof ApiError && error.status === 422) {
      for (const [field, messages] of Object.entries(error.fields)) {
        const target = ['username', 'email', 'password'].includes(field) ? field : 'username'
        fieldErrors[target] = messages.join('，')
      }
      if (Object.keys(fieldErrors).length === 0) ElMessage.error(error.message)
    } else ElMessage.error(error instanceof ApiError && error.status === 409 ? '用户名或邮箱已被使用，请更换后重试。' : error instanceof ApiError ? error.message : '注册失败，请稍后重试。')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page register-page">
    <div class="auth-left">
      <RouterLink class="brand auth-brand" to="/login"><span class="brand-mark">商</span><span><strong>拾集商家</strong><small>SELLER CONSOLE</small></span></RouterLink>
      <div class="auth-intro">
        <span class="eyebrow"><i></i> 开启你的经营旅程</span>
        <h1>好生意<br /><em>从这里开始</em></h1>
        <p>创建账号，进入清晰、可靠的商家服务空间。</p>
        <div class="auth-illustration" aria-hidden="true"><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="illustration-card card-back"><span></span><span></span><span></span></div><div class="illustration-card card-front"><div class="mini-icon">✦</div><div class="mini-lines"><i></i><i></i></div><div class="mini-chart"><b></b><b></b><b></b><b></b><b></b><b></b><b></b></div></div><div class="float-dot dot-a"></div><div class="float-dot dot-b"></div></div>
      </div>
      <div class="auth-left-footer"><span>安全 · 简洁 · 高效</span><span>© 2026 拾集商家</span></div>
    </div>
    <div class="auth-right">
      <div class="auth-form-wrap register-form-wrap">
        <div class="mobile-brand brand"><span class="brand-mark">商</span><span><strong>拾集商家</strong><small>SELLER CONSOLE</small></span></div>
        <div class="form-heading"><span class="eyebrow">CREATE ACCOUNT</span><h2>创建账号</h2><p>填写以下信息，创建基础登录账号。业务身份需按商家或店铺关系审批后获得。</p></div>
        <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @keyup.enter="submit" @submit.prevent="submit">
          <el-form-item label="用户名" prop="username" :error="fieldErrors.username"><el-input v-model="form.username" autocomplete="username" placeholder="3–100 位，以字母或数字开头" :prefix-icon="User" @input="delete fieldErrors.username" /></el-form-item>
          <el-form-item label="邮箱地址" prop="email" :error="fieldErrors.email"><el-input v-model="form.email" autocomplete="email" placeholder="name@example.com" :prefix-icon="Message" @input="delete fieldErrors.email" /></el-form-item>
          <el-form-item label="设置密码" prop="password" :error="fieldErrors.password"><el-input v-model="form.password" type="password" autocomplete="new-password" placeholder="至少 12 位，最多 128 位" :prefix-icon="Lock" show-password @input="delete fieldErrors.password" /></el-form-item>
          <el-form-item label="确认密码" prop="confirm"><el-input v-model="form.confirm" type="password" autocomplete="new-password" placeholder="请再次输入密码" :prefix-icon="CircleCheck" show-password /></el-form-item>
          <div class="password-hint"><el-icon><InfoFilled /></el-icon><span>密码长度需为 <strong>12–128 个字符</strong>。请妥善保管你的登录密码。</span></div>
          <el-button class="primary-button auth-submit" type="primary" :loading="loading" @click="submit">创建账号 <el-icon><Right /></el-icon></el-button>
        </el-form>
        <div class="auth-switch">已经有账号？ <RouterLink to="/login">返回登录 <el-icon><ArrowRight /></el-icon></RouterLink></div>
      </div>
      <div class="auth-right-footer">请妥善保管登录凭据，并避免在公共设备上保存登录状态。</div>
    </div>
  </div>
</template>
