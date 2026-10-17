<script setup lang="ts">
import { ref } from 'vue'
import ReviewQueue from '@/modules/merchant/components/ReviewQueue.vue'
const kind = ref<'shop' | 'warehouse'>('shop')
</script>
<template><div class="merchant-page"><div class="list-toolbar queue-switch"><el-button :type="kind === 'shop' ? 'primary' : 'default'" @click="kind = 'shop'">店铺申请</el-button><el-button :type="kind === 'warehouse' ? 'primary' : 'default'" @click="kind = 'warehouse'">仓库授权申请</el-button></div><ReviewQueue :key="kind" :kind="kind" /></div></template>
