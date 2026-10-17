import { computed, onScopeDispose, ref, watch } from 'vue'
import { useAuthStore } from '@/modules/auth/store'
import { ApiError } from '@/shared/http/errors'
import { useRouter } from 'vue-router'

export function useMerchantScope(onReset: () => void) {
  const auth = useAuthStore()
  const router = useRouter()
  const revision = ref(0)
  const key = computed(() => [auth.scopeEpoch, auth.token, auth.user?.id, auth.user?.merchant_id, auth.user?.role].join('|'))
  watch(key, () => { revision.value += 1; onReset() }, { flush: 'sync' })
  onScopeDispose(() => { revision.value += 1 })
  function capture() { return { key: key.value, revision: revision.value } }
  function current(value: ReturnType<typeof capture>) { return value.key === key.value && value.revision === revision.value && auth.user?.role === 'MERCHANT_ADMIN' }
  async function explain(error: unknown): Promise<string> {
    if (error instanceof ApiError && error.status === 403) {
      await router.replace({ name: 'forbidden' })
      return '当前账号没有访问权限。'
    }
    if (error instanceof ApiError) return error.status === 404 ? '内容不存在或已不可见。' : error.message
    return error instanceof Error ? error.message : '请求失败，请稍后重试。'
  }
  return { auth, revision, capture, current, explain }
}

