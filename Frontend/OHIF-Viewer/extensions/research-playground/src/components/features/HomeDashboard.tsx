import React, { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Bot, Box, Clock, List, Search, Trash2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import Card from '../ui/Card';
import { getBackendApiBaseUrl } from '../../utils/apiBase';

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
  const [itemToDelete, setItemToDelete] = useState<any | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const fetchHistory = async () => {
      setLoadingHistory(true);
      try {
        const response = await fetch(`${getBackendApiBaseUrl()}/hypotheses/?skip=0&limit=1000`);
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
      textareaRef.current.style.height = '72px';
      const scrollHeight = textareaRef.current.scrollHeight;
      textareaRef.current.style.height = `${Math.min(scrollHeight, 200)}px`;
    }
  }, [hypothesis]);

  const handleSearch = async () => {
    if (!hypothesis.trim()) {
      return;
    }

    try {
      const payload = { title: `${hypothesis.substring(0, 30)}...` };
      const response = await fetch(`${getBackendApiBaseUrl()}/chat_sessions/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();
      const sessionId = String(data.chat_id);
      // Persist the Literature Review preference so it survives navigation away and back via history
      try {
        localStorage.setItem(`cube_session_prefs_${sessionId}`, JSON.stringify({ includeLitReview }));
      } catch (_) {/* ignore storage errors */ }
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
      const fallbackId = makeFallbackSessionId();
      try {
        localStorage.setItem(`cube_session_prefs_${fallbackId}`, JSON.stringify({ includeLitReview }));
      } catch (_) {/* ignore storage errors */ }
      navigate('/playground', {
        state: {
          hypothesis,
          sessionId: fallbackId,
          sendInitialMessage: true,
          includeLitReview,
        },
      });
    }
  };

  const handleProjectClick = (project: any) => {
    const sessionId = project.chat_id || project.id || project.session_id;

    // Restore the Literature Review preference that was saved when this session was first created
    let includeLitReviewRestored = true;
    try {
      const saved = localStorage.getItem(`cube_session_prefs_${sessionId}`);
      if (saved) {
        const parsed = JSON.parse(saved);
        if (typeof parsed.includeLitReview === 'boolean') {
          includeLitReviewRestored = parsed.includeLitReview;
        }
      }
    } catch (_) {/* ignore storage errors */ }

    const state = {
      sessionId,
      hypothesis: project.title || project.description || null,
      sendInitialMessage: false,
      includeLitReview: includeLitReviewRestored,
    };

    if (onNavigate) {
      onNavigate('/playground', { state });
      return;
    }
    navigate('/playground', { state });
  };

  const handleDeleteClick = (e: React.MouseEvent, project: any) => {
    e.stopPropagation();
    setItemToDelete(project);
  };

  const confirmDelete = async () => {
    if (!itemToDelete) return;
    setIsDeleting(true);
    try {
      // Deleting the chat session cascades and cleans up the hypothesis better
      // Also fallback to hypotheses endpoint if chat_id is missing
      const endpoint = itemToDelete.chat_id
        ? `${getBackendApiBaseUrl()}/chat_sessions/${itemToDelete.chat_id}`
        : `${getBackendApiBaseUrl()}/hypotheses/${itemToDelete.id}`;
      const response = await fetch(endpoint, {
        method: 'DELETE',
      });
      if (response.ok) {
        setHistory(prev => prev.filter(item => item.id !== itemToDelete.id));
      } else {
        console.error('Failed to delete hypothesis:', await response.text());
        // Still remove it locally to allow user to bypass ghost entries
        setHistory(prev => prev.filter(item => item.id !== itemToDelete.id));
      }
    } catch (err) {
      console.error('Error deleting hypothesis:', err);
    } finally {
      setIsDeleting(false);
      setItemToDelete(null);
    }
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

          <button
            onClick={() => navigate('/worklist')}
            className="mr-2 p-2 rounded-md transition-all hover:bg-[#0d1e2e] text-[#5a8aaa] hover:text-[#5acce6] border border-transparent hover:border-[#1e3248]"
            title="Go to Worklist"
            aria-label="Go to Worklist"
          >
            <List size={16} />
          </button>

        </div>

      </div>

      <div className="z-10 mx-auto w-full max-w-5xl space-y-8 px-6 pb-12 pt-24 text-center">
        <p className="mx-auto max-w-4xl bg-gradient-to-r from-white to-[#91b9cd] bg-clip-text text-2xl font-medium leading-relaxed text-transparent md:text-3xl">
          Describe your research question or hypothesis, and CUBE will construct the workflow.
        </p>
        <div className="group relative mx-auto w-full max-w-4xl rounded-3xl border border-[#2e445c] bg-[#151a1f] p-2 shadow-2xl transition-all focus-within:border-[#5acce6] focus-within:shadow-[0_0_30px_rgba(90,204,230,0.15)]">
          {/* Search Area */}
          <div className="relative flex items-start">
            <Search
              className="absolute left-5 top-5 text-[#566a84] transition-colors group-focus-within:text-[#5acce6]"
              size={24}
            />
            <textarea
              ref={textareaRef}
              className="min-h-[72px] max-h-[200px] w-full resize-none overflow-y-auto bg-transparent py-5 pl-14 pr-4 text-lg text-white placeholder-[#566a84] focus:outline-none"
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

          {/* Action Footer */}
          <div className="flex items-center justify-between px-3 pb-2 pt-2 border-t border-[#1f2d40]">
            {/* Toggle */}
            <button
              type="button"
              onClick={() => setIncludeLitReview(!includeLitReview)}
              className="flex items-center gap-3 rounded-xl px-2 py-2 transition-colors hover:bg-[#1f2d40]"
            >
              <div
                className={`relative h-6 w-11 rounded-full transition-colors duration-300 ${includeLitReview ? 'bg-[#5acce6] shadow-[0_0_10px_rgba(90,204,230,0.3)]' : 'bg-[#2a3b4d]'
                  }`}
              >
                <div
                  className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-md transition-transform duration-300 ${includeLitReview ? 'translate-x-5' : 'translate-x-0.5'
                    }`}
                />
              </div>
              <span
                className={`text-sm font-medium transition-colors ${includeLitReview ? 'text-white' : 'text-[#566a84]'
                  }`}
              >
                Literature Review
              </span>
            </button>

            {/* Start Research Button */}
            <button
              onClick={handleSearch}
              className="flex items-center gap-2 rounded-xl bg-[#5acce6] px-6 py-2.5 font-bold text-black shadow-lg transition-all duration-300 hover:bg-[#4ab8d4] disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-[0_0_20px_rgba(90,204,230,0.4)] hover:-translate-y-0.5 disabled:hover:translate-y-0 disabled:hover:shadow-lg"
              disabled={!hypothesis.trim()}
            >
              Start Research <ArrowRight size={18} className="transition-transform hover:translate-x-1" />
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
              <div key={project.id} className="relative group">
                <Card
                  title={project.title}
                  description={`Edited ${formatDate(project.date ?? project.updated_at ?? project.created_at)}`}
                  icon={<Bot size={24} className="text-[#5acce6] opacity-80" />}
                  ctaText={project.status}
                  onClick={() => handleProjectClick(project)}
                />
                <button
                  onClick={(e) => handleDeleteClick(e, project)}
                  className="absolute top-4 right-4 z-20 rounded-md p-2 text-[#3c5d80] opacity-0 transition-all group-hover:opacity-100 hover:bg-red-500/10 hover:text-red-400"
                  aria-label="Delete hypothesis"
                  title="Delete hypothesis"
                >
                  <Trash2 size={18} />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Delete Confirmation Modal */}
      {itemToDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-xl border border-[#2e445c] bg-[#10151a] p-6 shadow-2xl">
            <h3 className="mb-2 text-lg font-bold text-white">Delete Hypothesis?</h3>
            <p className="mb-6 text-sm text-[#91b9cd]">
              Are you sure you want to delete <span className="font-semibold text-white">"{itemToDelete.title}"</span>? This action cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setItemToDelete(null)}
                disabled={isDeleting}
                className="rounded-lg px-4 py-2 text-sm font-medium text-[#566a84] transition-colors hover:bg-[#1a2535] hover:text-white"
              >
                Cancel
              </button>
              <button
                onClick={confirmDelete}
                disabled={isDeleting}
                className="flex items-center gap-2 rounded-lg bg-red-500/10 px-4 py-2 text-sm font-medium text-red-500 transition-colors hover:bg-red-500/20 disabled:opacity-50"
              >
                {isDeleting ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
