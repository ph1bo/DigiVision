import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import variableCloseIcon from '../assets/mastergo/variable-close.svg'
import './variable-reference.css'
import { api } from '../api'
import closeIcon from '../assets/mastergo/close.svg'
import captureCameraIcon from '../assets/mastergo/capture-camera.svg'
import captureDeleteIcon from '../assets/mastergo/capture-delete.svg'
import captureFlipHorizontalIcon from '../assets/mastergo/capture-flip-horizontal.svg'
import captureFlipVerticalIcon from '../assets/mastergo/capture-flip-vertical.svg'
import captureFolderIcon from '../assets/mastergo/capture-folder.svg'
import captureImageIcon from '../assets/mastergo/capture-image.svg'
import captureMoveUpIcon from '../assets/mastergo/capture-move-up.svg'
import captureRadioCheckedIcon from '../assets/mastergo/capture-radio-checked.svg'
import captureRadioUncheckedIcon from '../assets/mastergo/capture-radio-unchecked.svg'
import captureRotate180Icon from '../assets/mastergo/capture-rotate-180.svg'
import captureRotateLeftIcon from '../assets/mastergo/capture-rotate-left.svg'
import captureRotateRightIcon from '../assets/mastergo/capture-rotate-right.svg'
import captureSortIcon from '../assets/mastergo/capture-sort.svg'
import linkIcon from '../assets/mastergo/link.svg'
import displayAddIcon from '../assets/mastergo/display-add.svg'
import displayRemoveIcon from '../assets/mastergo/display-remove.svg'
import displayCloseIcon from '../assets/mastergo/display-close.svg'
import displayLinkIcon from '../assets/mastergo/display-link.svg'
import displayChevronDownIcon from '../assets/mastergo/display-chevron-down.svg'
import displayChevronUpIcon from '../assets/mastergo/display-chevron-up.svg'
import { iconAssets, iconSizes } from '../icon-assets'
import { AnnotationOverlay } from './AnnotationOverlay'
import { DesignSelect } from './DesignSelect'
import type { Camera, ModelPackage, NodeType, WorkflowNode } from '../types'
import './node-config.css'

type Params = Record<string, any>
type ValueKind = 'Region' | 'Image' | 'ImageArray' | 'Text' | 'Bool' | 'Int' | 'Float'
type ValueObject = { type: 'content' | 'ref'; ref: string | number | boolean; name?: string }
interface VariableOption { kind: ValueKind; ref: string; label: string; nodeId: string; nodeName: string; name: string; value?: unknown }

export interface NodeDebugResult {
  imageUrl?: string
  annotations?: Array<Record<string, any>>
  outputs?: Record<string, unknown>
  status?: string
  durationMs?: number
}

export interface NodeConfigDialogProps {
  node: WorkflowNode
  nodes: WorkflowNode[]
  models: ModelPackage[]
  cameras: Camera[]
  preview: string | null
  onClose: () => void
  onPatch: (patch: Params) => void
  onLabel: (label: string) => void
  /** 成功后关闭；拒绝时保留草稿。提供此接口后不再调用 onPatch/onLabel。 */
  onSave?: (params: Params, label: string) => Promise<void>
  /** 只调试传入快照，不保存草稿。imageUrl 必须可被浏览器直接加载。 */
  onDebug?: (params: Params) => Promise<NodeDebugResult>
}

const literal = (ref: string | number | boolean): ValueObject => ({ type: 'content', ref })
const valueObject = (value: any, fallback: string | number | boolean | null = null): ValueObject | null =>
  value && typeof value === 'object' && ['ref', 'content'].includes(value.type) ? value
    : value != null ? literal(value) : fallback == null ? null : literal(fallback)
const rowsOf = (value: any): Params[] => Array.isArray(value) ? value.filter(row => row && typeof row === 'object') : []
const idFor = (prefix: string) => `${prefix}-${crypto.randomUUID()}`
const messageOf = (reason: unknown) => reason instanceof Error ? reason.message : String(reason || '操作失败，请重试')
const kindOf = (type: string): ValueKind | undefined => ({
  bool: 'Bool', boolean: 'Bool', int: 'Int', integer: 'Int', float: 'Float', number: 'Float', double: 'Float',
  text: 'Text', str: 'Text', string: 'Text', image: 'Image', imagearray: 'ImageArray', 'list<image>': 'ImageArray',
  region: 'Region', 'list<region>': 'Region',
} as Record<string, ValueKind>)[String(type).replace(/\s/g, '').toLowerCase()]

function initialDraft(node: WorkflowNode, models: ModelPackage[]): Params {
  const p = structuredClone(node.data.params || {})
  if (node.data.nodeType === 'capture') {
    p.source ||= 'file'
    p.file = { path: '', ...p.file }
    p.dir = { path: '', autoSwitch: false, loop: true, intervalUs: 1000000, ...p.dir, files: rowsOf(p.dir?.files) }
    const c = p.camera || {}
    p.camera = {
      ...c, cameraId: c.cameraId || '', triggerMode: c.triggerMode || ({ softTrigger: 'soft', freeRun: 'continuous' }[c.mode as string] || 'soft'),
      timeout: { enabled: true, valueUs: 1000000, ...c.timeout },
      exposure: { ...c.exposure, enabled: c.exposure?.enabled ?? false, value: valueObject(c.exposure?.value) },
      gain: { ...c.gain, enabled: c.gain?.enabled ?? false, value: valueObject(c.gain?.value) },
      line: { ...c.line, enabled: c.line?.enabled ?? false, value: valueObject(c.line?.value) },
      transform: c.transform || { enabled: !!(c.flipX || c.flipY), mode: c.flipX && c.flipY ? 'rot180' : c.flipX ? 'hflip' : c.flipY ? 'vflip' : 'none' },
    }
    delete p.camera.mode; delete p.camera.flipX; delete p.camera.flipY
  }
  if (node.data.nodeType === 'model') {
    delete p.device
    const model = models.find(m => m.model_id === p.modelId || m.id === p.modelId)
    if (model) p.modelId = model.model_id
    for (const param of model?.manifest.params || []) {
      if (param.name && !['modelId', 'image', 'lable', 'label'].includes(param.name)) p[param.name] = valueObject(p[param.name], param.default ?? null)
    }
  }
  if (node.data.nodeType === 'display') {
    p.windowId = 1
    p.shapes = rowsOf(p.shapes).map(row => ({ ...row, id: row.id || idFor('shape'), visible: valueObject(row.visible, true), status: valueObject(row.status, true), showLabel: row.showLabel ?? false, labelText: valueObject(row.labelText, ''), style: { okColor: '#27C346', ngColor: '#F76965', lineWidth: 1, ...row.style } }))
    p.texts = rowsOf(p.texts).map(row => ({ ...row, id: row.id || idFor('text'), content: valueObject(row.content, ''), visible: valueObject(row.visible, true), status: valueObject(row.status, true), x: valueObject(row.x, 0), y: valueObject(row.y, 0), fontSize: row.fontSize ?? 32 }))
  }
  if (node.data.nodeType === 'branch') {
    const conditions = rowsOf(p.conditions)
    p.conditions = conditions.length ? conditions.map(row => ({ ...row, value: valueObject(row.value, false) })) : [{ id: idFor('condition'), value: literal(false) }]
  }
  if (node.data.nodeType === 'plc') {
    Object.assign(p, { host: '', port: 502, unitId: 1, address: 0, operation: 'coil', ...p })
    p.value = valueObject(p.value, p.operation === 'register' ? 0 : false)
  }
  return p
}

