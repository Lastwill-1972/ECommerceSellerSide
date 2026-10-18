import { computed, onScopeDispose, ref, watch } from 'vue'
import { useAuthStore } from '@/modules/auth/store'
import { ApiError } from '@/shared/http/errors'
import type { IdentityRole } from '@/shared/types/api'

/** Extends the existing merchant capture/current protocol to shop and NONE views. */
export function useIdentityScope(allowed: IdentityRole[], reset: () => void) {
  const auth = useAuthStore(), revision = ref(0)
  const key = computed(() => [auth.scopeEpoch, auth.token, auth.user?.id, auth.user?.merchant_id, auth.user?.shop_id, auth.user?.role].join('|'))
  watch(key, () => { revision.value++; reset() }, { flush: 'sync' })
  onScopeDispose(() => { revision.value++ })
  function capture() { return { key: key.value, revision: revision.value } }
  function current(ticket: ReturnType<typeof capture>) { return ticket.key === key.value && ticket.revision === revision.value && !!auth.user && allowed.includes(auth.user.role) }
  function explain(cause: unknown) {
    if (cause instanceof ApiError) {
      const fields = Object.values(cause.fields).flat().join('；')
      if (cause.status === 409) return `业务状态冲突：${cause.message} 请刷新后核对，不要重复提交。`
      if (cause.status === 403) return '当前资源或操作未获授权。可刷新身份确认资格；仓库未授权不代表店主身份失效。'
      return fields ? `${cause.message} ${fields}` : cause.message
    }
    return cause instanceof Error ? cause.message : '请求失败，请重试。'
  }
  return { auth, revision, capture, current, explain }
}
