import { registerAllLogRenderers } from './components/PlaygroundCanvas/logs/registerRenderers';
import { initResearchNotificationService } from './utils/ResearchNotificationService';

export default function preRegistration({ servicesManager }: any): void {
  registerAllLogRenderers();
  
  initResearchNotificationService(servicesManager);
}