export function NodeConfigDialog(props: NodeConfigDialogProps) {
  // 即使调用方未提供 key，切换节点也不会复用上个节点的草稿或异步状态。
  return <NodeConfigSession key={props.node.id} {...props} />
}

function NodeConfigSession({ node, nodes, models, cameras, preview, onClose, onPatch, onLabel, onSave, onDebug }: NodeConfigDialogProps) {
  const [draft, setDraft] = useState<Params>(() => initialDraft(node, models))
  const [label, setLabel] = useState(node.data.label)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [debugPreview, setDebugPreview] = useState<string | null>(null)
  const [failedPreview, setFailedPreview] = useState<string | null>(null)
  const [previewAnnotations, setPreviewAnnotations] = useState<Params[]>([])
  const [notice, setNotice] = useState('')
  const [debugDurationMs, setDebugDurationMs] = useState<number | null>(null)
  const [debugSucceeded, setDebugSucceeded] = useState(false)
  const alive = useRef(true)
  const locked = useRef(false)
  const localPreviewUrl = useRef<string | null>(null)
  const formRef = useRef<HTMLFormElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const type = node.data.nodeType
  const variables = useMemo(() => collectVariables(node, nodes, models), [node, nodes, models])
  const branch = type === 'branch'
  const compact = branch || type === 'plc'
  const previewUrl = debugPreview || preview
  const close = () => {
    // 已提交的持久化请求不可撤回；保存期间不允许取消，避免“取消但已保存”。
    if (locked.current && busy === '保存') return
    alive.current = false
    onClose()
  }
  useEffect(() => { alive.current = true; return () => {
    alive.current = false
    if (localPreviewUrl.current) URL.revokeObjectURL(localPreviewUrl.current)
  } }, [])
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    formRef.current?.focus()
    return () => previous?.focus()
  }, [])

  const patch = (changes: Params) => {
    setDraft(current => ({ ...current, ...changes }))
    setError(''); setNotice(''); setDebugPreview(null); setPreviewAnnotations([]); setFailedPreview(null); setDebugDurationMs(null); setDebugSucceeded(false)
  }
  const perform = async (name: string, action: () => Promise<void>) => {
    if (locked.current) return
    locked.current = true
    setBusy(name); setError(''); setNotice('')
    try { await action() } catch (reason) {
      if (alive.current) setError(messageOf(reason))
    } finally {
      if (alive.current) { locked.current = false; setBusy('') }
    }
  }
  const validate = () => {
    const issue = validateDraft(type, draft, label, variables, models, cameras)
    if (issue) { setError(issue); return false }
    return formRef.current?.reportValidity() ?? true
  }
  const save = () => {
    if (!validate()) return
    void perform('保存', async () => {
      const snapshot = structuredClone(draft)
      if (onSave) await onSave(snapshot, label.trim())
      else { await onPatch(snapshot); await onLabel(label.trim()) }
      if (alive.current) { alive.current = false; onClose() }
    })
  }
  const debug = (params: Params = draft) => {
    if (!onDebug || !validate()) return
    setDebugPreview(null); setPreviewAnnotations([]); setFailedPreview(null)
    void perform('调试', async () => {
      const result = await onDebug(structuredClone(params))
      if (!alive.current) return
      setDebugPreview(result.imageUrl || null)
      setPreviewAnnotations(result.imageUrl ? rowsOf(result.annotations) : [])
      setDebugDurationMs(Number.isFinite(result.durationMs) ? Number(result.durationMs) : null)
      setDebugSucceeded(result.status !== 'error')
      setNotice(result.imageUrl ? '调试完成' : '调试完成，未返回预览图像')
    })
  }
  return <div className="node-config-overlay nc-overlay" onMouseDown={event => { if (event.target === event.currentTarget) close() }}>
    <form ref={formRef} tabIndex={-1} className={`node-config-dialog nc-dialog ${compact ? 'nc-compact' : ''} ${branch ? 'is-branch' : ''} ${type === 'capture' ? 'is-capture-dialog' : ''}`} role="dialog" aria-modal="true" aria-label={`${node.data.label}配置`} onSubmit={event => { event.preventDefault(); save() }} onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); close() }
      if (event.key === 'Tab') {
        const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled):not([hidden]), select:not(:disabled), textarea:not(:disabled)')).filter(item => item.offsetParent !== null)
        const first = items[0], last = items.at(-1)
        if (event.shiftKey && (document.activeElement === first || document.activeElement === formRef.current)) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }
    }}>
      <header className="node-config-header"><div className="node-config-title"><span className="node-config-art">{stepIcon(type)}</span><span>{label || node.data.label}</span></div><button type="button" className="node-config-close" onClick={close} disabled={busy === '保存'} aria-label="关闭配置"><img src={closeIcon} alt="" /></button></header>
      <div className="node-config-main">
        <fieldset className={`node-config-settings nc-settings ${type === 'capture' ? 'nc-capture-settings' : ''}`} disabled={!!busy}>
          {type !== 'capture' && <Field label="节点名称"><input aria-label="节点名称" value={label} required onChange={event => setLabel(event.target.value)} /></Field>}
          {type === 'capture' && <CaptureSettings draft={draft} patch={patch} cameras={cameras} variables={variables} fileRef={fileRef} onUpload={file => {
            if (!file) return
            void perform('上传', async () => {
              const form = new FormData(); form.append('file', file)
              const result = await api.post<{ path: string; name: string; width?: number; height?: number; size?: number; format?: string; bitDepth?: string }>('/files/upload', form)
              if (alive.current) {
                patch({ file: { path: result.path, name: result.name, width: result.width, height: result.height, size: result.size ?? file.size, format: result.format || file.name.split('.').pop()?.toUpperCase(), bitDepth: result.bitDepth || '24-bit RGB' } })
                if (localPreviewUrl.current) URL.revokeObjectURL(localPreviewUrl.current)
                localPreviewUrl.current = URL.createObjectURL(file)
                setDebugPreview(localPreviewUrl.current)
              }
              if (fileRef.current) fileRef.current.value = ''
            })
          }} onScan={() => {
            if (!draft.dir.path.trim()) { setError('请输入目录路径'); return }
            void perform('扫描', async () => {
              const result = await api.post<{ files: Params[] }>('/files/scan', { path: draft.dir.path })
              if (alive.current) patch({ dir: { ...draft.dir, files: result.files.map(file => ({ ...file, selected: draft.dir.files.find((old: Params) => old.path === file.path)?.selected ?? true })) } })
            })
          }} onPreviewFile={path => debug({ ...draft, source: 'file', file: { ...draft.file, path } })} />}
          {type === 'model' && <ModelSettings draft={draft} patch={patch} models={models} variables={variables} />}
          {type === 'display' && <DisplaySettings draft={draft} patch={patch} variables={variables} />}
          {branch && <div className="nc-branch-content"><div className="nc-branch-type"><i />Bool 条件</div>{draft.conditions.map((condition: Params, index: number) => <ValueField key={condition.id || index} label={draft.conditions.length > 1 ? `条件 ${index + 1}` : '条件值'} kind="Bool" value={condition.value} variables={variables} onChange={value => patch({ conditions: draft.conditions.map((row: Params, i: number) => i === index ? { ...row, value } : row) })} />)}<div className="nc-branch-tip"><span>i</span>值为 True 时条件成立，可输入常量或链接上游 Bool 变量。</div></div>}
          {type === 'plc' && <PlcSettings draft={draft} patch={patch} variables={variables} />}
        </fieldset>
        {!compact && <div className="node-config-preview nc-preview">{previewUrl && previewUrl !== failedPreview ? <PreviewImage key={previewUrl} src={previewUrl} annotations={previewAnnotations} onError={() => setFailedPreview(previewUrl)} /> : type === 'capture' ? null : <div className="nc-preview-empty">{failedPreview ? '图像加载失败，请重新调试' : '暂无预览图像'}<small>点击调试获取当前配置的图像</small></div>}</div>}
      </div>
      <footer className="node-config-footer nc-footer">
        <div className="nc-feedback" role={error ? 'alert' : 'status'}>{error || (busy ? `${busy}中…` : notice)}</div>
        {type === 'capture' && draft.source === 'camera' && <div className="nc-camera-execution"><button type="button" disabled={!onDebug || !!busy} onClick={() => debug()}>{busy === '调试' ? '执行中' : '执行'}</button>{debugSucceeded && <><i /><b>OK</b></>}{debugDurationMs !== null && <span>耗时(μs)：{debugDurationMs * 1000}</span>}</div>}
        {type !== 'capture' && onDebug && <button type="button" disabled={!!busy} onClick={() => debug()}>{busy === '调试' ? '调试中' : '调试'}</button>}
        <button type="button" onClick={close} disabled={busy === '保存'}>取消</button>
        <button type="submit" className="primary" disabled={!!busy}>{busy === '保存' ? '保存中' : '保存'}</button>
      </footer>
    </form>
  </div>
}

