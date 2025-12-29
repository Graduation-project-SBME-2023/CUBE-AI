import React from 'react'
import { ToolEvent } from '../types'
import { logRendererRegistry } from './LogRendererRegistry'

export function AgentLogRenderer({ log }: { log: ToolEvent }) {
  const Component = logRendererRegistry[log.content_type]

  if (!Component) {
    return (
      <div className="text-red-400 text-xs">
        Unknown log type: {log.content_type}
      </div>
    )
  }

  return <Component data={log.content} />
}
