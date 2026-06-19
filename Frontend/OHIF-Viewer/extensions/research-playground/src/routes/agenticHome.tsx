import React from 'react';
import { useNavigate } from 'react-router-dom';
import HomeDashboard from '../components/features/HomeDashboard';

interface AgenticHomeProps {
  onNavigate?: (path: string, options?: any) => void;
  onBack?: () => void;
}

export default function AgenticHome({ onNavigate, onBack }: AgenticHomeProps) {
  return <HomeDashboard onNavigate={onNavigate} onBack={onBack} />;
}

export function PreviewAgenticHome() {
  const navigate = useNavigate();
  return <HomeDashboard onBack={() => navigate('/')} />;
}
