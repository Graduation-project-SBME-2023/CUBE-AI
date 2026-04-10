import { registerAllLogRenderers } from './components/PlaygroundCanvas/logs/registerRenderers';

export default function preRegistration() {
  registerAllLogRenderers();
}
