import { useEffect, useState } from 'react'
import { iconAssets, iconSizes } from '../icon-assets'
import { Close, Minus, Square } from '../ui-icons'
import { NavLink, useNavigate } from 'react-router-dom'
import { desktop, windowAction } from '../api'

export function TopBar() {
  const [connected, setConnected] = useState(false)
  const [menu, setMenu] = useState<string | null>(null)
  const navigate = useNavigate()
  useEffect(() => {
    const status = (event: Event) => setConnected((event as CustomEvent<boolean>).detail)
    window.addEventListener('service-status', status)
    return () => window.removeEventListener('service-status', status)
  }, [])
  const action = (path: string) => { navigate(path); setMenu(null) }
  return <header className="topbar" data-tauri-drag-region="deep">
    <div className="brand" data-tauri-drag-region="deep"><img className="brand-logo" src={iconAssets.logo} alt="Digi Vision" {...iconSizes.logo} /><span data-tauri-drag-region>Digi Vision</span></div>
    <nav className="app-menu" data-tauri-drag-region="false">
      {['文件', '视图', '设置', '管理', '帮助'].map(item => <div className="app-menu-item" key={item}>
        <button onClick={() => setMenu(menu === item ? null : item)}>{item}</button>
        {menu === item && <div className="app-menu-dropdown">
          {item === '文件' && <button onClick={() => { window.dispatchEvent(new Event('save-flow')); setMenu(null) }}>保存流程　⌘/Ctrl+S</button>}
          {item === '视图' && <button onClick={() => action('/')}>工作台</button>}
          {item === '设置' && <button onClick={() => action('/settings')}>设置</button>}
          {item === '管理' && <><button onClick={() => action('/cameras')}>相机管理</button><button onClick={() => action('/models')}>模型管理</button></>}
          {item === '帮助' && <button onClick={() => action('/help')}>使用说明</button>}
        </div>}
      </div>)}
    </nav>
    <div className="window-drag-area" data-tauri-drag-region title={desktop ? '拖动窗口，双击最大化/还原' : undefined} />
    <div className="top-actions" data-tauri-drag-region="false"><span className="connection-dot" style={{ background: connected ? '#22c55e' : '#999' }} /><span>{connected ? '本地服务已连接' : '连接中…'}</span><span className="window-divider" />
      <button disabled={!desktop} title="最小化" onClick={() => windowAction('window_minimize')}><Minus size={14} /></button>
      <button disabled={!desktop} title="最大化/还原" onClick={() => windowAction('window_toggle_maximize')}><Square size={12} /></button>
      <button disabled={!desktop} title="关闭" onClick={() => windowAction('window_close')}><Close size={14} /></button>
    </div>
  </header>
}
const toolbarItems = [
  { key: 'layout', title: '工作台', to: '/' }, { key: 'camera', title: '相机管理', to: '/cameras' },
  { key: 'model', title: '模型管理', to: '/models' }, { key: 'plc', title: 'PLC 管理', to: '/plc' },
  { key: 'help', title: '帮助', to: '/help' }, { key: 'setting', title: '设置', to: '/settings' },
] as const
export function AppToolbar() { return <div className="app-toolbar"><img className="toolbar-divider-img" src={iconAssets.toolbar.divider} alt="" {...iconSizes.toolbarDivider} />{toolbarItems.map(item => <span className="toolbar-cell" key={item.key}><NavLink to={item.to} end={item.to === '/'} title={item.title}><img src={iconAssets.toolbar[item.key]} alt={item.title} {...iconSizes.toolbar} /></NavLink></span>)}</div> }
