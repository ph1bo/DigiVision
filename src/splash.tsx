import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { backendLogPath, desktop, finishSplash, initializeRuntime, inspectRuntime, restartBackend } from './api'
import { summarizeCapabilities, type SplashSummary } from './splash-logic'
import './splash.css'

function SplashApp() {
  const [summary, setSummary] = useState<SplashSummary | null>(null)
  const [error, setError] = useState('')
  const [logPath, setLogPath] = useState('')
  const [retrying, setRetrying] = useState(false)

  async function boot() {
    setRetrying(true)
    setError('')
    try {
      await initializeRuntime()
      const capabilities = await inspectRuntime()
      const next = summarizeCapabilities(capabilities)
      setSummary(next)
      if (next.blocked) {
        setError('必要运行环境未就绪，请检查日志后重试')
        setLogPath(await backendLogPath())
        return
      }
      await new Promise(resolve => window.setTimeout(resolve, 300))
      await finishSplash()
    } catch (cause) {
      setSummary(null)
      setError(String(cause instanceof Error ? cause.message : cause))
      setLogPath(await backendLogPath())
    } finally {
      setRetrying(false)
    }
  }

  useEffect(() => { void boot() }, [])

  async function retry() {
    await restartBackend()
    await boot()
  }

  const entries = summary ? Object.entries(summary.steps) : []
  return (
    <main className="splash-shell" data-tauri-drag-region>
      <section className="splash-panel">
        <header className="splash-header">
          <div className="splash-mark">DV</div>
          <div>
            <div className="splash-brand">DigiVision</div>
            <div className="splash-caption">AI 视觉检测工业软件</div>
          </div>
          <span className="splash-version">DEMO</span>
        </header>
        <div className="splash-divider" />
        <div className="splash-state">
          <div className="splash-state-title">正在准备运行环境</div>
          <div className="splash-state-subtitle">首次启动会检查本地服务和设备环境</div>
        </div>
        <div className="splash-progress-track"><span style={{ width: `${summary?.progress ?? 12}%` }} /></div>
        <div className="splash-steps">
          {entries.map(([key, step]) => (
            <div className="splash-step" key={key}>
              <span className={`splash-step-dot ${step.tone}`} />
              <span className="splash-step-label">{step.label}</span>
              <span className={`splash-step-detail ${step.tone}`}>{step.detail}</span>
            </div>
          ))}
        </div>
        {summary && !summary.blocked && summary.mode === 'CPU' && (
          <div className="splash-notice">未检测到可用硬件加速，当前使用 CPU Provider</div>
        )}
        {error && (
          <div className="splash-error">
            <div>{error}</div>
            {logPath && <div className="splash-log">日志：{logPath}</div>}
            <button type="button" onClick={retry} disabled={retrying}>{retrying ? '正在重试' : '重试'}</button>
          </div>
        )}
      </section>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(<SplashApp />)

void desktop