function PreviewImage({ src, annotations, onError }: { src: string; annotations: Params[]; onError: () => void }) {
  const [size, setSize] = useState<{ width: number; height: number } | null>(null)
  return <div className="nc-image-layer">
    <img src={src} alt="节点真实图像预览" onLoad={event => setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} onError={onError} />
    {size && size.width > 0 && size.height > 0 && <AnnotationOverlay width={size.width} height={size.height} annotations={annotations} ariaLabel="调试标注" />}
  </div>
}

interface SettingsProps { draft: Params; patch: (changes: Params) => void; variables: VariableOption[] }
const formatFileSize = (size: unknown) => {
  const bytes = Number(size)
  if (!Number.isFinite(bytes) || bytes <= 0) return '—'
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(bytes / 1024))}KB`
}

const formatFileTime = (file: Params) => {
  const value = file.createdAt || file.modifiedAt || file.mtime
  if (!value) return '—'
  const date = new Date(typeof value === 'number' && value < 10_000_000_000 ? value * 1000 : value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
}

function CaptureSettings({ draft, patch, cameras, fileRef, onScan, onUpload, onPreviewFile }: SettingsProps & { cameras: Camera[]; fileRef: React.RefObject<HTMLInputElement | null>; onScan: () => void; onUpload: (file?: File) => void; onPreviewFile: (path: string) => void }) {
  const c = draft.camera, dir = draft.dir
  const [selectedFileIndex, setSelectedFileIndex] = useState(0)
  const [sortAscending, setSortAscending] = useState(true)
  const cameraPatch = (value: Params) => patch({ camera: { ...c, ...value } })
  const dirPatch = (value: Params) => patch({ dir: { ...dir, ...value } })
  const selectedCount = dir.files.filter((file: Params) => file.selected !== false).length
  const moveSelectedFile = (offset: number) => {
    const nextIndex = selectedFileIndex + offset
    if (selectedFileIndex < 0 || nextIndex < 0 || nextIndex >= dir.files.length) return
    const files = [...dir.files]
    ;[files[selectedFileIndex], files[nextIndex]] = [files[nextIndex], files[selectedFileIndex]]
    setSelectedFileIndex(nextIndex)
    dirPatch({ files })
  }
  const transformActions: Array<[string, string, string]> = [
    ['hflip', '水平翻转', captureFlipHorizontalIcon],
    ['vflip', '垂直翻转', captureFlipVerticalIcon],
    ['rot90_ccw', '逆时针旋转', captureRotateLeftIcon],
    ['rot90_cw', '顺时针旋转', captureRotateRightIcon],
    ['rot180', '旋转180度', captureRotate180Icon],
  ]
  return <div className="nc-capture-settings">
    <section className="nc-source-block"><div className="nc-source-head"><span>图像源</span><div className="nc-source-tabs">{[['file', '指定图片'], ['dir', '文件目录'], ['camera', '相机']].map(([key, text]) => <button type="button" aria-pressed={draft.source === key} className={draft.source === key ? 'active' : ''} key={key} onClick={() => patch({ source: key })}><img src={draft.source === key ? captureRadioCheckedIcon : captureRadioUncheckedIcon} alt="" />{text}</button>)}</div></div></section>
    {draft.source === 'file' && <>
      <section className="nc-capture-file-section"><strong>文件路径</strong><input ref={fileRef} hidden type="file" accept=".jpg,.jpeg,.png,.bmp,.tif,.tiff" onChange={event => { onUpload(event.target.files?.[0]); event.target.value = '' }} /><div className="nc-capture-path"><input aria-label="图片路径" value={draft.file.path} placeholder="请选择图片" onChange={event => patch({ file: { ...draft.file, path: event.target.value } })} /><button type="button" aria-label="选择图片" title="选择图片" onClick={() => fileRef.current?.click()}><img src={captureFolderIcon} alt="" /></button><small>支持格式：JPG、PNG、BMP、TIFF</small></div></section>
      <section className="nc-capture-info-section"><strong>图像信息</strong><div>{[['尺寸', draft.file.width && draft.file.height ? `${draft.file.width}*${draft.file.height}` : '—'], ['格式', draft.file.format || '—'], ['大小', formatFileSize(draft.file.size)], ['位深', draft.file.bitDepth || '—']].map(([name, value]) => <p key={name}><span>{name}</span><b>{value}</b></p>)}</div></section>
    </>}
    {draft.source === 'dir' && <>
      <section className="nc-directory-path"><strong>文件路径</strong><div className="nc-capture-path"><input aria-label="目录路径" value={dir.path} placeholder="请选择目录" onChange={event => dirPatch({ path: event.target.value, files: [] })} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); onScan() } }} /><button type="button" aria-label="扫描目录" title="扫描目录" onClick={onScan}><img src={captureFolderIcon} alt="" /></button></div></section>
      <section className="nc-directory-list-section">
        <div className="nc-directory-options"><strong>图像信息</strong><div className="nc-directory-option-controls"><div className="nc-directory-checks"><label><input type="checkbox" checked={dir.autoSwitch} onChange={event => dirPatch({ autoSwitch: event.target.checked })} />自动切换</label><label><input type="checkbox" checked={dir.loop} onChange={event => dirPatch({ loop: event.target.checked })} />循环</label></div><div className="nc-directory-gap-control"><span>间隔</span><div className="nc-directory-interval"><input aria-label="切换间隔" type="number" min={1} value={dir.intervalUs ?? ''} onChange={event => dirPatch({ intervalUs: event.target.value === '' ? null : event.target.valueAsNumber })} /><b>μs</b></div></div></div></div>
        <div className="nc-directory-toolbar"><strong>图片列表</strong><div><button type="button" aria-label="上移图片" title="上移" disabled={selectedFileIndex <= 0} onClick={() => moveSelectedFile(-1)}><img src={captureMoveUpIcon} alt="" /></button><button type="button" className="move-down" aria-label="下移图片" title="下移" disabled={selectedFileIndex < 0 || selectedFileIndex >= dir.files.length - 1} onClick={() => moveSelectedFile(1)}><img src={captureMoveUpIcon} alt="" /></button><i /><button type="button" aria-label="图片排序" title="按名称排序" onClick={() => { const direction = sortAscending ? 1 : -1; dirPatch({ files: [...dir.files].sort((a: Params, b: Params) => direction * String(a.name || a.path).localeCompare(String(b.name || b.path))) }); setSortAscending(value => !value); setSelectedFileIndex(0) }}><img src={captureSortIcon} alt="" /></button><i /><button type="button" aria-label="删除图片" title="删除" disabled={!dir.files.length} onClick={() => { dirPatch({ files: dir.files.filter((_: Params, index: number) => index !== selectedFileIndex) }); setSelectedFileIndex(Math.max(0, Math.min(selectedFileIndex, dir.files.length - 2))) }}><img src={captureDeleteIcon} alt="" /></button></div></div>
        <div className="nc-directory-table"><div className="nc-file-head"><span>选用</span><span>序号</span><span>名称</span><span>创建时间</span></div><div className="nc-file-list">{dir.files.map((file: Params, index: number) => <div role="button" tabIndex={0} aria-selected={selectedFileIndex === index} className={`nc-file-row ${selectedFileIndex === index ? 'selected' : ''}`} key={file.path} onClick={() => { setSelectedFileIndex(index); onPreviewFile(file.path) }} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelectedFileIndex(index); onPreviewFile(file.path) } }}><input aria-label={`选用${file.name || index + 1}`} type="checkbox" checked={file.selected !== false} onClick={event => event.stopPropagation()} onChange={event => dirPatch({ files: dir.files.map((row: Params, i: number) => i === index ? { ...row, selected: event.target.checked } : row) })} /><span>{index}</span><span title={file.path}><img src={captureImageIcon} alt="" />{file.name || file.path}</span><span>{formatFileTime(file)}</span></div>)}{!dir.files.length && <div className="nc-empty">尚未扫描到图片</div>}</div><div className="nc-directory-summary"><label><input type="checkbox" checked={!!dir.files.length && selectedCount === dir.files.length} onChange={event => dirPatch({ files: dir.files.map((file: Params) => ({ ...file, selected: event.target.checked })) })} />全选</label><span>共{dir.files.length}个文件/已选{selectedCount}个</span></div></div>
      </section>
    </>}
    {draft.source === 'camera' && <>
      <section className="nc-camera-base-section"><strong>相机基础配置</strong><div className="nc-camera-fields"><label><span>相机选择</span><div className="nc-camera-select-wrap"><img src={captureCameraIcon} alt="" /><DesignSelect className="nc-camera-select-trigger" ariaLabel="相机选择" value={c.cameraId} onChange={cameraId => cameraPatch({ cameraId })} options={[{ value: '', label: '请选择' }, ...(c.cameraId && !cameras.some(camera => camera.id === c.cameraId && camera.connected) ? [{ value: c.cameraId, label: `${c.cameraId}（未连接）`, disabled: true }] : []), ...cameras.filter(camera => camera.connected).map(camera => ({ value: camera.id, label: `${camera.name} · ${camera.model}` }))]} /></div></label><label><span>采集模式</span><DesignSelect ariaLabel="采集模式" value={c.triggerMode} onChange={triggerMode => cameraPatch({ triggerMode })} options={[{ value: 'soft', label: '软触发' }, { value: 'hardware', label: '硬触发' }, { value: 'continuous', label: '连续采集' }]} /></label><label><span>采集超时(μs)</span><input aria-label="采集超时(μs)" type="number" min={1} placeholder="1000000" title="建议 ≥ 500000：大分辨率相机传输一帧就需要 100ms 以上" value={c.timeout.valueUs ?? ''} onChange={event => cameraPatch({ timeout: { ...c.timeout, enabled: true, valueUs: event.target.value === '' ? null : event.target.valueAsNumber } })} /></label></div></section>
      <section className="nc-camera-imaging-section"><strong>成像参数</strong><div className="nc-camera-imaging"><label className="nc-camera-toggle"><button type="button" role="switch" aria-checked={c.exposure.enabled} className={c.exposure.enabled ? 'on' : ''} onClick={() => cameraPatch({ exposure: { ...c.exposure, enabled: !c.exposure.enabled } })}><i /></button><span>曝光时间(μs)</span></label>{c.exposure.enabled && <input aria-label="曝光时间(μs)" type="number" min={1} value={numberContent(c.exposure.value, 500)} onChange={event => cameraPatch({ exposure: { ...c.exposure, value: literal(event.target.valueAsNumber) } })} />}<label className="nc-camera-toggle"><button type="button" role="switch" aria-checked={c.gain.enabled} className={c.gain.enabled ? 'on' : ''} onClick={() => cameraPatch({ gain: { ...c.gain, enabled: !c.gain.enabled } })}><i /></button><span>增益</span></label>{c.gain.enabled && <input aria-label="增益" type="number" min={0} value={numberContent(c.gain.value, 0)} onChange={event => cameraPatch({ gain: { ...c.gain, value: literal(event.target.valueAsNumber) } })} />}</div></section>
      <section className="nc-camera-transform-section"><strong>图像变换</strong><div className="nc-transform-actions">{transformActions.map(([mode, title, icon]) => <button type="button" key={mode} title={title} aria-label={title} aria-pressed={c.transform.enabled && c.transform.mode === mode} className={c.transform.enabled && c.transform.mode === mode ? 'active' : ''} onClick={() => cameraPatch({ transform: c.transform.enabled && c.transform.mode === mode ? { enabled: false, mode: 'none' } : { enabled: true, mode } })}><img src={icon} alt="" /></button>)}</div></section>
    </>}
  </div>
}

function ModelSettings({ draft, patch, models, variables }: SettingsProps & { models: ModelPackage[] }) {
  const [tab, setTab] = useState('model')
  const model = models.find(item => item.model_id === draft.modelId)
  return <><Tabs values={[['basic', '基础设置'], ['model', '模型设置']]} value={tab} onChange={setTab} />{tab === 'basic' ? <ValueField label="输入图像" kind="Image" value={draft.image} variables={variables} onChange={image => patch({ image })} /> : <>
    <Field label="模型选择"><DesignSelect value={draft.modelId || ''} onChange={modelId => {
      const next = models.find(item => item.model_id === modelId)
      const changes: Params = { modelId }
      for (const param of model?.manifest.params || []) if (!['modelId', 'image', 'lable', 'label'].includes(param.name)) changes[param.name] = null
      for (const param of next?.manifest.params || []) if (!['modelId', 'image', 'lable', 'label'].includes(param.name)) changes[param.name] = valueObject(param.default)
      patch(changes)
    }} options={[{ value: '', label: '请选择' }, ...(draft.modelId && !model ? [{ value: draft.modelId, label: `${draft.modelId}（不可用）`, disabled: true }] : []), ...models.map(item => ({ value: item.model_id, label: `${item.name} · ${item.version}${item.serviceStatus === 'running' ? '' : '（未启动）'}` }))]} /></Field>
    {model ? <><section className="nc-model-info"><h4>模型信息</h4>{[['模型名称', model.name], ['模型 ID', model.model_id], ['版本', model.version], ['服务状态', model.serviceStatus === 'running' ? '运行中' : model.serviceStatus === 'error' ? '错误' : '未启动']].map(([key, value]) => <div key={key}><span>{key}</span><b title={value}>{value}</b></div>)}</section><section className="nc-section"><h4>推理参数</h4>{(model.manifest.params || []).filter(param => !['modelId', 'image', 'lable', 'label'].includes(param.name)).map(param => {
      const kind = kindOf(param.type)
      return kind ? <ValueField key={param.name} label={param.label || param.name} kind={kind} value={draft[param.name]} min={param.min} max={param.max} variables={variables} onChange={value => patch({ [param.name]: value })} /> : <p className="node-config-help" key={param.name}>{param.label || param.name}：暂不支持的参数类型 {param.type}</p>
    })}{!model.manifest.params?.length && <div className="node-config-help">此模型没有可配置参数。</div>}</section></> : <div className="node-config-help">选择模型后显示模型信息和推理参数。</div>}
  </>}</>
}

function DisplaySettings({ draft, patch, variables }: SettingsProps) {
  const [tab, setTab] = useState<'shapes' | 'texts'>('shapes')
  const [selectedId, setSelectedId] = useState<string | null>(draft.shapes[0]?.id || null)
  const [editor, setEditor] = useState<{ collection: 'shapes' | 'texts'; index: number | null; row: Params } | null>(null)
  const rows = rowsOf(draft[tab])
  const switchTab = (next: 'shapes' | 'texts') => {
    setTab(next)
    setSelectedId(rowsOf(draft[next])[0]?.id || null)
  }
  const add = () => setEditor({ collection: tab, index: null, row: newDisplayItem(tab) })
  const edit = (index: number) => setEditor({ collection: tab, index, row: structuredClone(rows[index]) })
  const remove = () => {
    const index = rows.findIndex(row => row.id === selectedId)
    if (index < 0) return
    const next = rows.filter((_, rowIndex) => rowIndex !== index)
    patch({ [tab]: next })
    setSelectedId(next[Math.min(index, next.length - 1)]?.id || null)
  }
  const saveItem = (row: Params) => {
    if (!editor) return
    const current = rowsOf(draft[editor.collection])
    const next = editor.index == null ? [...current, row] : current.map((item, index) => index === editor.index ? row : item)
    patch({ [editor.collection]: next })
    setTab(editor.collection)
    setSelectedId(row.id)
    setEditor(null)
  }
  return <>
    <section className="nc-image-link"><h4>图像链接</h4><ValueField label="输入图像" kind="Image" value={draft.image} variables={variables} onChange={image => patch({ image })} /></section>
    <section className="nc-display-settings">
      <h4>显示设置</h4>
      <div className="nc-display-tabs" role="tablist" aria-label="显示设置类型">
        <button type="button" role="tab" aria-selected={tab === 'shapes'} onClick={() => switchTab('shapes')}>绘制图形</button>
        <button type="button" role="tab" aria-selected={tab === 'texts'} onClick={() => switchTab('texts')}>绘制文字</button>
      </div>
      <div className="nc-display-actions">
        <button type="button" aria-label={`新增${tab === 'shapes' ? '图形' : '文字'}`} onClick={add}><img src={displayAddIcon} alt="" /></button>
        <button type="button" aria-label={`删除选中${tab === 'shapes' ? '图形' : '文字'}`} disabled={!selectedId} onClick={remove}><img src={displayRemoveIcon} alt="" /></button>
      </div>
      <div className="nc-display-table" role="table" aria-label={tab === 'shapes' ? '绘制图形列表' : '绘制文字列表'}>
        <div className="nc-display-table-head" role="row"><span /><span role="columnheader">#</span><span role="columnheader">类型</span><span role="columnheader">显示内容</span><span role="columnheader">是否显示</span><span role="columnheader">状态链接</span><span /></div>
        <div className="nc-display-table-body" role="rowgroup">
          {rows.map((row, index) => {
            const visible = displayBool(row.visible, variables)
            const status = displayBool(row.status, variables)
            return <div key={row.id} className={`nc-display-row ${row.id === selectedId ? 'selected' : ''}`} role="row" tabIndex={0} aria-selected={row.id === selectedId} onClick={() => setSelectedId(row.id)} onDoubleClick={() => edit(index)} onKeyDown={event => { if (event.key === 'Enter') edit(index); if (event.key === 'Delete') remove() }}>
              <span /><span role="cell">{index + 1}</span><span role="cell"><b>{tab === 'shapes' ? 'Region' : 'Text'}</b></span><span role="cell" title={displayContent(row.content, variables)}>{displayContent(row.content, variables)}</span><span role="cell"><DisplayState value={visible} /></span><span role="cell"><DisplayState value={status} /></span><span />
            </div>
          })}
          {!rows.length && <div className="nc-display-empty">暂无数据</div>}
        </div>
      </div>
    </section>
    {editor && <DisplayItemDialog collection={editor.collection} row={editor.row} variables={variables} onCancel={() => setEditor(null)} onSave={saveItem} />}
  </>
}

function newDisplayItem(collection: 'shapes' | 'texts'): Params {
  return collection === 'shapes'
    ? { id: idFor('shape'), content: null, visible: literal(false), status: literal(true), showLabel: true, labelText: literal(''), style: { okColor: '#27C346', ngColor: '#F76965', lineWidth: 1 } }
    : { id: idFor('text'), content: literal(''), visible: literal(false), status: literal(true), x: literal(0), y: literal(0), fontSize: 32 }
}

function displayContent(value: ValueObject | null | undefined, variables: VariableOption[]): string {
  if (!value) return '请链接'
  if (value.type === 'ref') return variables.find(item => item.ref === value.ref)?.label || value.name || String(value.ref || '请链接')
  return String(value.ref || '—')
}

function displayBool(value: ValueObject | null | undefined, variables: VariableOption[]): boolean | null {
  if (!value) return null
  const resolved = value.type === 'ref' ? variables.find(item => item.ref === value.ref)?.value : value.ref
  return typeof resolved === 'boolean' ? resolved : null
}

function DisplayState({ value }: { value: boolean | null }) {
  return <span className={`nc-display-state ${value == null ? 'unknown' : value ? 'true' : 'false'}`}><i />{value == null ? '—' : String(value)}</span>
}

function DisplayItemDialog({ collection, row, variables, onCancel, onSave }: { collection: 'shapes' | 'texts'; row: Params; variables: VariableOption[]; onCancel: () => void; onSave: (row: Params) => void }) {
  const [draft, setDraft] = useState<Params>(() => structuredClone(row))
  const [error, setError] = useState('')
  const dialogRef = useRef<HTMLDivElement>(null)
  const shape = collection === 'shapes'
  const update = (changes: Params) => { setDraft(current => ({ ...current, ...changes })); setError('') }
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    dialogRef.current?.focus()
    return () => previous?.focus()
  }, [])
  const save = () => {
    if (shape && (!draft.content || draft.content.type !== 'ref' || !variables.some(item => item.kind === 'Region' && item.ref === draft.content.ref))) { setError('请链接 Region 显示内容'); return }
    if (!shape && (!draft.content || (draft.content.type === 'content' && !String(draft.content.ref).trim()) || (draft.content.type === 'ref' && !variables.some(item => item.kind === 'Text' && item.ref === draft.content.ref)))) { setError('请输入或链接 Text 显示内容'); return }
    onSave(structuredClone(draft))
  }
  const dialog = <div className="nc-item-overlay" onMouseDown={event => { event.stopPropagation(); if (event.target === event.currentTarget) onCancel() }} onKeyDown={event => { event.stopPropagation(); if (event.key === 'Escape') { event.preventDefault(); onCancel() } }}>
    <div ref={dialogRef} tabIndex={-1} className={`nc-item-dialog ${shape ? 'is-region' : 'is-text'}`} role="dialog" aria-modal="true" aria-label={shape ? 'Region设置' : 'Text设置'}>
      <header className="nc-item-header"><span>{shape ? 'Region设置' : 'Text设置'}</span><button type="button" aria-label={`关闭${shape ? 'Region' : 'Text'}设置`} onClick={onCancel}><img src={displayCloseIcon} alt="" /></button></header>
      <div className="nc-item-body">
        <DisplayItemValueField label="显示内容" kind={shape ? 'Region' : 'Text'} value={draft.content} variables={variables} onChange={content => update({ content })} />
        <DisplayItemValueField label="是否显示" kind="Bool" value={draft.visible} variables={variables} onChange={visible => update({ visible })} />
        <DisplayItemValueField label="状态链接" kind="Bool" value={draft.status} variables={variables} onChange={status => update({ status })} />
        {shape ? <>
          <DisplayItemToggle label="是否显示标签" checked={!!draft.showLabel} onChange={showLabel => update({ showLabel })} />
          <DisplayItemValueField label="标签文字" kind="Text" value={draft.labelText} variables={variables} placeholder="请输入标签文字" onChange={labelText => update({ labelText })} />
          <DisplayColorField label="OK颜色" value={draft.style.okColor} onChange={okColor => update({ style: { ...draft.style, okColor } })} />
          <DisplayColorField label="NG颜色" value={draft.style.ngColor} onChange={ngColor => update({ style: { ...draft.style, ngColor } })} />
          <DisplayItemNumberField label="轮廓宽度" value={draft.style.lineWidth} min={1} onChange={lineWidth => update({ style: { ...draft.style, lineWidth } })} />
        </> : <>
          <DisplayItemNumberField label="位置X" ariaLabel="位置 X" value={numberContent(draft.x, 0)} min={0} onChange={x => update({ x: literal(x) })} />
          <DisplayItemNumberField label="位置Y" ariaLabel="位置 Y" value={numberContent(draft.y, 0)} min={0} onChange={y => update({ y: literal(y) })} />
          <DisplayItemNumberField label="字体大小" value={Number(draft.fontSize) || 32} min={1} onChange={fontSize => update({ fontSize })} />
        </>}
      </div>
      <footer className="nc-item-footer"><span role={error ? 'alert' : undefined}>{error}</span><button type="button" onClick={onCancel}>取消</button><button type="button" className="primary" onClick={save}>保存</button></footer>
    </div>
  </div>
  return createPortal(dialog, document.querySelector('.app-shell') || document.body)
}

function DisplayItemValueField({ label, kind, value, variables, onChange, placeholder = '请链接' }: { label: string; kind: 'Region' | 'Text' | 'Bool'; value?: ValueObject | null; variables: VariableOption[]; onChange: (value: ValueObject | null) => void; placeholder?: string }) {
  const [open, setOpen] = useState(false)
  const available = variables.filter(item => item.kind === kind)
  const selected = available.find(item => item.ref === value?.ref)
  const isRef = value?.type === 'ref'
  return <div className="nc-item-field">
    <span>{label}</span>
    <div className="nc-item-control-pair">
      <div className={`nc-item-control ${isRef ? 'is-reference' : ''}`}>
        {kind === 'Region' ? <span title={String(value?.ref || '')}>{selected?.label || value?.name || placeholder}</span>
          : kind === 'Bool' ? <DesignSelect className="nc-item-select" ariaLabel={label} value={isRef ? '__reference__' : String(value?.ref ?? false)} onChange={next => onChange(next === '__reference__' ? value || literal(false) : literal(next === 'true'))} options={[...(isRef ? [{ value: '__reference__', label: selected?.label || value?.name || String(value?.ref) }] : []), { value: 'false', label: 'False' }, { value: 'true', label: 'True' }]} />
            : <input aria-label={label} value={isRef ? selected?.label || value?.name || String(value?.ref || '') : String(value?.ref ?? '')} placeholder={placeholder} onChange={event => onChange(literal(event.target.value))} />}
      </div>
      <button type="button" className="nc-item-link" aria-label={`选择${label}引用`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}><img src={displayLinkIcon} alt="" /></button>
    </div>
    {isRef && !selected && <small>引用已失效</small>}
    {open && <VariableReferenceDialog label={label} kind={kind} variables={available} value={value} onClose={() => setOpen(false)} onSelect={item => { onChange({ type: 'ref', ref: item.ref, name: item.label }); setOpen(false) }} />}
  </div>
}

function DisplayItemToggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <div className="nc-item-field nc-item-toggle-field"><span>{label}</span><button type="button" role="switch" aria-label={label} aria-checked={checked} className={checked ? 'on' : ''} onClick={() => onChange(!checked)}><i /></button></div>
}

function DisplayColorField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <div className="nc-item-field"><span>{label}</span><label className="nc-item-color"><i style={{ background: value }} /><img src={displayChevronDownIcon} alt="" /><input aria-label={label} type="color" value={value} onChange={event => onChange(event.target.value.toUpperCase())} /></label></div>
}

function DisplayItemNumberField({ label, ariaLabel = label, value, min, onChange }: { label: string; ariaLabel?: string; value: number; min: number; onChange: (value: number) => void }) {
  const apply = (next: number) => onChange(Math.max(min, Number.isFinite(next) ? next : min))
  return <div className="nc-item-field"><span>{label}</span><div className="nc-item-number"><input aria-label={ariaLabel} type="number" min={min} value={value} onChange={event => apply(event.target.valueAsNumber)} /><span><button type="button" aria-label={`${label}增加`} onClick={() => apply(value + 1)}><img src={displayChevronUpIcon} alt="" /></button><button type="button" aria-label={`${label}减少`} onClick={() => apply(value - 1)}><img src={displayChevronDownIcon} alt="" /></button></span></div></div>
}

function numberContent(value: ValueObject | number | null | undefined, fallback: number): number {
  const resolved = value && typeof value === 'object' ? value.type === 'content' ? value.ref : fallback : value
  const number = Number(resolved)
  return Number.isFinite(number) ? number : fallback
}

function PlcSettings({ draft, patch, variables }: SettingsProps) {
  return <section className="nc-section"><h4>Modbus TCP</h4><Field label="主机地址"><input value={draft.host} placeholder="IP 地址或主机名" onChange={event => patch({ host: event.target.value })} /></Field><NumberField label="端口" value={draft.port} min={1} max={65535} onChange={port => patch({ port })} /><NumberField label="单元 ID" value={draft.unitId} min={0} max={255} onChange={unitId => patch({ unitId })} /><NumberField label="地址" value={draft.address} min={0} max={65535} onChange={address => patch({ address })} /><Field label="操作类型"><DesignSelect value={draft.operation} onChange={operation => patch({ operation, value: literal(operation === 'coil' ? false : 0) })} options={[{ value: 'coil', label: '写入线圈 (coil)' }, { value: 'register', label: '写入寄存器 (register)' }]} /></Field><ValueField key={draft.operation} label="写入值" kind={draft.operation === 'coil' ? 'Bool' : 'Int'} value={draft.value} min={0} max={65535} variables={variables} onChange={value => patch({ value })} /><div className="node-config-help">地址从 0 开始。调试将实际写入 PLC。</div></section>
}

function ValueField({ label, kind, value, variables, onChange, min, max }: { label: string; kind: ValueKind; value?: ValueObject | null; variables: VariableOption[]; onChange: (value: ValueObject | null) => void; min?: number; max?: number }) {
  const [open, setOpen] = useState(false)
  const allowLiteral = !['Region', 'Image', 'ImageArray'].includes(kind)
  const refMode = value?.type === 'ref' || !allowLiteral
  const available = variables.filter(item => item.kind === kind)
  const selected = available.find(item => item.ref === value?.ref)
  const fallback = kind === 'Bool' ? false : kind === 'Text' ? '' : (min ?? 0)
  return <div className="nc-value-field">
    <div className="nc-value-heading"><span>{label}</span>{allowLiteral && <DesignSelect className="nc-value-mode" ariaLabel={`${label}输入方式`} value={refMode ? 'ref' : 'content'} onChange={next => { if (next === 'ref') setOpen(true); else { onChange(literal(fallback)); setOpen(false) } }} options={[{ value: 'content', label: '常量' }, { value: 'ref', label: '变量引用' }]} />}</div>
    {refMode ? <div className="nc-reference-box"><span title={String(value?.ref || '')}>{selected?.label || value?.name || value?.ref || '请选择上游变量'}</span><button type="button" aria-label={`选择${label}引用`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}><img src={linkIcon} alt="" /></button></div>
      : kind === 'Bool' ? <DesignSelect ariaLabel={label} value={String(value?.ref ?? false)} onChange={next => onChange(literal(next === 'true'))} options={[{ value: 'false', label: 'False' }, { value: 'true', label: 'True' }]} />
        : <input aria-label={label} type={kind === 'Text' ? 'text' : 'number'} step={kind === 'Int' ? 1 : 'any'} min={min} max={max} value={value?.ref == null ? '' : String(value.ref)} onChange={event => onChange(kind === 'Text' ? literal(event.target.value) : event.target.value === '' ? null : literal(event.target.valueAsNumber))} />}
    {refMode && value?.ref && !selected && <small className="nc-invalid-ref">引用已失效或类型不匹配，请重新选择。</small>}
    {open && <VariableReferenceDialog label={label} kind={kind} variables={available} value={value} onClose={() => setOpen(false)} onSelect={item => { onChange({ type: 'ref', ref: item.ref, name: item.label }); setOpen(false) }} />}
  </div>
}

function VariableReferenceDialog({ label, kind, variables, value, onClose, onSelect }: {
  label: string; kind: ValueKind; variables: VariableOption[]; value?: ValueObject | null
  onClose: () => void; onSelect: (item: VariableOption) => void
}) {
  const initial = variables.find(item => value?.type === 'ref' && item.ref === value.ref)
  const [sourceId, setSourceId] = useState(initial?.nodeId || variables[0]?.nodeId || '')
  const [selectedRef, setSelectedRef] = useState(initial?.ref || '')
  const dialogRef = useRef<HTMLDivElement>(null)
  const sources = [...new Map(variables.map(item => [item.nodeId, item.nodeName])).entries()]
  const rows = variables.filter(item => item.nodeId === sourceId)
  const selected = rows.find(item => item.ref === selectedRef)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    // Portal 脱离父表单，父窗口暂时不可交互，Escape 只关闭当前引用窗口。
    const underlying = Array.from(document.querySelectorAll<HTMLElement>('.nc-dialog, .nc-item-dialog'))
    const old = underlying.map(element => element.inert)
    underlying.forEach(element => { element.inert = true })
    dialogRef.current?.focus()
    return () => { underlying.forEach((element, i) => { element.inert = old[i] }); previous?.focus() }
  }, [])
  const confirm = () => { if (selected) onSelect(selected) }
  return createPortal(<div className="vr-overlay" onMouseDown={event => { event.stopPropagation(); if (event.target === event.currentTarget) onClose() }} onClick={event => event.stopPropagation()} onKeyDown={event => {
    event.stopPropagation()
    if (event.key === 'Escape') { event.preventDefault(); onClose() }
    if (event.key === 'Tab') {
      const items = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [tabindex="0"]') || [])
      const first = items[0], last = items.at(-1)
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialogRef.current)) { event.preventDefault(); first?.focus() }
    }
  }}>
    <div className="vr-dialog" ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label="变量引用" aria-description={`${label}：请选择 ${kind} 类型的上游变量`}>
      <header className="vr-header"><span>变量引用</span><button type="button" aria-label="关闭变量引用" onClick={onClose}><img src={variableCloseIcon} alt="" /></button></header>
      <div className="vr-content">
        <aside className="vr-sources"><div className="vr-source-title">可链接列表</div><div role="list" aria-label="可链接节点">{sources.map(([id, name]) => <button type="button" key={id} className={id === sourceId ? 'active' : ''} aria-pressed={id === sourceId} title={name} onClick={() => { setSourceId(id); setSelectedRef('') }}>{name}</button>)}</div></aside>
        <div className="vr-table-wrap"><div className="vr-table" role="table" aria-label="可引用变量">
          <div className="vr-table-head" role="row"><span role="columnheader" aria-label="序号" /><span role="columnheader">类型</span><span role="columnheader">名称</span><span role="columnheader">值</span><span role="columnheader">备注</span></div>
          <div className="vr-table-body" role="rowgroup">{rows.map((item, index) => <div key={item.ref} role="row" tabIndex={0} aria-selected={item.ref === selectedRef} className={`vr-row ${item.ref === selectedRef ? 'selected' : ''}`} onClick={() => setSelectedRef(item.ref)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelectedRef(item.ref) } }}>
            <span role="cell">{index + 1}</span><span role="cell"><b className="vr-type">{item.kind === 'Region' ? 'List<Region>' : item.kind}</b></span><span role="cell" title={item.name}>{item.name}</span><span role="cell" title={variableValue(item)}>{variableValue(item)}</span><span role="cell">—</span>
          </div>)}</div>
        </div>{!rows.length && <div className="vr-empty">暂无 {kind === 'Region' ? 'List<Region>' : kind} 类型的上游变量</div>}</div>
      </div>
      <footer className="vr-footer"><button type="button" onClick={onClose}>取消</button><button type="button" className="primary" disabled={!selected} onClick={confirm}>保存</button></footer>
    </div>
  </div>, document.querySelector('.app-shell') || document.body)
}

function variableValue(item: VariableOption): string {
  if (item.value === undefined || item.value === null) return '—'
  if (item.kind === 'Image') return '图像'
  if (item.kind === 'ImageArray') return Array.isArray(item.value) ? `${item.value.length} 张图像` : '图像数组'
  if (item.kind === 'Region') return Array.isArray(item.value) ? `${item.value.length} 个区域` : '区域'
  if (item.kind === 'Bool') return item.value ? 'True' : 'False'
  return String(item.value)
}

function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="nc-field"><span>{label}</span>{children}</label> }
function NumberField({ label, value, onChange, min, max }: { label: string; value: number | null; onChange: (value: number | null) => void; min?: number; max?: number }) { return <Field label={label}><input aria-label={label} type="number" step={1} min={min} max={max} value={value ?? ''} onChange={event => onChange(event.target.value === '' ? null : event.target.valueAsNumber)} /></Field> }
function Tabs({ values, value, onChange }: { values: string[][]; value: string; onChange: (value: string) => void }) { return <div className="nc-tabs">{values.map(([key, title]) => <button type="button" key={key} aria-pressed={value === key} className={value === key ? 'active' : ''} onClick={() => onChange(key)}>{title}</button>)}</div> }
function stepIcon(type: NodeType) { const src = iconAssets.flow[type as keyof typeof iconAssets.flow]; return src ? <img src={src} alt="" {...iconSizes.flow} /> : <span className="node-config-dot" /> }

function collectVariables(node: WorkflowNode, nodes: WorkflowNode[], models: ModelPackage[]): VariableOption[] {
  const currentIndex = nodes.findIndex(item => item.id === node.id)
  if (currentIndex < 0) return []
  // 流程列表为执行顺序；分支内部变量仅在所属分支内可引用。
  const scopes = new Map<string, string[]>()
  const scope: string[] = []
  for (const item of nodes) {
    const branch = item.data.branch
    if (branch?.role === 'end') scope.pop()
    if (branch?.role === 'else' || branch?.role === 'elseIf') scope.pop()
    scopes.set(item.id, [...scope])
    if (branch && ['if', 'else', 'elseIf'].includes(branch.role)) scope.push(item.id)
  }
  const currentScope = scopes.get(node.id) || []
  const upstream = nodes.slice(0, currentIndex).filter(item => (scopes.get(item.id) || []).every((arm, i) => arm === currentScope[i]))
  const result = new Map<string, VariableOption>()
  const add = (item: WorkflowNode, key: string, label: string, type: string) => {
    const kind = kindOf(type)
    if (kind) result.set(`${item.id}.${key}`, { kind, ref: `${item.id}.${key}`, label: `${item.data.label} · ${label}`, nodeId: item.id, nodeName: item.data.label, name: label, value: item.data.outputs?.find(output => output.variable === key)?.value })
  }
  for (const item of upstream) {
    if (item.data.nodeType === 'capture') {
      add(item, 'Image', '图像', 'Image')
      for (const output of item.data.outputs || []) if (output.variable !== 'Image') add(item, output.variable, output.displayName, output.type)
    }
    if (item.data.nodeType === 'model') {
      // 不使用可能属于旧模型的 outputs 缓存，按该节点的 modelId 重建。
      const model = models.find(model => model.model_id === item.data.params.modelId || model.id === item.data.params.modelId)
      for (const task of model?.manifest.tasks || []) for (const label of task.labels || []) {
        const prefix = `${task.key}:${label.label}`
        add(item, `${prefix}:detected`, `${task.name} · ${label.name}检出`, 'Bool')
        add(item, `${prefix}:count`, `${task.name} · ${label.name}数量`, 'Int')
        add(item, `${prefix}:regions`, `${task.name} · ${label.name}区域`, 'List<Region>')
      }
    }
  }
  return [...result.values()]
}

function validateDraft(type: NodeType, p: Params, label: string, variables: VariableOption[], models: ModelPackage[], cameras: Camera[]): string | null {
  if (!label.trim()) return '请输入节点名称'
  const number = (value: any, name: string, min = 0, max = Infinity, integer = true) => typeof value !== 'number' || !Number.isFinite(value) || (integer && !Number.isInteger(value)) || value < min || value > max ? `${name}必须为 ${min}～${max === Infinity ? '有效上限' : max} 范围内的${integer ? '整数' : '数字'}` : null
  const value = (v: any, kind: ValueKind, name: string, min?: number, max?: number): string | null => {
    if (!v || !['ref', 'content'].includes(v.type)) return `请设置${name}`
    if (v.type === 'ref') return variables.some(row => row.ref === v.ref && row.kind === kind) ? null : `${name}引用不存在或类型不匹配`
    if (kind === 'Bool') return typeof v.ref === 'boolean' ? null : `${name}必须为 Bool`
    if (kind === 'Text') return typeof v.ref === 'string' ? null : `${name}必须为 Text`
    if (kind === 'Int' || kind === 'Float') return number(v.ref, name, min ?? -Infinity, max, kind === 'Int')
    return `${name}必须引用上游${kind}变量`
  }
  if (type === 'capture') {
    if (p.source === 'file') return p.file?.path?.trim() ? null : '请选择图片或输入图片路径'
    if (p.source === 'dir') {
      if (!p.dir?.path?.trim()) return '请输入目录路径'
      if (!rowsOf(p.dir.files).some(file => file.selected !== false && file.path)) return '请扫描目录并至少选用一张图片'
      if (p.dir.autoSwitch) return number(p.dir.intervalUs, '切换间隔', 1)
      return null
    }
    if (p.source !== 'camera') return '请选择图像源'
    const c = p.camera
    if (!cameras.some(camera => camera.id === c.cameraId && camera.connected)) return '请选择已连接的相机'
    if (!['soft', 'hardware', 'continuous'].includes(c.triggerMode)) return '请选择有效的采集模式'
    if (c.timeout.enabled) { const error = number(c.timeout.valueUs, '采集超时', 1); if (error) return error }
    for (const key of ['exposure', 'gain', 'line']) if (c[key].enabled) { const error = value(c[key].value, key === 'line' ? 'Int' : 'Float', { exposure: '曝光时间', gain: '增益', line: '行数' }[key]!, key === 'gain' ? 0 : 1); if (error) return error }
  }
  if (type === 'model') {
    const model = models.find(model => model.model_id === p.modelId)
    if (!model) return '请选择有效模型'
    const error = value(p.image, 'Image', '输入图像'); if (error) return error
    for (const param of model.manifest.params || []) {
      if (['modelId', 'image', 'lable', 'label'].includes(param.name)) continue
      const kind = kindOf(param.type)
      if (!kind) return `不支持的模型参数类型：${param.type}`
      if (p[param.name] == null && param.required === false) continue
      const error = value(p[param.name], kind, param.label || param.name, param.min, param.max); if (error) return error
    }
  }
  if (type === 'branch') for (const [index, condition] of rowsOf(p.conditions).entries()) { const error = value(condition.value, 'Bool', `条件 ${index + 1}`); if (error) return error }
  if (type === 'display') {
    const imageError = value(p.image, 'Image', '输入图像'); if (imageError) return imageError
    for (const group of ['shapes', 'texts']) for (const [index, row] of rowsOf(p[group]).entries()) {
      const name = `${group === 'shapes' ? '图形' : '文字'} ${index + 1}`
      if (group === 'texts' && row.content?.type === 'content' && row.content.ref === '') return `请输入${name}显示内容`
      for (const [key, kind] of [['content', group === 'shapes' ? 'Region' : 'Text'], ['visible', 'Bool'], ['status', 'Bool']] as [string, ValueKind][]) { const error = value(row[key], kind, `${name} ${key}`); if (error) return error }
      if (group === 'shapes') {
        if (!/^#[\da-f]{6}$/i.test(row.style.okColor) || !/^#[\da-f]{6}$/i.test(row.style.ngColor)) return `${name}颜色格式错误`
        const error = number(row.style.lineWidth, `${name}轮廓宽度`, 1); if (error) return error
      } else {
        for (const axis of ['x', 'y']) { const error = value(row[axis], 'Int', `${name}位置 ${axis}`, 0); if (error) return error }
        const error = number(row.fontSize, `${name}字体大小`, 1); if (error) return error
      }
    }
  }
  if (type === 'plc') {
    if (typeof p.host !== 'string' || !p.host.trim() || /[\s/:]/.test(p.host)) return '请输入主机名或 IPv4 地址，不含协议和端口'
    for (const [key, min, max] of [['port', 1, 65535], ['unitId', 0, 255], ['address', 0, 65535]] as [string, number, number][]) { const error = number(p[key], key, min, max); if (error) return error }
    if (p.operation === 'coil') return value(p.value, 'Bool', '线圈值')
    if (p.operation === 'register') return value(p.value, 'Int', '寄存器值', 0, 65535)
    return '请选择 PLC 操作类型'
  }
  return null
}
