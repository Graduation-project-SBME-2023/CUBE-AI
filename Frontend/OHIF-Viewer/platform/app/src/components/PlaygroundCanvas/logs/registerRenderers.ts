import { registerLogRenderer } from './LogRendererRegistry'
// import { MarkdownLog } from './renderers/MarkdownLog'
import { ProgressLog } from './renderers/ProgressLog'
import { TextLog } from './renderers/TextLog'

export function registerAllLogRenderers() {
  // registerLogRenderer('markdown', MarkdownLog)
  registerLogRenderer('progress', ProgressLog)
  registerLogRenderer('text', TextLog)
}
