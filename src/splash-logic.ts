export type SplashTone = 'pending' | 'success' | 'warning' | 'error'

export interface RuntimeCapabilities {
  database: { ready: boolean; path?: string }
  inference: {
    version?: string | null
    availableProviders?: string[]
    recommendedProvider?: string | null
    cpuAvailable: boolean
    accelerated?: boolean
    error?: string
  }
  camera: { vendors: Array<{ id: string; name?: string; available: boolean; error?: string }> }
}

export interface SplashStep {
  label: string
  tone: SplashTone
  detail: string
}

export interface SplashSummary {
  blocked: boolean
  progress: number
  mode: string
  steps: {
    backend: SplashStep
    database: SplashStep
    inference: SplashStep
    camera: SplashStep
  }
}

export function summarizeCapabilities(capabilities: RuntimeCapabilities): SplashSummary {
  const databaseReady = capabilities.database?.ready === true
  const inference = capabilities.inference ?? { cpuAvailable: false }
  const cpuReady = inference.cpuAvailable === true
  const vendor = capabilities.camera?.vendors?.find(item => item.id === 'hikvision')
  const cameraReady = vendor?.available === true
  const steps = {
    backend: { label: '启动本地服务', tone: 'success' as const, detail: '本地服务已就绪' },
    database: {
      label: '初始化数据',
      tone: databaseReady ? 'success' as const : 'error' as const,
      detail: databaseReady ? 'SQLite 已就绪' : 'SQLite 初始化失败',
    },
    inference: {
      label: '检测推理引擎',
      tone: cpuReady ? 'success' as const : 'error' as const,
      detail: cpuReady
        ? inference.accelerated
          ? `${inference.recommendedProvider} 已启用`
          : '使用 CPU 模式'
        : 'CPU Provider 不可用',
    },
    camera: {
      label: '检测相机环境',
      tone: cameraReady ? 'success' as const : 'warning' as const,
      detail: cameraReady ? 'MVS SDK 已就绪' : '未检测到 MVS SDK，可稍后安装',
    },
  }
  const completed = Object.values(steps).filter(step => step.tone === 'success' || step.tone === 'warning').length
  return {
    blocked: steps.database.tone === 'error' || steps.inference.tone === 'error',
    progress: Math.round(completed / Object.keys(steps).length * 100),
    mode: inference.accelerated ? String(inference.recommendedProvider) : 'CPU',
    steps,
  }
}
