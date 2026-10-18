<script setup lang="ts">
import { useAuthStore } from '@/modules/auth/store'
const auth = useAuthStore()
</script>
<template>
  <div class="merchant-page">
    <div class="welcome-row"><div><div class="page-eyebrow">MERCHANT WORKSPACE <span></span> 商家工作区</div><h1>商家经营概览</h1><p>商家编号来自当前服务端身份：{{ auth.user?.merchant_id }}</p></div></div>
    <section class="merchant-card"><span class="section-kicker">本阶段功能</span><h2>资料、店铺、商品、库存与订单</h2><p>商品、SKU 和库存均通过当前商家身份访问；所有列表在进入时重新读取，人工库存操作带幂等保护。</p><div class="merchant-links"><RouterLink :to="{ name: 'merchant-profile' }">商家资料</RouterLink><RouterLink :to="{ name: 'merchant-shops' }">店铺列表</RouterLink><RouterLink :to="{ name: 'merchant-applications' }">审批中心</RouterLink><RouterLink :to="{ name: 'merchant-orders' }">订单列表</RouterLink><RouterLink :to="{ name: 'merchant-catalog' }">商品管理</RouterLink><RouterLink :to="{ name: 'merchant-inventory' }">库存管理</RouterLink></div></section>
  </div>
</template>
