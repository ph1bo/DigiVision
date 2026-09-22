import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { backendLogPath, desktop, finishSplash, initializeRuntime, inspectRuntime, restartBackend } from './api'
import { summarizeCapabilities, type SplashSummary } from './splash-logic'
import splashLogo from './assets/mastergo/splash-logo.svg'
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

  const progress = summary?.progress ?? (retrying ? 12 : 8)
  const statusText = error
    ? '运行环境检查失败'
    : retrying
      ? '正在初始化视觉引擎...'
      : '正在准备运行环境...'
  const modeText = summary?.mode && summary.mode !== 'CPU' ? summary.mode : 'AI 推理引擎 · 就绪'
  return (
    <main className="splash-shell" data-tauri-drag-region>
      <section className="splash-brand-panel">
        <header className="splash-header">
          <img className="splash-logo" src={splashLogo} alt="" />
          <div className="splash-brand-lockup">
            <div className="splash-brand">Digi Vision</div>
            <div className="splash-caption">AI工业视觉检测平台</div>
          </div>
        </header>

        <div className="splash-wordmark">
          <h1>DIGI<br /><span>VISION</span></h1>
          <p className="splash-tagline">让机器看懂世界 · 让数据驱动未来</p>
          <p className="splash-motto">AI Vision for a Digital World</p>
          <div className="splash-feature-list" aria-label="功能模块">
            <span><b className="feature-icon feature-defect" />缺陷检测</span>
            <span><b className="feature-icon feature-target" />目标定位</span>
            <span><b className="feature-icon feature-analysis" />智能分析</span>
          </div>
        </div>

        <footer className="splash-progress-area">
          <div className="splash-progress-label">
            <span className={`splash-loader ${error ? 'is-error' : ''}`} />
            <strong>{statusText}</strong>
            <b>{progress}%</b>
          </div>
          <div className="splash-progress-track"><span style={{ width: `${progress}%` }} /></div>
          <div className="splash-meta-row">
            <span>© 2025 Digi Vision Technology Co., Ltd.</span>
            <span>v3.2.0&nbsp; · &nbsp;Build 20250812</span>
          </div>
          {error && (
            <div className="splash-error">
              <div>{error}</div>
              {logPath && <div className="splash-log">日志：{logPath}</div>}
              <button type="button" onClick={retry} disabled={retrying}>{retrying ? '正在重试' : '重试'}</button>
            </div>
          )}
        </footer>
      </section>

      <section className="splash-visual-panel" aria-label="视觉引擎状态">
        <div className="splash-visual-topbar">
          <span className="online-status"><i />SYSTEM ONLINE</span>
          <span>DIGI-VISION CORE 3.2</span>
        </div>
        <div className="splash-visual-stage">
          <div className="visual-grid" />
          <i className="visual-corner corner-tl" /><i className="visual-corner corner-tr" />
          <i className="visual-corner corner-bl" /><i className="visual-corner corner-br" />
          <div className="radar-rings"><i /><i /><i /><i /></div>
          <div className="radar-orbit orbit-wide" /><div className="radar-orbit orbit-narrow" />
          <div className="radar-core"><i /><b /></div>
          <div className="scan-beam beam-left" /><div className="scan-beam beam-right" />
          <i className="visual-diamond diamond-a" /><i className="visual-diamond diamond-b" />
          <i className="visual-diamond diamond-c" /><i className="visual-diamond diamond-d" />
          <div className="inspection-plane">
            <div className="plane-grid" />
            <i className="inspection-block block-a" /><i className="inspection-block block-b" /><i className="inspection-block block-c" />
          </div>
        </div>
        <div className="splash-visual-bottombar">
          <span><i className="engine-dot" />{modeText}</span>
          <span><i className="accuracy-icon" />识别精度 98.6% 推理时延 12ms</span>
        </div>
      </section>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(<SplashApp />)

void desktop
