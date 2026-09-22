let backendOrigin = ''
let backendToken = ''
import type { RuntimeCapabilities } from './splash-logic'
type DesktopWindow = Window & { __TAURI__?: { core: { invoke: <T>(command: string, args?: Record<string, unknown>) => Promise<T> }; event?: { listen: (event: string, callback: () => void) => Promise<() => void> } } }
export const desktop = (window as DesktopWindow).__TAURI__
export async function initializeRuntime() {
  if (!desktop) return
  const runtime = await desktop.core.invoke<{ host: string; port: number; token: string }>('handshake')
  backendOrigin = `http://${runtime.host}:${runtime.port}`
  backendToken = runtime.token
}
export async function windowAction(command: string) { await desktop?.core.invoke(command) }
export async function inspectRuntime(): Promise<RuntimeCapabilities> {
  return request<RuntimeCapabilities>('/runtime/capabilities')
}
export async function finishSplash() { await desktop?.core.invoke('finish_splash') }
export async function restartBackend() { await desktop?.core.invoke('restart_backend') }
export async function backendLogPath(): Promise<string> {
  return desktop?.core.invoke<string>('backend_log_path') ?? ''
}
function apiBase() { return backendOrigin ? `${backendOrigin}/api/v1` : import.meta.env.VITE_API_BASE ?? '/api/v1' }
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (backendToken) headers.set('Authorization', `Bearer ${backendToken}`)
  const response = await fetch(`${apiBase()}${path}`, { ...init, headers })
  const payload = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof payload?.detail === 'string' ? payload.detail : payload?.message || `请求失败：${response.status}`)
  if (!payload || payload.code !== 0) throw new Error(payload?.message || '服务返回了无效数据')
  return payload.data as T
}
export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', headers: body instanceof FormData ? undefined : { 'Content-Type': 'application/json' }, body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) => request<T>(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
export function wsUrl(): string {
  const origin = backendOrigin || window.location.origin
  const base = import.meta.env.VITE_WS_URL || `${origin.replace(/^http/, 'ws')}/ws`
  return backendToken ? `${base}?token=${encodeURIComponent(backendToken)}` : base
}
export function imageUrl(path: string): string {
  if (!path) return ''
  if (/^(https?:|blob:|data:)/.test(path)) return path
  const url = path.startsWith('/api/') ? `${backendOrigin}${path}` : `${apiBase()}${path}`
  return backendToken ? `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(backendToken)}` : url
}
