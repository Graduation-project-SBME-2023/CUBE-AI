import { UINotificationService, ServicesManager } from '@ohif/core';
import websocketService from './WebsocketService';

export type ResearchEventType = 
  | 'literature_complete'
  | 'data_complete'
  | 'segmentation_complete'
  | 'features_complete'
  | 'modeling_complete'
  | 'reporting_complete'
  | 'study_complete';

export interface NotificationOptions {
  title: string;
  message: string;
  type: 'info' | 'success' | 'warning' | 'error';
  duration?: number;
}

const PHASE_MESSAGES: Record<ResearchEventType, NotificationOptions> = {
  literature_complete: {
    title: 'Literature Review Complete',
    message: 'Literature analysis and source mapping finished.',
    type: 'success',
  },
  data_complete: {
    title: 'Cohort Selection Complete',
    message: 'Population cohort has been finalized.',
    type: 'success',
  },
  segmentation_complete: {
    title: 'Segmentation QC Complete',
    message: 'All segmentations have been approved.',
    type: 'success',
  },
  features_complete: {
    title: 'Feature Extraction Complete',
    message: 'Radiomics features have been computed.',
    type: 'success',
  },
  modeling_complete: {
    title: 'Modeling Complete',
    message: 'Model training and validation finished.',
    type: 'success',
  },
  reporting_complete: {
    title: 'Paper Writing Complete',
    message: 'Research report has been generated.',
    type: 'success',
  },
  study_complete: {
    title: 'Study Complete',
    message: 'All research phases finished successfully!',
    type: 'success',
    duration: 8000,
  },
};

interface AgentStatus {
  name: string;
  status: 'RUNNING' | 'COMPLETED' | 'PENDING' | 'FAILED' | string;
  started_at?: string | null;
  finished_at?: string | null;
}

class ResearchNotificationService {
  private notification: UINotificationService;
  private unsubscribers: (() => void)[] = [];
  private activeNotificationIds: Map<string, string> = new Map();
  private completedPhases: Set<string> = new Set();
  private isStudyComplete: boolean = false;
  private enabled: boolean = true;
  private wsUnsubscribe: (() => void) | null = null;
  private wsStatusUnsubscribe: (() => void) | null = null;
  private previousAgentStatuses: Map<string, string> = new Map();
  private isDisconnectedNotificationShown: boolean = false;

  constructor(notificationService?: UINotificationService) {
    this.notification = notificationService || new UINotificationService();
  }

