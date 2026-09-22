import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@icon-park/react/styles/index.css'
import './index.css'
import App from './App.tsx'
import { desktop, initializeRuntime } from './api'

const root = createRoot(document.getElementById('root')!)
async function boot() {
try {
  await initializeRuntime()
  await desktop?.event?.listen('python-restarted', async () => { await initializeRuntime(); window.dispatchEvent(new Event('runtime-ready')) })
  root.render(
  <StrictMode>
    <App />
  </StrictMode>,
)

} catch (error) { root.render(<div style={{ padding: 32, color: '#fff' }}>本地服务启动失败：{String(error)}<button onClick={() => location.reload()}>重试</button></div>) }
}
void boot()
