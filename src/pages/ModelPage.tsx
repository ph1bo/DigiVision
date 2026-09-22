import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import chevronDownIcon from '../assets/mastergo/chevron-down.svg'
import closeIcon from '../assets/mastergo/close.svg'
import modelManageIcon from '../assets/mastergo/model-manage.svg'
import trashIcon from '../assets/mastergo/trash-model.svg'
import type { ModelPackage } from '../types'

export function ModelPage() {
  const navigate = useNavigate()
  const [models, setModels] = useState<ModelPackage[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [message, setMessage] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const selected = useMemo(() => models.find((model) => model.id === selectedId) || models[0], [models, selectedId])

  const refresh = useCallback(async () => {
    const list = await api.get<ModelPackage[]>('/models')
    setModels(list)
    setSelectedId((current) => list.some((model) => model.id === current) ? current : list[0]?.id || '')
  }, [])

  useEffect(() => {
    refresh().catch((error) => setMessage((error as Error).message))
  }, [refresh])

  const importModel = async (file?: File | null) => {
    if (!file) return
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const imported = await api.post<ModelPackage>('/models/import', form)
      await refresh()
      setSelectedId(imported.id)
      setMessage(`已导入 ${imported.name} ${imported.version}`)
    } catch (error) {
      setMessage((error as Error).message)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const toggleModel = async (model: ModelPackage) => {
    setPending(model.id)
    try {
      const running = model.serviceStatus === 'running'
      await api.post(`/models/${model.id}/${running ? 'stop' : 'start'}`)
      await refresh()
      setSelectedId(model.id)
    } catch (error) {
      setMessage((error as Error).message)
      await refresh().catch(() => {})
    } finally { setPending(null) }
  }

  const removeModel = async (model: ModelPackage) => {
    if (!window.confirm(`确认删除模型 ${model.name}？`)) return
    try {
      await api.delete(`/models/${model.id}`)
      await refresh()
    } catch (error) {
      setMessage((error as Error).message)
    }
  }

  const tasks = selected?.manifest.tasks || []

  return (
    <div className="management-overlay">
      <section className="management-dialog management-model-dialog" aria-label="模型管理">
        <header className="management-dialog-header">
          <div className="management-dialog-title">
            <img src={modelManageIcon} alt="" />
            <span>模型管理</span>
          </div>
          <button className="management-dialog-close" onClick={() => navigate('/')} aria-label="关闭">
            <img src={closeIcon} alt="" />
          </button>
        </header>

        <div className="management-model-main">
          <section className="model-list-section">
            <div className="model-list-heading">
              <h3>模型列表</h3>
              <input ref={fileRef} hidden type="file" accept=".dvmodel,application/zip" onChange={(event) => importModel(event.target.files?.[0])} />
              <button onClick={() => fileRef.current?.click()} disabled={uploading}>新增</button>
            </div>
            <div className="mg-table model-management-table">
              <div className="mg-table-head">
                <span>序号</span><span>模型名称</span><span>状态</span><span>版本号</span><span>导入时间</span><span>操作</span>
              </div>
              <div className="mg-table-body">
                {models.map((model, index) => {
                  const running = model.serviceStatus === 'running'
                  return (
                    <div
                      className={`mg-table-row ${selected?.id === model.id ? 'selected' : ''}`}
                      key={model.id}
                      onClick={() => setSelectedId(model.id)}
                    >
                      <span className="model-seq">{index + 1}</span>
                      <span>{model.name}</span>
                      <span className="model-switch-cell">
                        <button
                          disabled={!!pending || uploading}
                          title={model.lastError || (running ? '运行中' : model.serviceStatus === 'error' ? '启动失败' : '未启动')}
                          className={`mg-switch ${running ? 'on' : ''}`}
                          onClick={(event) => { event.stopPropagation(); toggleModel(model) }}
                          aria-label={running ? '停止模型' : '启动模型'}
                        >
                          <i />
                        </button>
                      </span>
                      <span>{model.version}</span>
                      <span>{formatTime(model.imported_at)}</span>
                      <span className="mg-row-actions">
                        <button disabled={!!pending || running} title="删除" onClick={(event) => { event.stopPropagation(); removeModel(model) }}>
                          <img src={trashIcon} alt="" />
                        </button>
                      </span>
                    </div>
                  )
                })}
                {models.length === 0 && <div className="management-empty-row">暂无模型</div>}
              </div>
            </div>
          </section>

          <aside className="model-info-panel">
            <h3>模型信息</h3>
            <div className="model-info-tree">
              {tasks.map((task) => (
                <div className="model-tree-group" key={task.key}>
                  <div className={`model-tree-row ${collapsed.has(task.key) ? 'collapsed' : 'expanded'}`} role="button" tabIndex={0} onClick={() => setCollapsed(current => { const next = new Set(current); if (next.has(task.key)) next.delete(task.key); else next.add(task.key); return next })}>
                    <img src={chevronDownIcon} alt="" />
                    <span>{task.name}</span>
                  </div>
                  {!collapsed.has(task.key) && task.labels.map((label) => (
                    <div className="model-tree-row model-tree-child" key={`${task.key}-${label.label}`}>
                      <span>{label.name}</span>
                    </div>
                  ))}
                </div>
              ))}
              {tasks.length === 0 && <div className="model-info-empty">暂无模型信息</div>}
            </div>
          </aside>
        </div>

        <footer className="management-dialog-footer model-dialog-footer">
          <button onClick={() => navigate('/')}>关闭</button>
        </footer>

        {message && <button className="management-toast" onClick={() => setMessage('')}>{message}</button>}
      </section>
    </div>
  )
}

function formatTime(value: string) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-') : '-'
}
