import { demoFetch } from './demo-api'
import { useCallback, useEffect, useState } from 'react'

export class ApiError extends Error {
  code: string
  status: number
  constructor(message: string, code = 'REQUEST_FAILED', status = 0) { super(message); this.code = code; this.status = status }
}

let tokenPromise: Promise<string> | undefined
const token = () => tokenPromise ??= demoFetch('/api/state').then(async response => {
  if (!response.ok) throw new ApiError('无法初始化本地会话', 'SESSION_FAILED', response.status)
  return (await response.json()).token as string
})

async function parse<T>(response: Response): Promise<T> {
  const body = await response.json()
  if (!response.ok) {
    const error = body.error
    throw new ApiError(typeof error === 'string' ? error : error?.message || '请求失败', error?.code, response.status)
  }
  return (body.data ?? body) as T
}

export const get = <T,>(path: string) => demoFetch(path).then(parse<T>)
export const forGame = (path: string, game: string) => `${path}${path.includes('?') ? '&' : '?'}game=${encodeURIComponent(game)}`

export async function post<T>(path: string, body: unknown, signal?: AbortSignal) {
  return parse<T>(await demoFetch(path, {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json', 'X-InsightPulse-Token': await token() },
    body: JSON.stringify(body),
  }))
}

export function useResource<T>(path: string) {
  const [data, setData] = useState<T>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  const reload = useCallback(() => setRevision(current => current + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    // oxlint-disable-next-line react/set-state-in-effect -- shared by initial load and explicit retry
    setLoading(true)
    setError('')
    demoFetch(path, { signal: controller.signal }).then(parse<T>).then(value => { if (!controller.signal.aborted) setData(value) })
      .catch(error => { if (!controller.signal.aborted) setError(error.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [path, revision])
  return { data, error, loading, reload }
}
