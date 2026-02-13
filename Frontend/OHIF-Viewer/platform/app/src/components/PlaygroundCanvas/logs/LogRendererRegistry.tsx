import { ComponentType } from 'react'
import { AgentLogEvent } from '../types'

export type LogRendererProps<T = any> = {
  data: T
}

type Registry = {
  [K in AgentLogEvent['type']]: ComponentType<any>
}

export const logRendererRegistry: Partial<Registry> = {}

export function registerLogRenderer<T extends AgentLogEvent['type']>(
  type: T,
  component: ComponentType<Extract<AgentLogEvent, { type: T }>['data']>
) {
  logRendererRegistry[type] = component
}
