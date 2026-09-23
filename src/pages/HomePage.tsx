import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Branch,
  Brain,
  Camera as CameraIcon,
  Database,
  MoreHorizontal,
  Play,
  Plus,
  Power,
  PreviewOpen,
  StepForward,
  Stop,
  Trash2,
  VolumeNotice,
  X,
} from '../ui-icons'
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { api, imageUrl, wsUrl } from '../api'
import { useLocation } from 'react-router-dom'
import { ModuleSidebar } from '../components/Sidebar'
import { NodeConfigDialog } from '../components/NodeConfigDialog'
import { AnnotationOverlay } from '../components/AnnotationOverlay'
import { iconAssets, iconSizes } from '../icon-assets'
import { moduleCatalog } from '../modules'
import { buildStepRows, compileWorkflow, duplicateWorkflowNode, expandBranch, insertModule, moveWorkflowNode, normalizeWorkflow, removeWorkflowNode } from '../workflow'
import { Sparkles } from '../ui-icons'
import type { Camera, FlowDetail, FlowSummary, LogItem, ModelPackage, NodeType, RunEvent, WorkflowNode } from '../types'

const defaultParams: Record<NodeType, Record<string, any>> = {
  start: {},
  capture: { source: 'file', file: { path: '' } },
  quickCapture: { source: 'camera', camera: { cameraId: '' } },
  preprocess: { mode: 'pass' },
  calibration: { mode: 'pass' },
  affine: { mode: 'pass' },
  model: { modelId: '', image: { type: 'ref', ref: '', name: '图像' } },
  branch: { conditions: [] },
  display: {
    image: { type: 'ref', ref: '', name: '图像' },
    windowId: 1,
    shapes: [],
    texts: [],
  },
  storage: { saveImage: true, saveContext: true },
  alarm: { mode: 'screen', durationMs: 800 },
  plc: { host: '', port: 502, unitId: 1, address: 0, operation: 'coil', value: { type: 'content', ref: true } },
  end: {},
}

const flowArt: Partial<Record<NodeType, keyof typeof iconAssets.flow>> = {
  capture: 'capture',
  quickCapture: 'capture',
  model: 'model',
  branch: 'branch',
  display: 'display',
  plc: 'plc',
  end: 'end',
}

