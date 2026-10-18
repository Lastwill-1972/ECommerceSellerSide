import JSONbigFactory from 'json-bigint'

const JSONbig = JSONbigFactory({ storeAsString: true, useNativeBigInt: true })

/** Parse JSON while keeping every integer token as its exact decimal text. */
export function parseApiJson(data: unknown): unknown {
  if (typeof data !== 'string' || data.length === 0) return data
  try {
    return JSONbig.parse(data) as unknown
  } catch {
    return data
  }
}

/** Serialize integer identifiers as exact JSON number tokens for strict integer APIs. */
export function stringifyApiJson(data: unknown): string {
  return JSONbig.stringify(data)
}

/** Render decimal amount strings without ever converting through JS Number. */
export function formatDecimal(value: string, fractionDigits = 2): string {
  if (!/^-?\d+(?:\.\d+)?$/.test(value)) return value
  const negative = value.startsWith('-')
  const unsigned = negative ? value.slice(1) : value
  const [integer = '0', fraction = ''] = unsigned.split('.')
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  if (fraction.length > fractionDigits && /[1-9]/.test(fraction.slice(fractionDigits))) return value
  const padded = fraction.padEnd(fractionDigits, '0').slice(0, fractionDigits)
  return `${negative ? '-' : ''}${grouped}${fractionDigits > 0 ? `.${padded}` : ''}`
}

/** Keep naive database-local DATETIME text as entered by the server. */
export function formatNaiveDateTime(value: string): string {
  return value.replace('T', ' ')
}
