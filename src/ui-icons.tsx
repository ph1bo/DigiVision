import type { IIconProps } from '@icon-park/react/es/runtime'
import type { CSSProperties } from 'react'
import {
  IconAdd,
  IconArrowRight,
  IconBoard,
  IconCamera,
  IconCaretDown,
  IconCaretUpDown,
  IconCheck,
  IconClose,
  IconFlow,
  IconGear,
  IconLink,
  IconLoading,
  IconModel,
  IconMore,
  IconPlay,
  IconSearch,
  IconStepForward,
  IconStop,
  IconTrash,
  IconUnlink,
  IconUsers,
} from './design-icons'
import {
  Branch as IpBranch,
  Copy as IpCopy,
  Cpu as IpCpu,
  DatabaseSetting as IpDatabase,
  Download as IpDownload,
  FileZip as IpFileZip,
  Filter as IpFilter,
  FolderOpen as IpFolderOpen,
  FolderPlus as IpFolderPlus,
  LinkOut as IpLinkOut,
  Minus as IpMinus,
  Pause as IpPause,
  Power as IpPower,
  PreviewOpen as IpPreviewOpen,
  Save as IpSave,
  Square as IpSquare,
  Upload as IpUpload,
  VolumeNotice as IpVolumeNotice,
} from '@icon-park/react'

type DesignIconProps = { size?: number; className?: string; style?: CSSProperties }

function wrap(Icon: (props: IIconProps) => React.ReactElement) {
  return function HfIcon(props: IIconProps) {
    return <Icon theme="outline" strokeWidth={2} size={16} fill="currentColor" {...props} />
  }
}

function flip(Icon: (props: DesignIconProps) => React.ReactElement, degrees: number) {
  return function FlippedIcon(props: DesignIconProps) {
    return <Icon {...props} style={{ ...props.style, transform: `rotate(${degrees}deg)` }} />
  }
}

/* ---- 1:1 取自主 MasterGo 高保稿导出的原始 SVG ---- */
export const Close = IconClose
export const X = IconClose
export const Plus = IconAdd
export const Check = IconCheck
export const CheckCircle2 = IconCheck
export const Play = IconPlay
export const StepForward = IconStepForward
export const Step = IconStepForward
export const Stop = IconStop
export const SquareStop = IconStop
export const ArrowRight = IconArrowRight
export const Delete = IconTrash
export const Trash2 = IconTrash
export const Search = IconSearch
export const More = IconMore
export const MoreHorizontal = IconMore
export const ChevronDown = IconCaretDown
export const ChevronUp = flip(IconCaretDown, 180)
export const Up = IconCaretUpDown
export const Down = IconCaretDown
export const Link = IconLink
export const Link2 = IconLink
export const PlugZap = IconLink
export const LinkBreak = IconUnlink
export const Unlink = IconUnlink
export const Unplug = IconUnlink
export const Refresh = IconLoading
export const RefreshCw = IconLoading
export const RotateCcw = IconLoading
export const Loading = IconLoading
export const Camera = IconCamera
export const CameraIcon = IconCamera
export const Video = IconCamera
export const Videocamera = IconCamera
export const User = IconUsers
export const UserRound = IconUsers
export const Workbench = IconBoard
export const Workflow = IconBoard
export const SettingTwo = IconGear
export const Settings2 = IconGear
export const Brain = IconModel
export const Sparkles = IconModel
export const GitBranch = IconFlow
export const Cube = IconFlow
export const Boxes = IconFlow

/* ---- 高保稿未覆盖的图标，暂用 IconPark 线性图标 ---- */
export const Branch = wrap(IpBranch)
export const Construction = wrap(IpPower)
export const Copy = wrap(IpCopy)
export const Cpu = wrap(IpCpu)
export const Database = wrap(IpDatabase)
export const Download = wrap(IpDownload)
export const FileZip = wrap(IpFileZip)
export const FileArchive = wrap(IpFileZip)
export const Filter = wrap(IpFilter)
export const ListFilter = wrap(IpFilter)
export const FolderOpen = wrap(IpFolderOpen)
export const FolderPlus = wrap(IpFolderPlus)
export const GoStart = wrap(IpPower)
export const LinkOut = wrap(IpLinkOut)
export const ExternalLink = wrap(IpLinkOut)
export const Minus = wrap(IpMinus)
export const Pause = wrap(IpPause)
export const Power = wrap(IpPower)
export const PreviewOpen = wrap(IpPreviewOpen)
export const MonitorPlay = wrap(IpPreviewOpen)
export const Save = wrap(IpSave)
export const Square = wrap(IpSquare)
export const Upload = wrap(IpUpload)
export const UploadCloud = wrap(IpUpload)
export const VolumeNotice = wrap(IpVolumeNotice)
export const Volume2 = wrap(IpVolumeNotice)
