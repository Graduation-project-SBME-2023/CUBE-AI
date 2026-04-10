import React from 'react'
import ReactMarkdown from 'react-markdown'

export function MarkdownLog({ data }: { data: { content: string } }) {
  return (
    <div className="prose prose-invert max-w-none text-xs">
      <ReactMarkdown>{data.content}</ReactMarkdown>
    </div>
  )
}