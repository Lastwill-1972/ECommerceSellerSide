<script setup lang="ts">
import { useAuthStore } from '@/modules/auth/store'
const auth = useAuthStore()
</script>
<template>
  <div class="merchant-page">
    <div class="welcome-row"><div><div class="page-eyebrow">MERCHANT WORKSPACE <span></span> 商家工作区</div><h1>商家经营概览</h1><p>商家编号来自当前服务端身份：{{ auth.user?.merchant_id }}</p></div></div>
    <section class="merchant-card"><span class="section-kicker">本阶段功能</span><h2>资料、店铺、审批与订单</h2><p>名称仅在成功提交并收到服务端确认后显示为当前商家名称。列表和详情每次打开都会重新读取。</p><div class="merchant-links"><RouterLink :to="{ name: 'merchant-profile' }">商家资料</RouterLink><RouterLink :to="{ name: 'merchant-shops' }">店铺列表</RouterLink><RouterLink :to="{ name: 'merchant-applications' }">审批中心</RouterLink><RouterLink :to="{ name: 'merchant-orders' }">订单列表</RouterLink></div></section>
  </div>
</template>
