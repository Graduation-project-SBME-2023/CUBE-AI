import { ComponentType } from 'react'
import { ToolContent, ToolEvent } from '../types'

export type LogRendererProps<T = any> = {
  data: T
}

type Registry = {
  [K in ToolContent['content_type']]: ComponentType<{ data: any }>
};

export const logRendererRegistry: Partial<Registry> = {}

export function registerLogRenderer<T extends ToolEvent['content_type']>(
  type: T,
  component: ComponentType<Extract<ToolEvent, { type: T }>['content']>
) {
  logRendererRegistry[type] = component
}
