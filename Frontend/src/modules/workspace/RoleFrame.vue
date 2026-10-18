<script setup lang="ts">
import { watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/modules/auth/store'
const auth = useAuthStore(), route = useRoute(), router = useRouter()
let redirecting = false
watch(() => [auth.identityState, auth.user?.role, auth.user?.shop_id] as const, () => {
  const allowed = route.meta.allowedRoles as string[] | undefined
  if (!redirecting && auth.identityState === 'ready' && allowed && (!auth.user || !allowed.includes(auth.user.role))) {
    // The destination guard refreshes /me too. Do not recursively start another
    // navigation while this frame is still mounted during that verification.
    redirecting = true
    // Keep the latch for this frame's lifetime: a cancelled competing navigation
    // must not re-arm it while the replacement guard is still verifying /me.
    void router.replace({ name: 'dashboard' })
  }
}, { immediate: true })
</script>
<template><RouterView :key="$route.path" /></template>
