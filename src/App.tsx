import { useEffect, useLayoutEffect } from 'react'
import { desktop } from './api'
import { HashRouter as BrowserRouter, Route, Routes, useLocation } from 'react-router-dom'
import './App.css'
import './runtime-ui.css'
import { AppToolbar, TopBar } from './components/TopBar'
import { CameraPage } from './pages/CameraPage'
import { HomePage } from './pages/HomePage'
import { ModelPage } from './pages/ModelPage'
import { PlaceholderPage } from './pages/PlaceholderPage'

function App() {
  // 控件按高保等比缩放，多出的窗口空间交给图像区，舞台始终贴合窗口。
  useLayoutEffect(() => {
    const fit = () => {
      // 设计稿以 1920×1080 为基准；窗口较小时保留可读的控件比例，再按缩放后的舞台尺寸铺满窗口。
      const scale = Math.min(1, Math.max(0.85, window.innerWidth / 1920, window.innerHeight / 1080))
      const style = document.documentElement.style
      style.setProperty('--stage-scale', String(scale))
      style.setProperty('--stage-width', `${window.innerWidth / scale}px`)
      style.setProperty('--stage-height', `${window.innerHeight / scale}px`)
    }
    fit()
    const raf = requestAnimationFrame(fit)
    window.addEventListener('resize', fit)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', fit)
    }
  }, [])

  useEffect(() => {
    if (!desktop) return
    const contextMenu = (event: MouseEvent) => {
      if (!(event.target instanceof Element) || !event.target.closest('input, textarea, [contenteditable="true"], [contenteditable=""]')) event.preventDefault()
    }
    const zoomKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && ['+', '-', '=', '0'].includes(event.key)) event.preventDefault()
    }
    const zoomWheel = (event: WheelEvent) => { if (event.ctrlKey) event.preventDefault() }
    const gesture = (event: Event) => event.preventDefault()
    document.addEventListener('contextmenu', contextMenu)
    document.addEventListener('keydown', zoomKey)
    document.addEventListener('wheel', zoomWheel, { passive: false })
    document.addEventListener('gesturestart', gesture, { passive: false })
    return () => {
      document.removeEventListener('contextmenu', contextMenu)
      document.removeEventListener('keydown', zoomKey)
      document.removeEventListener('wheel', zoomWheel)
      document.removeEventListener('gesturestart', gesture)
    }
  }, [])

  return <BrowserRouter><AppContent /></BrowserRouter>
}

function AppContent() {
  const location = useLocation()
  const management = location.pathname === '/cameras' || location.pathname === '/models'
  return (
    <div className="stage-fit" spellCheck={false}>
      <div className={`app-shell ${management ? 'management-open' : ''}`}>
        <TopBar />
        <AppToolbar />
        <div style={{ display: ['/', '/cameras', '/models'].includes(location.pathname) ? 'contents' : 'none' }}><HomePage /></div>
        <Routes>
          <Route path="/" element={null} />
          <Route path="/cameras" element={null} />
          <Route path="/models" element={null} />
          <Route path="/plc" element={<PlaceholderPage title="PLC 管理" />} />
          <Route path="/help" element={<PlaceholderPage title="帮助" />} />
          <Route path="/settings" element={<PlaceholderPage title="设置" />} />
          <Route path="*" element={<PlaceholderPage title="功能建设中" />} />
        </Routes>
        {location.pathname === '/cameras' && <CameraPage />}
        {location.pathname === '/models' && <ModelPage />}
      </div>
    </div>
  )
}

export default App