export function HomePage() {
  const [flows, setFlows] = useState<FlowSummary[]>([])
  const [flow, setFlow] = useState<FlowDetail | null>(null)
  const [nodes, setNodes] = useState<WorkflowNode[]>([])
  const [logs, setLogs] = useState<LogItem[]>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [activeNodeId, setActiveNodeId] = useState<string | null>(null)
  const [configOpen, setConfigOpen] = useState(false)
  const [models, setModels] = useState<ModelPackage[]>([])
  const [cameras, setCameras] = useState<Camera[]>([])
  const [preview, setPreview] = useState<string | null>(null)
  const [annotations, setAnnotations] = useState<Array<Record<string, any>>>([])
  const [runPaused, setRunPaused] = useState(false)
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [durationMs, setDurationMs] = useState<number | null>(null)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [menuNodeId, setMenuNodeId] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [dropTarget, setDropTarget] = useState<{ id: string; place: 'before' | 'after' } | null>(null)
  const [draggingNodeId, setDraggingNodeId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [serviceConnected, setServiceConnected] = useState(false)
  const [flowMenu, setFlowMenu] = useState<string | null>(null)
  const location = useLocation()
  const flowRef = useRef(flow)
  const nodesRef = useRef(nodes)
  const runRef = useRef(activeRunId)
  const finishedRun = useRef<string | null>(null)
  flowRef.current = flow
  nodesRef.current = nodes
  runRef.current = activeRunId
  const wsRef = useRef<WebSocket | null>(null)

  const selectedNode = useMemo(() => nodes.find((node) => node.id === selectedNodeId) || null, [nodes, selectedNodeId])
  const connectedCameras = useMemo(() => cameras.filter((camera) => camera.connected), [cameras])
  const stepNodes = useMemo(() => nodes.filter((node) => node.data.nodeType !== 'start'), [nodes])
  const allStepRows = useMemo(() => buildStepRows(nodes), [nodes])
  const stepRows = useMemo(
    () => allStepRows.filter((row) => !row.ancestors.some(id => collapsed.has(id))),
    [allStepRows, collapsed],
  )

  const toggleBranch = useCallback((branchId: string) => {
    setCollapsed((current) => {
      const next = new Set(current)
      if (next.has(branchId)) next.delete(branchId)
      else next.add(branchId)
      return next
    })
  }, [])

  const pushLog = useCallback((level: string, module: string, content: string) => {
    setLogs((current) => [{ id: `${Date.now()}-${Math.random()}`, time: new Date().toLocaleTimeString('zh-CN', { hour12: false }), level, module, content }, ...current].slice(0, 100))
  }, [])

  const loadFlow = useCallback(async (flowId: string) => {
    const detail = await api.get<FlowDetail>(`/flows/${flowId}`)
    const normalized = normalizeWorkflow(detail.nodes)
    setFlow(detail)
    setNodes(normalized.map(node => ({ ...node, data: { ...node.data, outputs: [], status: 'idle', durationMs: undefined } })))
    setPreview(null)
    setAnnotations([])
    setDurationMs(null)
    setCollapsed(new Set())
    setConfigOpen(false)
    setMenuNodeId(null)
    setDropTarget(null)
    setDraggingNodeId(null)
    setSelectedNodeId(normalized.find((node) => node.data.nodeType === 'model')?.id || normalized[0]?.id || null)
  }, [])

  const refreshLists = useCallback(async (selectFirst = false) => {
    const [flowList, modelList, cameraList] = await Promise.all([
      api.get<FlowSummary[]>('/flows'),
      api.get<ModelPackage[]>('/models'),
      api.get<Camera[]>('/cameras'),
    ])
    setFlows(flowList)
    setModels(modelList)
    setCameras(cameraList)
    if (selectFirst && flowList[0]) await loadFlow(flowList[0].id)
  }, [loadFlow])

  const applyEvent = useCallback((event: RunEvent) => {
    if (event.flowId && event.flowId !== flowRef.current?.id) return
    if (event.type === 'run.paused') setRunPaused(true)
    if (event.type === 'node.started' && event.nodeId) {
      setRunPaused(false)
      setActiveNodeId(event.nodeId)
      setNodes((current) => current.map((node) => node.id === event.nodeId ? { ...node, data: { ...node.data, status: 'running' } } : node))
    }
    if (event.type === 'node.finished' && event.nodeId) {
      setNodes((current) => current.map((node) => node.id === event.nodeId ? { ...node, data: { ...node.data, status: event.status === 'failed' ? 'failed' : 'success', durationMs: event.durationMs, outputs: event.outputs ? Object.entries(event.outputs).map(([variable, value]) => ({ variable, displayName: variable, type: typeof value === 'boolean' ? 'Bool' : typeof value === 'number' ? 'Float' : variable === 'Image' ? 'Image' : variable.endsWith(':regions') ? 'List<Region>' : 'Text', value })) : node.data.outputs } } : node))
      if (event.status !== 'failed') pushLog('success', event.nodeLabel || event.nodeType || '节点', event.content || `${event.nodeLabel || event.nodeId} 执行完成，耗时 ${event.durationMs ?? 0}ms`)
    }
    if (event.type === 'log') pushLog(event.level || 'info', event.nodeLabel || event.nodeType || '系统', event.content || '')
    if (event.type === 'window.frame') {
      setPreview(imageUrl(String(event.imageUrl || '')))
      setAnnotations([])
    }
    if (event.type === 'window.overlay') setAnnotations(event.annotations || [])
    if (event.type === 'run.started') {
      setRunPaused(false)
      setActiveRunId(event.runId || null)
      setPreview(null)
      setAnnotations([])
      pushLog('info', '流程', `开始执行，runId=${event.runId}`)
    }
    if (event.type === 'run.finished') {
      finishedRun.current = event.runId || null
      setActiveRunId(null)
      setActiveNodeId(null)
      setDurationMs(Number(event.durationMs || 0))
      const failed = event.result === 'failed'
      pushLog(runResultLogLevel(event.result), '流程', `${failed ? '执行失败' : '执行完成'}，耗时 ${event.durationMs ?? 0}ms${failed && event.error ? `：${event.error}` : ''}`)
    }
    if (event.type === 'run.stopped') {
      finishedRun.current = event.runId || null
      setDurationMs(Number(event.durationMs || 0))
      setActiveRunId(null)
      setActiveNodeId(null)
      pushLog('info', '流程', `已停止，耗时 ${event.durationMs ?? 0}ms`)
    }
    if (event.type === 'alarm') pushLog('error', '声光警示器', `警示信号 ${event.mode}，持续 ${event.durationMs}ms`)
  }, [pushLog])

  useEffect(() => {
    let disposed = false
    let reconnect: ReturnType<typeof setTimeout>
    const sync = async () => {
      const active = await api.get<{ runId: string; flowId: string; status: string; mode: string; imageUrl?: string; annotations?: Array<Record<string, any>>; nodeOutputs?: Record<string, Record<string, unknown>> } | null>('/runs/active')
      if (disposed) return
      if (active?.mode === 'debug') return
      if (active) {
        setRunPaused(active.status === 'paused')
        setActiveRunId(active.runId)
        if (active.flowId === flowRef.current?.id && active.imageUrl) {
          setPreview(imageUrl(active.imageUrl))
          setAnnotations(active.annotations || [])
        }
        if (active.flowId === flowRef.current?.id && active.nodeOutputs) {
          setNodes(current => current.map(node => active.nodeOutputs?.[node.id] ? { ...node, data: { ...node.data, status: 'success', outputs: outputRows(active.nodeOutputs[node.id]) } } : node))
        }
      } else if (runRef.current) {
        const id = runRef.current
        const record = await api.get<{ result: string; duration_ms: number; status: string; flow_id: string; record: { imageUrl?: string; annotations?: Array<Record<string, any>>; nodeOutputs?: Record<string, Record<string, unknown>> } }>(`/runs/${id}`).catch(() => null)
        if (disposed) return
        setActiveRunId(null)
        setActiveNodeId(null)
        if (record && record.flow_id === flowRef.current?.id && record.record) {
          if (record.record.imageUrl) setPreview(imageUrl(record.record.imageUrl))
          setAnnotations(record.record.annotations || [])
          setNodes(current => current.map(node => record.record.nodeOutputs?.[node.id] ? { ...node, data: { ...node.data, status: 'success', outputs: outputRows(record.record.nodeOutputs[node.id]) } } : node))
        }
        if (record) applyEvent({ type: record.status === 'stopped' ? 'run.stopped' : 'run.finished', runId: id, result: record.result, durationMs: record.duration_ms })
      }
    }
    const connect = () => {
      if (disposed) return
      const ws = new WebSocket(wsUrl())
      wsRef.current = ws
      ws.onopen = () => { setServiceConnected(true); window.dispatchEvent(new CustomEvent('service-status', { detail: true })); void sync().catch(() => {}); void refreshLists(!flowRef.current).catch(error => pushLog('error', '系统', error.message)) }
      ws.onmessage = message => { try { applyEvent(JSON.parse(message.data) as RunEvent) } catch { /* 心跳 */ } }
      ws.onerror = () => ws.close()
      ws.onclose = () => { if (!disposed) { setServiceConnected(false); window.dispatchEvent(new CustomEvent('service-status', { detail: false })); reconnect = setTimeout(connect, 1500) } }
    }
    refreshLists(true).catch(error => pushLog('error', '系统', error.message))
    connect()
    const poll = setInterval(() => { if (runRef.current) void sync().catch(() => {}) }, 2000)
    const restart = () => { wsRef.current?.close(); void refreshLists(false).catch(error => pushLog('error', '系统', error.message)) }
    window.addEventListener('runtime-ready', restart)
    return () => { disposed = true; clearTimeout(reconnect); clearInterval(poll); window.removeEventListener('runtime-ready', restart); wsRef.current?.close() }
  }, [applyEvent, pushLog, refreshLists])

  useEffect(() => { if (location.pathname === '/') void refreshLists(false).catch(error => pushLog('error', '系统', error.message)) }, [location.pathname, refreshLists, pushLog])

  const persistFlow = useCallback(async (detail: FlowDetail, currentNodes: WorkflowNode[]) => {
    const clean = compileWorkflow(currentNodes).map(node => ({ ...node, data: { ...node.data, outputs: [], status: 'idle' as const, durationMs: undefined } }))
    return api.put<FlowDetail>(`/flows/${detail.id}`, { ...detail, nodes: clean })
  }, [])

  const saveFlow = useCallback(async () => {
    if (!flowRef.current) return
    const updated = await persistFlow(flowRef.current, nodesRef.current)
    setFlow(updated)
    pushLog('success', '流程', '流程已保存到 SQLite')
    return updated
  }, [persistFlow, pushLog])

  useEffect(() => {
    const save = () => { if (!runRef.current) void saveFlow().catch(error => pushLog('error', '流程', error.message)) }
    const key = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); save() } }
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (flowRef.current && JSON.stringify(compileWorkflow(nodesRef.current).map(n => ({id:n.id,params:n.data.params,label:n.data.label}))) !== JSON.stringify(compileWorkflow(flowRef.current.nodes).map(n => ({id:n.id,params:n.data.params,label:n.data.label})))) event.preventDefault()
    }
    window.addEventListener('save-flow', save); window.addEventListener('keydown', key); window.addEventListener('beforeunload', beforeUnload)
    return () => { window.removeEventListener('save-flow', save); window.removeEventListener('keydown', key); window.removeEventListener('beforeunload', beforeUnload) }
  }, [saveFlow, pushLog])

  const switchFlow = async (id: string) => {
    if (activeRunId || busy || id === flow?.id) return
    try { await saveFlow(); await loadFlow(id) } catch (error) { pushLog('error', '流程', (error as Error).message) }
  }
  const createFlow = async (copy = false) => {
    if (activeRunId || busy) return
    setBusy(true)
    try {
      await saveFlow()
      const created = await api.post<FlowDetail>('/flows', { name: copy && flow ? `${flow.name} 副本` : `流程 ${flows.length + 1}` })
      if (copy && flow) await persistFlow(created, nodes)
      await refreshLists(); await loadFlow(created.id)
    } catch (error) { pushLog('error', '流程', (error as Error).message) } finally { setBusy(false) }
  }
  const deleteFlow = async () => {
    if (!flow || activeRunId || !window.confirm(`确认删除流程「${flow.name}」？`)) return
    try { await api.delete(`/flows/${flow.id}`); setFlow(null); setNodes([]); setPreview(null); setAnnotations([]); setDurationMs(null); await refreshLists(true) } catch(error) { pushLog('error', '流程', (error as Error).message) }
  }
  const reorderFlow = async (direction: -1 | 1) => {
    const index = flows.findIndex(item => item.id === flow?.id), target = index + direction
    if (activeRunId || index < 0 || target < 0 || target >= flows.length) return
    const list = [...flows]; [list[index], list[target]] = [list[target], list[index]]
    try { await api.put('/flows/order', { ids: list.map(item => item.id) }); setFlows(list) } catch(error) { pushLog('error', '流程', (error as Error).message) }
  }
  const renameFlow = async () => {
    if (!flow || activeRunId) return
    const name = window.prompt('流程名称', flow.name)?.trim()
    if (!name) return
    try { const updated = await persistFlow({ ...flow, name }, nodes); setFlow(updated); await refreshLists() } catch(error) { pushLog('error', '流程', (error as Error).message) }
    setFlowMenu(null)
  }
  const runFlow = async (mode: 'run' | 'step') => {
    if (!flow || activeRunId || busy) return
    setBusy(true)
    try {
      await saveFlow()
      setNodes(current => current.map(node => ({ ...node, data: { ...node.data, status: 'idle', outputs: [], durationMs: undefined } })))
      setPreview(null); setAnnotations([]); setDurationMs(null)
      const result = await api.post<{ runId: string }>(`/flows/${flow.id}/runs`, { mode })
      if (finishedRun.current !== result.runId) setActiveRunId(result.runId)
    } catch (error) { pushLog('error', '流程', (error as Error).message) } finally { setBusy(false) }
  }

  const stepFlow = async () => {
    if (!activeRunId) return runFlow('step')
    if (busy || !runPaused) return
    setBusy(true)
    try {
      setRunPaused(false)
      await api.post(`/runs/${activeRunId}/step`)
    } catch (error) {
      pushLog('error', '流程', (error as Error).message)
    } finally { setBusy(false) }
  }

  const stopFlow = async () => {
    if (!activeRunId) return
    try {
      await api.post(`/runs/${activeRunId}/stop`)
    } catch (error) {
      pushLog('error', '流程', (error as Error).message)
    }
  }

  const addModule = useCallback((type: NodeType, targetId?: string, place: 'before' | 'after' = 'after') => {
    const catalog = moduleCatalog.find((item) => item.type === type)
    if (!catalog || !flowRef.current || runRef.current) return
    const id = `${type}-${crypto.randomUUID()}`
    const node: WorkflowNode = {
      id, type: 'workflow', position: { x: 0, y: 0 },
      data: { label: catalog.label, nodeType: type, params: structuredClone(defaultParams[type]), status: 'idle', outputs: [] },
    }
    setNodes((current) => insertModule(current, node, targetId, place))
    setCollapsed(new Set())
    setSelectedNodeId(id)
    setMenuNodeId(null)
    setConfigOpen(false)
  }, [])

  const removeNode = useCallback((nodeId: string) => {
    if (runRef.current) return
    setNodes((current) => removeWorkflowNode(current, nodeId))
    setSelectedNodeId(null)
    setMenuNodeId(null)
    setConfigOpen(false)
  }, [])

  const duplicateNode = useCallback((nodeId: string) => {
    if (runRef.current) return
    setNodes((current) => duplicateWorkflowNode(current, nodeId))
    setMenuNodeId(null)
  }, [])

  const openNode = (node: WorkflowNode) => {
    if (activeRunId || busy) return
    setSelectedNodeId(node.id)
    setMenuNodeId(null)
    if (node.data.branch?.role === 'end' || node.data.branch?.role === 'else') {
      setNodes((current) => expandBranch(current, node.id))
      setConfigOpen(false)
      return
    }
    setConfigOpen(true)
  }

  const deleteSelected = () => {
    if (!selectedNodeId) return
    removeNode(selectedNodeId)
    setConfigOpen(false)
  }

  const updateSelectedParams = (patch: Record<string, any>) => {
    if (!selectedNodeId) return
    setNodes((current) => compileWorkflow(current.map((node) => node.id === selectedNodeId ? { ...node, data: { ...node.data, params: { ...node.data.params, ...patch } } } : node)))
  }

  const resetDragState = useCallback(() => {
    setDragOver(false)
    setDropTarget(null)
    setDraggingNodeId(null)
  }, [])

  return (
    <div className="main-layout">
      <section className="left-rail">
        <div className="flow-list-panel panel">
          <div className="panel-header compact">
            <span>流程列表</span>
            <div><button><ChevronUp size={12} /></button><button><X size={12} /></button></div>
          </div>
          <div className="flow-toolbar">
            <button title="新增流程" disabled={!!activeRunId || busy} onClick={() => createFlow()}><Plus size={13} /></button>
            <button title="复制流程" disabled={!flow || !!activeRunId || busy} onClick={() => createFlow(true)}><Copy size={13} /></button>
            <button title="上移流程" disabled={!!activeRunId} onClick={() => reorderFlow(-1)}><ChevronUp size={13} /></button>
            <button title="下移流程" disabled={!!activeRunId} onClick={() => reorderFlow(1)}><ChevronDown size={13} /></button>
            <button title="删除流程" disabled={!flow || !!activeRunId} onClick={deleteFlow}><Trash2 size={13} /></button>
          </div>
          <div className="flow-list-body">
            {flows.map((item, index) => (
              <button className={`flow-row ${item.id === flow?.id ? 'active' : ''}`} key={item.id} disabled={!!activeRunId} onDoubleClick={renameFlow} onClick={() => switchFlow(item.id)}>
                <span className="flow-bullet" />
                <span>{item.name || `流程 ${index + 1}`}</span>
                <span title="流程操作" onClick={async event => { event.stopPropagation(); await switchFlow(item.id); setFlowMenu(flowMenu === item.id ? null : item.id) }}><MoreHorizontal size={13} /></span>
              </button>
            ))}
            {flowMenu && <div className="flow-actions-menu"><button onClick={renameFlow}>重命名</button><button onClick={() => createFlow(true)}>复制</button><button onClick={deleteFlow}>删除</button></div>}
            {flows.length === 0 && <div className="step-empty">点击 + 新建流程</div>}
          </div>
        </div>
        <ModuleSidebar onAdd={addModule} />
      </section>

      <section className="step-panel panel">
        <div className="panel-header compact">
          <span>流程栏</span>
          <div><button><X size={16} /></button></div>
        </div>
        <div className="step-toolbar">
          <button className="primary" disabled={!flow || !!activeRunId || busy || !serviceConnected} onClick={() => runFlow('run')} title="运行"><Play size={16} /></button>
          <button disabled={!flow || busy || !serviceConnected || (!!activeRunId && !runPaused)} onClick={stepFlow} title="单步"><StepForward size={16} /></button>
          <button disabled={!!activeRunId || busy} onClick={deleteSelected} title="删除节点"><Trash2 size={16} /></button>
          <button onClick={stopFlow} disabled={!activeRunId} title="停止"><Stop size={16} /></button>
        </div>
        <div
          className={`step-list ${dragOver ? 'drop-active' : ''} ${dropTarget ? 'has-drop-target' : ''}`}
          onDragOver={(event) => {
            if (activeRunId || !flow) return
            const isModule = event.dataTransfer.types.includes('application/x-digivision-node')
            const isStep = event.dataTransfer.types.includes(STEP_DRAG_MIME)
            if (!isModule && !isStep) return
            event.preventDefault()
            event.dataTransfer.dropEffect = isStep ? 'move' : 'copy'
            setDragOver(true)
          }}
          onDragLeave={(event) => {
            if (event.currentTarget.contains(event.relatedTarget as Node)) return
            resetDragState()
          }}
          onDrop={(event) => {
            if (activeRunId || !flow) return
            event.preventDefault()
            const sourceId = event.dataTransfer.getData(STEP_DRAG_MIME)
            const type = event.dataTransfer.getData('application/x-digivision-node') as NodeType
            if (sourceId) setNodes((current) => moveWorkflowNode(current, sourceId, null, 'after'))
            else if (moduleCatalog.some(item => item.type === type)) addModule(type)
            resetDragState()
          }}
          onClick={() => setMenuNodeId(null)}
        >
          {stepRows.map((row) => {
            const node = row.node
            const current = node.id === selectedNodeId
            const role = node.data.branch?.role
            return (
              <div
                className={`step-row ${row.level > 0 ? 'child' : ''} ${current ? 'selected' : ''} ${node.id === activeNodeId ? 'running' : ''} ${menuNodeId === node.id ? 'menu-open' : ''} ${draggingNodeId === node.id ? 'dragging' : ''} ${dropTarget?.id === node.id ? `drop-${dropTarget.place}` : ''} ${node.data.status || 'idle'}`}
                style={{ paddingLeft: row.level * 21.5 }}
                data-node-id={node.id}
                data-branch-role={role}
                key={row.key}
                draggable={!activeRunId && !busy}
                onDragStart={(event) => {
                  if ((event.target as HTMLElement).closest('button')) {
                    event.preventDefault()
                    return
                  }
                  event.stopPropagation()
                  event.dataTransfer.setData(STEP_DRAG_MIME, node.id)
                  event.dataTransfer.setData('text/plain', node.id)
                  event.dataTransfer.effectAllowed = 'move'
                  setDraggingNodeId(node.id)
                  setDropTarget(null)
                  setMenuNodeId(null)
                }}
                onDragEnd={resetDragState}
                onDragOver={(event) => {
                  if (activeRunId || !flow) return
            const isModule = event.dataTransfer.types.includes('application/x-digivision-node')
                  const isStep = event.dataTransfer.types.includes(STEP_DRAG_MIME)
                  if (!isModule && !isStep) return
                  event.preventDefault()
                  event.stopPropagation()
                  event.dataTransfer.dropEffect = isStep ? 'move' : 'copy'
                  setDragOver(true)
                  setDropTarget({ id: node.id, place: dropPlace(event) })
                }}
                onDragLeave={(event) => {
                  if (event.currentTarget.contains(event.relatedTarget as Node)) return
                  setDropTarget((current) => current?.id === node.id ? null : current)
                }}
                onClick={(event) => { event.stopPropagation(); setSelectedNodeId(node.id); setMenuNodeId(null) }}
                onDoubleClick={() => openNode(node)}
                onDrop={(event) => {
                  if (activeRunId || !flow) return
                  event.preventDefault()
                  event.stopPropagation()
                  const sourceId = event.dataTransfer.getData(STEP_DRAG_MIME)
                  const type = event.dataTransfer.getData('application/x-digivision-node') as NodeType
                  const place = dropPlace(event)
                  if (sourceId) setNodes((current) => moveWorkflowNode(current, sourceId, node.id, place))
                  else if (moduleCatalog.some(item => item.type === type)) addModule(type, node.id, place)
                  resetDragState()
                }}
                title={role === 'end' ? '双击添加否则' : role === 'else' ? '双击添加否则如果' : undefined}
              >
                <span className="step-index">{row.index}</span>
                {role === 'end' ? (
                  <span className="step-collapse end-node-icon" title="条件分支结束">
                    <img src={iconAssets.flow.end} alt="" {...iconSizes.flow} />
                  </span>
                ) : role ? (
                  <button
                    className="step-collapse"
                    aria-expanded={!collapsed.has(node.id)}
                    title={collapsed.has(node.id) ? '展开分支' : '收起分支'}
                    onDoubleClick={(event) => event.stopPropagation()}
                    onClick={(event) => { event.stopPropagation(); setMenuNodeId(null); toggleBranch(node.id) }}
                  >
                    <img src={iconAssets.flow.collapse} alt="" {...iconSizes.flowCollapse} />
                  </button>
                ) : null}
                {role === 'end' ? null : <span className="step-icon">{stepIcon(node.data.nodeType)}</span>}
                <span className="step-label">{row.label}</span>
                <span className="step-state">{node.data.status === 'success' ? <Check size={14} /> : node.data.status === 'failed' ? <X size={14} /> : null}</span>
                <button
                  className="step-more"
                  title="节点操作"
                  onDoubleClick={(event) => event.stopPropagation()}
                  onClick={(event) => { event.stopPropagation(); setMenuNodeId((current) => current === node.id ? null : node.id) }}
                >
                  <MoreHorizontal size={14} />
                </button>
                {menuNodeId === node.id ? (
                  <span className="step-menu" onClick={(event) => event.stopPropagation()} onDoubleClick={(event) => event.stopPropagation()}>
                    <button disabled={node.data.nodeType === 'end'} onClick={() => duplicateNode(node.id)}>复制</button>
                    <button onClick={() => removeNode(node.id)}>删除</button>
                  </span>
                ) : null}
              </div>
            )
          })}
          {stepNodes.length === 0 && <div className="step-empty">拖入或双击左侧模块添加节点</div>}
        </div>
      </section>

      <section className="image-panel panel">
        <div className="panel-header compact">
          <span>图像窗口</span>
          <div><button><ChevronUp size={12} /></button><button><X size={12} /></button></div>
        </div>
        <div className="image-grid single-view">
          <ImageTile className="tile-single" image={preview} annotations={annotations} />
        </div>
        <div className="image-footer"><span>流程总耗时</span><b>{durationMs === null ? '—' : `${durationMs} ms`}</b></div>
      </section>

      <aside className="output-panel panel">
        <div className="panel-header compact">
          <span>模块输出</span>
          <div><button><ChevronUp size={12} /></button><button><X size={12} /></button></div>
        </div>
        <OutputRows node={selectedNode} />
      </aside>

      <section className="log-panel panel">
        <div className="panel-header compact">
          <span>日志栏</span>
          <div><button><ChevronUp size={12} /></button><button><X size={12} /></button></div>
        </div>
        <div className="log-table">
          <div className="log-head"><span>时间</span><span>类型</span><span>模块</span><span>内容</span></div>
          <div className="log-body">
            {logs.length === 0 && <div className="empty-row">暂无日志，执行流程后在这里查看节点输出。</div>}
            {logs.map((log) => (
              <div className="log-row" key={log.id}>
                <span>{log.time}</span>
                <span className={`level-${log.level === "success" ? "info" : log.level}`}>{log.level === "success" ? "INFO" : log.level.toUpperCase()}</span>
                <span>{log.module}</span>
                <span>{log.content}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {configOpen && selectedNode && (
        <NodeConfigDialog
          key={selectedNode.id}
          node={selectedNode}
          nodes={nodes}
          models={models}
          cameras={connectedCameras}
          preview={null}
          onSave={async (params, label) => {
            if (!flow) return
            const next = compileWorkflow(nodes.map(node => node.id === selectedNode.id ? { ...node, data: { ...node.data, params, label } } : node))
            const updated = await persistFlow(flow, next)
            setFlow(updated); setNodes(next); pushLog('success', '节点', '节点配置已保存')
          }}
          onDebug={async params => {
            await saveFlow()
            const result = await api.post<{ imageUrl?: string }>(`/flows/${flow!.id}/nodes/${selectedNode.id}/debug`, { params })
            return { ...result, imageUrl: result.imageUrl ? imageUrl(result.imageUrl) : undefined }
          }}
          onClose={() => setConfigOpen(false)}
          onPatch={updateSelectedParams}
          onLabel={(label) => setNodes((current) => current.map((node) => node.id === selectedNode.id ? { ...node, data: { ...node.data, label } } : node))}
        />
      )}
    </div>
  )
}

function runResultLogLevel(result: unknown) {
  return result === 'failed' ? 'error' : 'success'
}

function ImageTile({ className, image, annotations = [] }: { className: string; image: string | null; annotations?: Array<Record<string, any>> }) {
  const [size, setSize] = useState({ width: 1, height: 1 })
  return <div className={`image-tile ${className}`}>
    {image && <img src={image} alt="检测图像" onLoad={event => setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} />}
    {image && <AnnotationOverlay width={size.width} height={size.height} annotations={annotations} />}
  </div>
}

function OutputRows({ node }: { node: WorkflowNode | null }) {
  const rows = node?.data.outputs || []
  return <div className="output-rows">
    <div className="output-head"><span>输出名称</span><span>输出值</span></div>
    {rows.map((row, index) => <div className="output-row" key={`${row.variable}-${index}`}><span>{row.displayName || row.variable}</span><b>{formatValue(row.value)}</b></div>)}
    {!rows.length && <div className="step-empty">{node ? '该节点暂无运行输出' : '请选择节点'}</div>}
  </div>
}

function stepIcon(type: NodeType) {
  const art = flowArt[type]
  if (art) return <img src={iconAssets.flow[art]} alt="" {...iconSizes.flow} />
  return {
    start: <Play size={13} />,
    capture: <CameraIcon size={13} />,
    quickCapture: <CameraIcon size={13} />,
    preprocess: <Sparkles size={13} />,
    calibration: <MoreHorizontal size={13} />,
    affine: <MoreHorizontal size={13} />,
    model: <Brain size={13} />,
    branch: <Branch size={13} />,
    display: <PreviewOpen size={13} />,
    storage: <Database size={13} />,
    alarm: <VolumeNotice size={13} />,
    plc: <Power size={13} />,
    end: <Power size={13} />,
  }[type]
}

function formatValue(value: unknown) {
  if (value == null) return '-'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const STEP_DRAG_MIME = 'application/x-digivision-step'

function dropPlace(event: DragEvent<HTMLElement>): 'before' | 'after' {
  const rect = event.currentTarget.getBoundingClientRect()
  return event.clientY < rect.top + rect.height / 2 ? 'before' : 'after'
}

function outputRows(outputs: Record<string, unknown>) {
  return Object.entries(outputs).map(([variable, value]) => ({ variable, displayName: variable, type: typeof value === 'boolean' ? 'Bool' : typeof value === 'number' ? 'Int' : variable === 'Image' ? 'Image' : variable.endsWith(':regions') ? 'List<Region>' : 'Text', value }))
}
