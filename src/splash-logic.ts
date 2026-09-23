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

export const SPLASH_MIN_DURATION_MS = 6000

export const SPLASH_MESSAGES = [
  '正在启动服务…',
  '正在读取运行配置…',
  '正在初始化数据环境…',
  '正在准备视觉引擎…',
  '正在初始化推理引擎…',
  '正在加载推理组件…',
  '正在启动视觉引擎…',
  '正在检测计算能力…',
  '正在校验模型运行环境…',
  '正在建立本地通信…',
  '正在准备图像处理链…',
  '正在加载视觉分析模块…',
  '正在同步设备状态…',
  '正在完成运行环境检查…',
  '视觉引擎即将就绪…',
]

export function getSplashWaitMs(elapsedMs: number): number {
  return Math.max(0, SPLASH_MIN_DURATION_MS - elapsedMs)
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
