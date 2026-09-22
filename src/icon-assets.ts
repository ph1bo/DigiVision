/**
 * 高保稿导出的图标资源（2x PNG，直接来自 MasterGo「导出图片」）。
 * 名称沿用导出文件名：toolbar = 顶部工具栏，module = 模块卡插画。
 */
import logo from './assets/icons/logo.png'
import toolbarCamera from './assets/icons/toolbar/icon-camera.png'
import toolbarDivider from './assets/icons/toolbar/divider.png'
import toolbarHelp from './assets/icons/toolbar/icon-help.png'
import toolbarLayout from './assets/icons/toolbar/icon-layout.png'
import toolbarModel from './assets/icons/toolbar/icon-model.png'
import toolbarPlc from './assets/icons/toolbar/icon-plc.png'
import toolbarSetting from './assets/icons/toolbar/icon-setting.png'
import moduleCollect from './assets/icons/module/icon-collect.png'
import moduleCondition from './assets/icons/module/icon-condition.png'
import moduleDisplay from './assets/icons/module/icon-display.png'
import moduleModel from './assets/icons/module/icon-model.png'
import modulePlc from './assets/icons/module/icon-plc.png'
import flowBranch from './assets/flow-icons/branch.svg'
import flowCapture from './assets/flow-icons/capture.svg'
import flowCollapse from './assets/flow-icons/collapse.svg'
import flowDisplay from './assets/flow-icons/display.svg'
import flowEnd from './assets/flow-icons/end.svg'
import flowModel from './assets/flow-icons/model.svg'
import flowPlc from './assets/flow-icons/plc.png'

export const iconAssets = {
  logo,
  toolbar: {
    divider: toolbarDivider,
    layout: toolbarLayout,
    camera: toolbarCamera,
    model: toolbarModel,
    plc: toolbarPlc,
    help: toolbarHelp,
    setting: toolbarSetting,
  },
  module: {
    collect: moduleCollect,
    condition: moduleCondition,
    display: moduleDisplay,
    model: moduleModel,
    plc: modulePlc,
  },
  /** 流程栏节点图标：MasterGo 导出的 16×16 SVG（plc 由模块插画裁切）。 */
  flow: {
    capture: flowCapture,
    model: flowModel,
    branch: flowBranch,
    display: flowDisplay,
    plc: flowPlc,
    end: flowEnd,
    collapse: flowCollapse,
  },
}

/** 图标按原始 CSS 尺寸使用（导出为 2x）。 */
export const iconSizes = {
  toolbar: { width: 20, height: 20 },
  toolbarDivider: { width: 2, height: 26 },
  module: { width: 82, height: 53 },
  flow: { width: 16, height: 16 },
  flowCollapse: { width: 12, height: 12 },
  logo: { width: 22, height: 21 },
}
