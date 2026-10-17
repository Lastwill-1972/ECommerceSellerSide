<script setup lang="ts">
import { watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/modules/auth/store'
const auth = useAuthStore(), router = useRouter()
watch(() => [auth.identityState, auth.user?.role, auth.user?.merchant_id] as const, ([state, role, merchantId]) => {
  if (state === 'ready' && (role !== 'MERCHANT_ADMIN' || merchantId === null)) void router.replace({ name: 'forbidden' })
}, { immediate: true })
</script>
<template><RouterView /></template>
