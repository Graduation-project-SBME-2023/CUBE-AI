import React from 'react'
import { ToolEvent, ToolLog } from '../types'
import { logRendererRegistry } from './LogRendererRegistry'

export function AgentLogRenderer({ log }: { log: ToolLog }) {
  const eventToRender = log.events[log.events.length - 1];
  if (!eventToRender) {
    return null;
  }
  
  const Component = logRendererRegistry[eventToRender.content_type]

  if (!Component) {
    return (
      <div className="text-red-400 text-xs">
        Unknown log type: {eventToRender.content_type}
      </div>
    )
  }

  return <Component data={eventToRender.content} />
}
