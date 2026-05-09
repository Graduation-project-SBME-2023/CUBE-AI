import React, { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Bot, Box, Clock, Search } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import Card from '../ui/Card';

const makeFallbackSessionId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

interface HomeDashboardProps {
  onNavigate?: (path: string, options?: any) => void;
  onBack?: () => void;
}

export default function HomeDashboard({ onNavigate, onBack }: HomeDashboardProps) {
  const [hypothesis, setHypothesis] = useState('');
  const [includeLitReview, setIncludeLitReview] = useState(true);
  const [history, setHistory] = useState<any[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [showAllHistory, setShowAllHistory] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const fetchHistory = async () => {
      setLoadingHistory(true);
      try {
        const response = await fetch('http://localhost:8000/hypotheses/?skip=0&limit=1000');
        if (response.ok) {
          const data = await response.json();
          const sorted = [...data].sort((a, b) => {
            const aTime = new Date(a.date ?? a.updated_at ?? a.created_at ?? 0).getTime();
            const bTime = new Date(b.date ?? b.updated_at ?? b.created_at ?? 0).getTime();
            return bTime - aTime;
          });
          setHistory(showAllHistory ? sorted : sorted.slice(0, 10));
        }
      } catch (err) {
        console.error('Failed to fetch history:', err);
      } finally {
        setLoadingHistory(false);
      }
    };
    fetchHistory();
  }, [showAllHistory]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = '56px';
      const scrollHeight = textareaRef.current.scrollHeight;
      textareaRef.current.style.height = `${Math.min(scrollHeight, 140)}px`;
    }
  }, [hypothesis]);

  const handleSearch = async () => {
    if (!hypothesis.trim()) {
      return;
    }

    try {
      const payload = { title: `${hypothesis.substring(0, 30)}...` };
      const response = await fetch('http://localhost:8000/chat_sessions/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();
      const sessionId = String(data.chat_id);
      navigate('/playground', {
        state: {
          hypothesis,
          sessionId,
          sendInitialMessage: true,
          includeLitReview,
        },
      });
    } catch (err) {
      console.error('Failed to create session:', err);
      navigate('/playground', {
        state: {
          hypothesis,
          sessionId: makeFallbackSessionId(),
          sendInitialMessage: true,
          includeLitReview,
        },
      });
    }
  };

  const handleProjectClick = (project: any) => {
    const state = {
      sessionId: project.chat_id || project.id || project.session_id,
      hypothesis: project.title || project.description || null,
      sendInitialMessage: false,
    };

    if (onNavigate) {
      onNavigate('/playground', { state });
      return;
    }
    navigate('/playground', { state });
  };

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return 'Unknown';
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffHours = diffMs / (1000 * 60 * 60);
    if (diffHours < 1) return 'Just now';
    if (diffHours < 24) return `${Math.floor(diffHours)} hours ago`;
    const diffDays = diffHours / 24;
    if (diffDays < 2) return 'Yesterday';
    if (diffDays < 7) return `${Math.floor(diffDays)} days ago`;
    return 'Last week';
  };

  return (
    <div className="relative flex h-screen w-full flex-col overflow-y-auto bg-black font-sans text-white">
      <div
        className="pointer-events-none absolute inset-0 opacity-20"
        style={{ backgroundImage: 'radial-gradient(#44626f 1px, transparent 1px)', backgroundSize: '30px 30px' }}
      />
      <div
        className="pointer-events-none absolute inset-0"
        style={{ background: 'radial-gradient(circle at 50% 0%, rgba(111, 229, 184, 0.1), transparent 60%)' }}
      />

      <div className="sticky top-0 z-50 flex h-16 items-center justify-between border-b border-[#3c5d80] bg-[#151a1f]/80 px-8 backdrop-blur-md">
        <div className="flex items-center gap-3 text-lg font-bold tracking-wide text-[#91b9cd]">
          <button
            onClick={() => (onBack ? onBack() : navigate('/'))}
            className="rounded-full p-2 text-[#566a84] transition-colors hover:bg-[#1f2d40] hover:text-white"
            title="Back to Startup"
            aria-label="Back to Startup"
          >
            <ArrowLeft size={20} />
          </button>
          <Box size={20} className="text-[#5acce6]" />
          <span>
            CUBE <span className="font-normal opacity-50">| Agentic Mode</span>
          </span>
        </div>
        <div className="flex h-8 w-8 items-center justify-center rounded-full border border-[#023a7d] bg-[#151a1f] text-xs font-bold text-[#5acce6] shadow-inner">
          AI
        </div>
      </div>

      <div className="z-10 mx-auto w-full max-w-5xl space-y-8 px-6 pb-12 pt-24 text-center">
        <p className="mx-auto max-w-4xl bg-gradient-to-r from-white to-[#91b9cd] bg-clip-text text-2xl font-medium leading-relaxed text-transparent md:text-3xl">
          Describe your research question or hypothesis, and CUBE will construct the workflow.
        </p>

        <div className="relative mx-auto w-full max-w-3xl pt-4">
          <div className="group flex flex-col gap-3">
            <div className="relative w-full">
              <Search className="absolute left-5 top-4 text-[#566a84] transition-colors group-focus-within:text-[#5acce6]" size={24} />
              <textarea
                ref={textareaRef}
                className="min-h-[56px] max-h-[140px] w-full resize-none overflow-y-auto rounded-2xl border border-[#3c5d80] bg-[#151a1f] py-4 pl-14 pr-4 text-lg text-white placeholder-[#566a84] shadow-2xl transition-all focus:border-[#5acce6] focus:outline-none focus:ring-1 focus:ring-[#5acce6]"
                placeholder="Enter your research question or hypothesis..."
                value={hypothesis}
                onChange={e => setHypothesis(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSearch();
                  }
                }}
                rows={1}
              />
            </div>

            <div className="flex items-center gap-2 px-2 text-[#91b9cd]">
              <input
                type="checkbox"
                id="litReviewToggle"
                checked={includeLitReview}
                onChange={e => setIncludeLitReview(e.target.checked)}
                className="h-4 w-4 rounded border-[#3c5d80] bg-[#151a1f] text-[#5acce6] focus:ring-[#5acce6]"
              />
              <label htmlFor="litReviewToggle" className="cursor-pointer text-sm font-medium">
                Include Literature Review
              </label>
            </div>

            <button
              onClick={handleSearch}
              className="self-end rounded-xl bg-[#5acce6] px-6 py-3 font-bold text-black shadow-lg transition-colors hover:bg-[#4ab8d4]"
              disabled={!hypothesis.trim()}
              style={{ opacity: hypothesis.trim() ? 1 : 0.5, cursor: hypothesis.trim() ? 'pointer' : 'not-allowed' }}
            >
              Start Research <ArrowRight size={16} className="inline" />
            </button>
          </div>
        </div>
      </div>

      <div className="z-10 mx-auto w-full max-w-6xl px-6 pb-20">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-xl font-bold text-white">
            <Clock size={20} className="text-[#5acce6]" />
            {showAllHistory ? 'All Hypotheses' : 'Recent Hypotheses'}
          </h2>
          <button
            onClick={() => setShowAllHistory(prev => !prev)}
            className="flex items-center gap-1 text-xs text-[#566a84] transition-colors hover:text-[#5acce6]"
          >
            {showAllHistory ? 'Show Recent' : 'View All'} <ArrowRight size={12} />
          </button>
        </div>

        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
          {loadingHistory ? (
            <div className="text-[#566a84]">Loading...</div>
          ) : history.length === 0 ? (
            <div className="text-[#566a84]">No recent hypotheses</div>
          ) : (
            history.map(project => (
              <div key={project.id} onClick={() => handleProjectClick(project)}>
                <Card
                  title={project.title}
                  description={`Edited ${formatDate(project.date ?? project.updated_at ?? project.created_at)}`}
                  icon={<Bot size={24} className="text-[#5acce6] opacity-80" />}
                  ctaText={project.status}
                  onClick={() => handleProjectClick(project)}
                  className="group"
                />
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
