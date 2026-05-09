import React from 'react';
import { useLocation } from 'react-router-dom';
import PlaygroundContainer from '../../components/features/PlaygroundContainer';

export default function AgentsPlayground() {
  const location = useLocation();
  const hypothesis = location.state?.hypothesis || '';
  const sessionId = location.state?.sessionId || null;
  const sendInitialMessage = location.state?.sendInitialMessage || false;

  return (
    <PlaygroundContainer
      hypothesis={hypothesis}
      sessionId={sessionId}
      sendInitialMessage={sendInitialMessage}
      includeLitReview={location.state?.includeLitReview ?? true}
    />
  );
}
