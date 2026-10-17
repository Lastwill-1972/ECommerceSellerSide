<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/modules/auth/store'

const auth = useAuthStore()
const router = useRouter()

function signOutAndReturnToLogin() {
  auth.signOut()
  void router.replace({ name: 'login' })
}
</script>

<template>
  <main class="system-page">
    <div class="system-art forbidden-art"><span class="art-lock"><el-icon><Lock /></el-icon></span><i></i><b></b><em></em></div>
    <span class="system-code">403</span>
    <h1>当前账号暂不可访问</h1>
    <p>服务端检测到身份关系冲突或当前账号没有访问权限。为保护账户数据，我们没有显示业务内容。</p>
    <div class="system-actions">
      <button class="primary-button link-button" type="button" @click="signOutAndReturnToLogin">退出并重新登录</button>
      <RouterLink class="secondary-button" to="/">返回工作台</RouterLink>
    </div>
    <small>如果问题持续存在，请联系平台管理员检查账号身份配置。</small>
  </main>
</template>
