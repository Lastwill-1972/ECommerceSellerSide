import axios, { type AxiosError } from 'axios'

export type FieldErrors = Record<string, string[]>

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly fields: FieldErrors = {},
    readonly code = 'unknown',
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  if (!axios.isAxiosError(error)) return new ApiError('发生了意外错误，请稍后重试。')
  const axiosError = error as AxiosError<{ detail?: unknown }>
  const status = axiosError.response?.status
  if (!status) return new ApiError('无法连接服务器，请检查网络后重试。', undefined, {}, 'network')

  let message = '请求未能完成，请稍后重试。'
  let code = 'http'
  if (status === 401) { message = '登录状态已失效，请重新登录。'; code = 'unauthorized' }
  else if (status === 403) { message = '当前账号没有访问权限。'; code = 'forbidden' }
  else if (status === 404) { message = '请求的内容不存在。'; code = 'not_found' }
  else if (status === 409) { message = '信息已存在或与当前状态冲突，请检查后重试。'; code = 'conflict' }
  else if (status === 422) { message = '请检查表单中标记的内容。'; code = 'validation' }
  else if (status === 503) { message = '认证服务暂不可用，请稍后重试。'; code = 'unavailable' }

  const fields: FieldErrors = {}
  const detail = axiosError.response?.data?.detail
  if (Array.isArray(detail)) {
    for (const issue of detail) {
      if (typeof issue === 'object' && issue !== null && 'loc' in issue && 'msg' in issue) {
        const item = issue as { loc: unknown[]; msg: unknown }
        const field = item.loc.filter((part) => part !== 'body').join('.') || 'form'
        const text = typeof item.msg === 'string' ? item.msg : '格式不正确'
        fields[field] = [...(fields[field] ?? []), text]
      }
    }
  }
  return new ApiError(message, status, fields, code)
}
