import { describe, expect, it } from 'vitest'
import axios from 'axios'
import { toApiError } from './errors'

describe('HTTP error mapping', () => {
  it('classifies auth expiry, conflicts, and validation responses with status codes', () => {
    const mapStatus = (status: number, detail: unknown = {}) => toApiError(axios.AxiosError.from(new Error('http'), 'ERR_BAD_REQUEST', undefined, undefined, {
      status, statusText: 'Error', headers: {}, config: { headers: new axios.AxiosHeaders() }, data: { detail },
    }))
    expect(mapStatus(401).code).toBe('unauthorized')
    expect(mapStatus(409).code).toBe('conflict')
    expect(mapStatus(422, [{ loc: ['body', 'rejection_reason'], msg: 'required' }]).fields.rejection_reason).toEqual(['required'])
  })

  it('extracts FastAPI validation fields for form feedback', () => {
    const source = axios.AxiosError.from(new Error('invalid'), 'ERR_BAD_REQUEST', undefined, undefined, {
      status: 422,
      statusText: 'Unprocessable Entity',
      headers: {},
      config: { headers: new axios.AxiosHeaders() },
      data: { detail: [{ loc: ['body', 'username'], msg: 'String should have at least 3 characters' }] },
    })
    const error = toApiError(source)
    expect(error.code).toBe('validation')
    expect(error.fields.username).toEqual(['String should have at least 3 characters'])
  })

  it('classifies server unavailability and network errors', () => {
    const unavailable = axios.AxiosError.from(new Error('offline'), 'ERR_NETWORK')
    expect(toApiError(unavailable).code).toBe('network')
    const service = axios.AxiosError.from(new Error('503'), 'ERR_BAD_RESPONSE', undefined, undefined, {
      status: 503, statusText: 'Service Unavailable', headers: {}, config: { headers: new axios.AxiosHeaders() }, data: {},
    })
    expect(toApiError(service).code).toBe('unavailable')
  })
})
