import { useState, useEffect } from 'react';
import websocketService from '../../../../services/websocket';
import { ScratchpadMessage } from './types';

export function useExecutionMessages() {
  const [messages, setMessages] = useState<ScratchpadMessage[]>([]);
  const [isFinished, setIsFinished] = useState(false);

  useEffect(() => {
    const handleWSMessage = (data: any) => {
      const { type, payload } = data;
      
      if (type === 'run_finished') {
        setIsFinished(true);
        return;
      }
      
      // Scratchpad updates are wrapped in scratchpad_update
      if (type === 'scratchpad_update') {
        const msg = payload as ScratchpadMessage;
        setMessages(prev => [...prev, msg]);
      }
      
      // Support granular types if they come directly
      if (['agent_registered', 'agent_note', 'agent_flag', 'agent_status', 'agent_summary'].includes(type)) {
        const msg = payload as ScratchpadMessage;
        // Avoid duplicates if both are sent
        msg.type = type;
        setMessages(prev => {
            // Very simple dedup based on timestamp and content length
            const isDup = prev.some(p => p.timestamp === msg.timestamp && p.content === msg.content);
            if (isDup) return prev;
            return [...prev, msg];
        });
      }
    };

    const unsubscribe = websocketService.subscribe(handleWSMessage);
    return () => unsubscribe();
  }, []);

  return { messages, isFinished };
}
