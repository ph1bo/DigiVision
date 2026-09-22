import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, imageUrl } from '../api'
import cameraManageIcon from '../assets/mastergo/camera-manage.svg'
import chevronDownIcon from '../assets/mastergo/chevron-down.svg'
import closeIcon from '../assets/mastergo/close.svg'
import linkIcon from '../assets/mastergo/link.svg'
import trashIcon from '../assets/mastergo/trash.svg'
import unlinkIcon from '../assets/mastergo/unlink.svg'
import type { Camera, CameraDevice } from '../types'

interface Vendor {
  id: string
  name: string
  sdk: string
  sdkVersion: string
  available: boolean
  error: string
}

export function CameraPage() {
  const navigate = useNavigate()
  const [cameras, setCameras] = useState<Camera[]>([])
  const [vendors, setVendors] = useState<Vendor[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [vendor, setVendor] = useState('hikvision')
  const [devices, setDevices] = useState<CameraDevice[]>([])
  const [selectedDevice, setSelectedDevice] = useState('')
  const [previews, setPreviews] = useState<Record<string, string>>({})
  const [message, setMessage] = useState('')
  const [pending, setPending] = useState(false)
  const [edits, setEdits] = useState<Record<string, { name?: string; remark?: string }>>({})
  const [loading, setLoading] = useState(false)

  const selected = useMemo(() => cameras.find((camera) => camera.id === selectedId) || cameras[0], [cameras, selectedId])
  const selectedVendor = vendors.find((item) => item.id === vendor)
  const preview = selected ? (selected.connected ? previews[selected.id] || null : null) : null

  const refresh = useCallback(async () => {
    const [cameraList, vendorList] = await Promise.all([api.get<Camera[]>('/cameras'), api.get<Vendor[]>('/cameras/vendors')])
    setCameras(cameraList)
    setVendors(vendorList)
    setSelectedId((current) => cameraList.some((item) => item.id === current) ? current : cameraList[0]?.id || '')
  }, [])

  const discover = useCallback(async (vendorId: string) => {
    setLoading(true)
    try {
      const result = await api.post<{ devices: CameraDevice[]; sdkAvailable: boolean; error: string }>('/cameras/discover', { vendor: vendorId })
      setDevices(result.devices)
      if (result.error) setMessage(result.error)
      setSelectedDevice((current) => {
        if (result.devices.some((device) => deviceKey(device) === current && !device.added)) return current
        const first = result.devices.find((device) => !device.added)
        return first ? deviceKey(first) : ''
      })
    } catch (error) {
      setDevices([])
      setSelectedDevice('')
      setMessage((error as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh().catch((error) => setMessage((error as Error).message))
  }, [refresh])

  useEffect(() => {
    discover(vendor)
  }, [discover, vendor])

  const addDevice = async () => {
    const device = devices.find((item) => deviceKey(item) === selectedDevice)
    if (!device) return
    const index = cameras.filter((item) => item.name.startsWith('Camera')).length + 1
    setPending(true)
    try {
      const created = await api.post<Camera>('/cameras', {
      name: `Camera${index}`,
      vendor,
      model: device.model,
      serial: device.serial,
      interface: device.interface,
      ip_address: device.ip_address,
      remark: `${device.serial}_${device.interface.toUpperCase()}`,
    })
    await refresh()
    await discover(vendor)
    setSelectedId(created.id)
    setMessage(`已添加 ${device.model}`)
    } catch (error) { setMessage((error as Error).message) } finally { setPending(false) }
  }

  const grabCamera = useCallback(async (camera: Camera) => {
    const result = await api.post<{ imageUrl: string; width: number; height: number }>(`/cameras/${camera.id}/grab`)
    setPreviews((current) => ({ ...current, [camera.id]: imageUrl(result.imageUrl) }))
    return result
  }, [])

  const toggleConnection = async (camera: Camera) => {
    setPending(true)
    try {
      if (camera.connected) {
        await api.post(`/cameras/${camera.id}/disconnect`)
        setMessage('相机已断开')
      } else {
        await api.post(`/cameras/${camera.id}/connect`)
        await grabCamera(camera)
        setMessage('相机已连接并取图')
      }
      await refresh()
      setSelectedId(camera.id)
    } catch (error) {
      setMessage((error as Error).message)
    } finally { await refresh().catch(() => {}); setPending(false) }
  }

  const selectCamera = async (camera: Camera) => {
    setSelectedId(camera.id)
    if (camera.connected && !previews[camera.id]) {
      try {
        await grabCamera(camera)
      } catch (error) {
        setMessage((error as Error).message)
      }
    }
  }

  const removeCamera = async (camera: Camera) => {
    if (!window.confirm(`确认删除 ${camera.name}？`)) return
    try {
      await api.delete(`/cameras/${camera.id}`)
      setSelectedId('')
      await refresh()
      await discover(vendor)
    } catch (error) {
      setMessage((error as Error).message)
    }
  }

  const save = async () => {
    setPending(true)
    try {
      for (const [id, patch] of Object.entries(edits)) {
        if (patch.name !== undefined && !patch.name.trim()) throw new Error('相机名称不能为空')
        await api.put(`/cameras/${id}`, patch)
      }
      await refresh()
      navigate('/')
    } catch (error) {
      setMessage((error as Error).message)
    } finally { setPending(false) }
  }

  return (
    <div className="management-overlay">
      <section className="management-dialog management-camera-dialog" aria-label="相机管理">
        <header className="management-dialog-header">
          <div className="management-dialog-title">
            <img src={cameraManageIcon} alt="" />
            <span>相机管理</span>
          </div>
          <button className="management-dialog-close" onClick={() => navigate('/')} aria-label="关闭">
            <img src={closeIcon} alt="" />
          </button>
        </header>

        <div className="management-camera-main">
          <div className="management-camera-left">
            <section className="camera-device-info">
              <h3>设备信息</h3>
              <div className="camera-device-form">
                <label>
                  <span>相机厂商</span>
                  <div className="mg-select">
                    <select value={vendor} onChange={(event) => setVendor(event.target.value)}>
                      {vendors.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
                    </select>
                    <img src={chevronDownIcon} alt="" />
                  </div>
                </label>
                <label>
                  <span>相机列表</span>
                  <div className="mg-select">
                    <select value={selectedDevice} disabled={loading} onChange={(event) => setSelectedDevice(event.target.value)}>
                      <option value="">请选择</option>
                      {devices.map((device) => (
                        <option value={deviceKey(device)} disabled={device.added} key={deviceKey(device)}>
                          {device.model} · {device.serial}{device.ip_address ? ` · ${device.ip_address}` : ''}
                        </option>
                      ))}
                    </select>
                    <img src={chevronDownIcon} alt="" />
                  </div>
                </label>
                <label>
                  <span>SDK版本</span>
                  <input readOnly value={selectedVendor?.sdkVersion || '选择相机后自动带出'} />
                </label>
                <button className="mg-secondary-button camera-add-button" onClick={addDevice} disabled={!selectedDevice || loading || pending}>添加至列表</button>
              </div>
            </section>

            <section className="camera-list-section">
              <h3>设备列表</h3>
              <div className="mg-table camera-management-table">
                <div className="mg-table-head">
                  <span>名称</span><span>SN</span><span>连接状态</span><span>备注</span><span>操作</span>
                </div>
                <div className="mg-table-body">
                  {cameras.map((camera) => (
                    <div
                      className={`mg-table-row ${selected?.id === camera.id ? 'selected' : ''}`}
                      key={camera.id}
                      onClick={() => selectCamera(camera)}
                    >
                      <input className="mg-cell-input" aria-label="相机名称" value={edits[camera.id]?.name ?? camera.name} onClick={event => event.stopPropagation()} onChange={event => setEdits(current => ({ ...current, [camera.id]: { ...current[camera.id], name: event.target.value } }))} />
                      <span>{camera.serial}</span>
                      <span className={camera.connected ? 'mg-connected' : 'mg-disconnected'}>
                        <i />{camera.connected ? 'true' : 'false'}
                      </span>
                      <input className="mg-cell-input" aria-label="相机备注" value={edits[camera.id]?.remark ?? camera.remark} onClick={event => event.stopPropagation()} onChange={event => setEdits(current => ({ ...current, [camera.id]: { ...current[camera.id], remark: event.target.value } }))} />
                      <span className="mg-row-actions">
                        <button
                          disabled={pending}
                          title={camera.connected ? '断开相机' : '连接相机'}
                          onClick={(event) => { event.stopPropagation(); toggleConnection(camera) }}
                        >
                          <img src={camera.connected ? unlinkIcon : linkIcon} alt="" />
                        </button>
                        <button
                          disabled={pending || camera.connected} title="删除"
                          onClick={(event) => { event.stopPropagation(); removeCamera(camera) }}
                        >
                          <img src={trashIcon} alt="" />
                        </button>
                      </span>
                    </div>
                  ))}
                  {cameras.length === 0 && <div className="management-empty-row">暂无相机</div>}
                </div>
              </div>
            </section>
          </div>

          <section className="camera-preview-screen">
            {preview ? (
              <img src={preview} alt="相机预览" />
            ) : (
              <div className="camera-preview-empty">
                <img src={cameraManageIcon} alt="" />
                <span>{selected ? (selected.connected ? '点击采集图像获取预览' : '相机未连接') : '请选择相机'}</span>
              </div>
            )}
          </section>
        </div>

        <footer className="management-dialog-footer">
          <button onClick={() => discover(vendor)} disabled={loading || pending}>刷新设备</button>
          <button disabled={!selected?.connected || pending} onClick={() => selected && grabCamera(selected).catch(error => setMessage(error.message))}>采集图像</button>
          <button onClick={() => navigate('/')}>取消</button>
          <button className="mg-primary-button" disabled={pending} onClick={save}>保存</button>
        </footer>

        {message && <button className="management-toast" onClick={() => setMessage('')}>{message}</button>}
      </section>
    </div>
  )
}

function deviceKey(device: CameraDevice) {
  return `${device.serial}:${device.interface}`
}