  static create(servicesManager?: ServicesManager): ResearchNotificationService {
    const notificationService = servicesManager?.services?.uiNotificationService;
    return new ResearchNotificationService(notificationService);
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
    if (!enabled) {
      this.clearAllNotifications();
    }
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  private showNotification(key: string, options: NotificationOptions): void {
    if (!this.enabled) {
      return;
    }

    const existingId = this.activeNotificationIds.get(key);
    if (existingId) {
      this.notification.hide(existingId);
    }

    const id = this.notification.show({
      title: options.title,
      message: options.message,
      type: options.type,
      duration: options.duration || 5000,
      position: 'bottomRight',
      autoClose: true,
    });

    this.activeNotificationIds.set(key, id);
  }

  private handleAgentStatus(agents: AgentStatus[]): void {
    if (!Array.isArray(agents)) return;

    agents.forEach(agent => {
      const prevStatus = this.previousAgentStatuses.get(agent.name);
      const currentStatus = agent.status;

      if (prevStatus !== currentStatus) {
        this.previousAgentStatuses.set(agent.name, currentStatus);

        if (currentStatus === 'COMPLETED') {
          const phaseKey = agent.name.toLowerCase().replace(/\s+/g, '_');
          if (!this.completedPhases.has(phaseKey)) {
            this.completedPhases.add(phaseKey);
            const phaseNotification = this.getPhaseNotification(phaseKey);
            if (phaseNotification) {
              this.showNotification(phaseKey, phaseNotification);
            }
          }
        } else if (currentStatus === 'FAILED') {
          this.showNotification(`error_${agent.name}`, {
            title: 'Agent Failed',
            message: `Agent "${agent.name}" encountered an error.`,
            type: 'error',
          });
        } else if (currentStatus === 'RUNNING') {
          this.showNotification(`running_${agent.name}`, {
            title: 'Agent Started',
            message: `Agent "${agent.name}" is now running.`,
            type: 'info',
            duration: 3000,
          });
        }
      }
    });
  }

  private getPhaseNotification(phaseKey: string): NotificationOptions | null {
    const phaseMap: Record<string, ResearchEventType> = {
      literature_review: 'literature_complete',
      literature: 'literature_complete',
      population: 'data_complete',
      data: 'data_complete',
      cohort: 'data_complete',
      segmentation: 'segmentation_complete',
      segment: 'segmentation_complete',
      features: 'features_complete',
      radiomics: 'features_complete',
      modeling: 'modeling_complete',
      model: 'modeling_complete',
      report: 'reporting_complete',
      reporting: 'reporting_complete',
    };

    const eventType = phaseMap[phaseKey];
    if (eventType) {
      return PHASE_MESSAGES[eventType];
    }
    return null;
  }

  private handleToolEvent(type: string, payload: any): void {
    if (type === 'tool_started') {
      this.showNotification(`tool_${payload.source}`, {
        title: 'Tool Started',
        message: `Tool "${payload.source}" has started.`,
        type: 'info',
        duration: 3000,
      });
    } else if (type === 'tool_finished') {
      this.showNotification(`tool_${payload.source}`, {
        title: 'Tool Completed',
        message: `Tool "${payload.source}" finished.`,
        type: 'success',
        duration: 3000,
      });
    } else if (type === 'error') {
      this.showNotification(`error_tool_${payload.source}`, {
        title: 'Tool Error',
        message: `Tool "${payload.source}" encountered an error.`,
        type: 'error',
      });
    }
  }

  private handleWebSocketMessage = (data: any): void => {
    if (!this.enabled) return;
    
    console.log('[ResearchNotificationService] Received WS message:', data);
    
    const { type, payload } = data || {};

    if (type === 'study_complete' || type === 'execution_complete') {
      this.isStudyComplete = true;
      this.showNotification('study_complete', PHASE_MESSAGES.study_complete);
      this.completedPhases.add('study');
      return;
    }

    if (type === 'Pipeline_agent_status' || type === 'pipeline_agent_status') {
      const agents = payload?.agents || [];
      this.handleAgentStatus(agents);
      return;
    }

    if (type === 'run_finished') {
      this.isStudyComplete = true;
      this.showNotification('study_complete', PHASE_MESSAGES.study_complete);
      this.completedPhases.add('study');
      return;
    }

    if (type === 'run_status') {
      this.showNotification('run_status', {
        title: 'Run Status',
        message: `Current agent: ${payload?.current_agent || 'Unknown'}`,
        type: 'info',
        duration: 2000,
      });
      return;
    }

    if (['tool_started', 'tool_progress', 'tool_log', 'tool_finished', 'error'].includes(type)) {
      this.handleToolEvent(type, payload);
      return;
    }

    const phaseCompletions: Record<string, ResearchEventType> = {
      literature_output: 'literature_complete',
      population_output: 'data_complete',
      segmentation_output: 'segmentation_complete',
      features_output: 'features_complete',
      modeling_output: 'modeling_complete',
      report_output: 'reporting_complete',
    };

    const eventType = phaseCompletions[type];
    if (eventType && (payload?.status === 'complete' || payload?.status === 'completed')) {
      const phase = eventType.replace('_complete', '');
      if (!this.completedPhases.has(phase)) {
        this.completedPhases.add(phase);
        this.showNotification(eventType, PHASE_MESSAGES[eventType]);
      }
    }
  };

  private handleWebSocketStatusChange = (isConnected: boolean): void => {
    if (isConnected && this.enabled) {
      console.log('[ResearchNotificationService] WebSocket reconnected, service active');
      this.isDisconnectedNotificationShown = false;
      this.showNotification('websocket_connected', {
        title: 'Connected',
        message: 'Research service connected.',
        type: 'success',
        duration: 3000,
      });
    } else if (!isConnected && !this.isDisconnectedNotificationShown) {
      this.isDisconnectedNotificationShown = true;
      this.showNotification('websocket_disconnected', {
        title: 'Disconnected',
        message: 'Research service disconnected. Reconnecting...',
        type: 'warning',
        duration: 0, // keep it visible until reconnected
      });
    }
  };

  start(): void {
    if (this.wsUnsubscribe) {
      return;
    }

    console.log('[ResearchNotificationService] Starting, connecting to:', websocketService['url']);
    
    websocketService.connect();
    this.wsUnsubscribe = websocketService.subscribe(this.handleWebSocketMessage);
    this.wsStatusUnsubscribe = websocketService.onStatusChange(this.handleWebSocketStatusChange);

    console.log('[ResearchNotificationService] Started');
  }

  stop(): void {
    this.clearAllNotifications();
    if (this.wsUnsubscribe) {
      this.wsUnsubscribe();
      this.wsUnsubscribe = null;
    }
    if (this.wsStatusUnsubscribe) {
      this.wsStatusUnsubscribe();
      this.wsStatusUnsubscribe = null;
    }
    this.completedPhases.clear();
    this.isStudyComplete = false;
    this.previousAgentStatuses.clear();
    this.isDisconnectedNotificationShown = false;
  }

  restart(): void {
    this.stop();
    this.start();
  }

  clearAllNotifications(): void {
    this.activeNotificationIds.forEach(id => {
      this.notification.hide(id);
    });
    this.activeNotificationIds.clear();
  }

  resetPhases(): void {
    this.completedPhases.clear();
    this.isStudyComplete = false;
    this.activeNotificationIds.clear();
    this.previousAgentStatuses.clear();
  }

  getCompletedPhases(): string[] {
    return Array.from(this.completedPhases);
  }

  isStudyCompleted(): boolean {
    return this.isStudyComplete;
  }

  setNotificationService(notificationService: UINotificationService): void {
    this.notification = notificationService;
  }

  isActive(): boolean {
    return this.wsUnsubscribe !== null;
  }
}

let researchNotificationServiceInstance: ResearchNotificationService | null = null;

export function getResearchNotificationService(): ResearchNotificationService {
  if (!researchNotificationServiceInstance) {
    researchNotificationServiceInstance = new ResearchNotificationService();
  }
  return researchNotificationServiceInstance;
}

export function initResearchNotificationService(servicesManager?: ServicesManager): ResearchNotificationService {
  console.log('[ResearchNotificationService] Initializing...');
  const service = getResearchNotificationService();
  const notificationService = servicesManager?.services?.uiNotificationService;
  if (notificationService) {
    service.setNotificationService(notificationService);
    console.log('[ResearchNotificationService] UI Notification service connected');
  } else {
    console.warn('[ResearchNotificationService] UI Notification service NOT found');
  }
  if (!service.isActive()) {
    console.log('[ResearchNotificationService] Starting service...');
    service.start();
  }
  return service;
}

export function testNotification(phase?: string): void {
  const service = getResearchNotificationService();
  
  if (phase) {
    const notification = service.getPhaseNotification?.(phase);
    if (notification) {
      service.showNotification(`test_${phase}`, notification);
      return;
    }
  }

  const testPhases = [
    'literature_review',
    'population',
    'segmentation',
    'features',
    'modeling',
    'report',
  ];
  
  testPhases.forEach((p, i) => {
    setTimeout(() => {
      const notification = {
        title: `${p.charAt(0).toUpperCase() + p.slice(1)} Phase`,
        message: `Test notification for ${p} phase.`,
        type: 'info' as const,
        duration: 3000,
      };
      service.showNotification(`test_${p}`, notification);
    }, i * 1500);
  });
}

window.testResearchNotification = testNotification;

export { ResearchNotificationService };
export default getResearchNotificationService();