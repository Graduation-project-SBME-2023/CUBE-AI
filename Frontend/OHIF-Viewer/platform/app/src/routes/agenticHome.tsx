import React, { useState, useEffect } from 'react';
import { Search, Clock, Plus, ArrowLeft, ArrowRight, Box, Bot } from 'lucide-react';
import Card from '../components/ui/Card';
import { useNavigate } from 'react-router';
import websocketService from '../utils/WebsocketService';
import { use } from 'i18next';

// Icon aliases to satisfy JSX typing
const ClockIcon: any = Clock;
const BoxIcon: any = Box;
const BotIcon: any = Bot;
const ArrowRightIcon: any = ArrowRight;
const ArrowLeftIcon: any = ArrowLeft;
const SearchIcon: any = Search;

// ==========================================
// STYLE CONFIGURATION
// ==========================================
const styles = {
  container:
    'w-full h-screen bg-[#000000] flex flex-col text-white font-sans overflow-y-auto relative',

  // Background Elements
  bgTexture: {
    backgroundImage: 'radial-gradient(#44626f 1px, transparent 1px)',
    backgroundSize: '30px 30px',
    opacity: 0.2,
  },
  bgGradient: {
    background: 'radial-gradient(circle at 50% 0%, rgba(111, 229, 184, 0.1), transparent 60%)',
  },

  // Navigation
  navBar:
    'h-16 bg-[#151a1f]/80 backdrop-blur-md border-b border-[#3c5d80] flex items-center justify-between px-8 sticky top-0 z-50',
  navLogo: 'flex items-center gap-3 font-bold text-lg tracking-wide text-[#91b9cd]',
  navProfile:
    'w-8 h-8 rounded-full bg-[#151a1f] border border-[#023a7d] flex items-center justify-center text-[#5acce6] text-xs font-bold shadow-inner hover:bg-[#0f1724] transition-colors',

  // Hero Section
  heroSection: 'w-full max-w-5xl mx-auto pt-24 pb-12 px-6 text-center space-y-8 z-10',
  heroSubtitle:
    'text-2xl md:text-3xl font-medium text-transparent bg-clip-text bg-gradient-to-r from-white to-[#91b9cd] max-w-4xl mx-auto leading-relaxed',

  // Search Bar
  searchContainer: 'relative max-w-3xl mx-auto w-full pt-4',
  searchInputWrapper: 'relative group',
  searchInput:
    'w-full h-16 pl-14 pr-36 rounded-2xl bg-[#151a1f] border border-[#3c5d80] text-lg text-white placeholder-[#566a84] focus:outline-none focus:border-[#5acce6] focus:ring-1 focus:ring-[#5acce6] transition-all shadow-2xl',
  searchIcon:
    'absolute left-5 top-1/2 -translate-y-1/2 text-[#566a84] group-focus-within:text-[#5acce6] transition-colors',
  searchButton:
    'absolute right-2 top-2 bottom-2 px-6 bg-[#5acce6] hover:bg-[#5acce6] text-[#000000] font-bold rounded-xl flex items-center gap-2 transition-colors',

  // Grid Section
  gridSection: 'w-full max-w-6xl mx-auto px-6 pb-20 z-10',
  gridHeader: 'flex items-center justify-between mb-6',
  gridTitle: 'text-xl font-bold text-white flex items-center gap-2',

  // Cards
  projectsGrid: 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6',
  projectCard:
    'group p-6 rounded-xl bg-[#151a1f] border border-[#3c5d80] hover:border-[#5acce6] hover:bg-[#17202a] transition-all cursor-pointer flex flex-col gap-4 relative overflow-hidden shadow-lg hover:shadow-[#5acce6]/8',
  cardStatus: status =>
    `inline-flex px-2 py-1 rounded text-[10px] font-bold uppercase tracking-wider w-fit ${
      status === 'Completed'
        ? 'bg-[#023a7d] text-[#5acce6] border border-[#0f3354]'
        : status === 'In Progress'
          ? 'bg-[#3b2d1c] text-[#f8ce46] border border-[#705228]'
          : 'bg-[#1f2d40] text-[#91b9cd] border border-[#3c5d80]'
    }`,
  cardTitle: 'text-lg font-bold text-white group-hover:text-[#5acce6] transition-colors',
  cardDate: 'text-xs text-[#566a84] flex items-center gap-1.5 mt-auto',
  cardHoverOverlay:
    'absolute inset-0 bg-gradient-to-r from-[#5acce6]/0 to-[#5acce6]/5 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none',
};

// ==========================================
// AGENTIC HOME PAGE COMPONENT
// ==========================================
const AgenticHome = ({ onNavigate, onBack }) => {
  const [hypothesis, setHypothesis] = useState('');
  const navigate = useNavigate();
  const handleSearch = async () => {
    if (!hypothesis.trim()) {
      return;
    }

    try {
      // 1. Create the session in the database first
      const payload = { title: hypothesis.substring(0, 30) + '...' };
      const response = await fetch('http://localhost:8000/chat_sessions/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();
      const newSessionId = data.chat_id;

      // 2. Navigate to playground passing both hypothesis AND the new sessionId
      navigate('/playground', {
        state: {
          hypothesis,
          sessionId: newSessionId,
          sendInitialMessage: true,
        },
      });
    } catch (err) {
      console.error('Failed to create session:', err);
      // Fallback: Navigate anyway with a timestamp-based ID if DB fails
      navigate('/playground', {
        state: {
          hypothesis,
          sessionId: Date.now(),
        },
      });
    }
  };

  const handleProjectClick = project => {
    // Use the prop to navigate
    if (onNavigate) {
      onNavigate('/cube-workflow', { state: { projectId: project.id } });
    }
  };

  const MOCK_HISTORY = [
    {
      id: 1,
      title: 'Liver Tumor Segmentation Analysis',
      date: '2 hours ago',
      status: 'In Progress',
    },
    { id: 2, title: 'Brain MRI Anomaly Detection v2', date: 'Yesterday', status: 'Completed' },
    {
      id: 3,
      title: 'Chest X-Ray Pneumonia Classification',
      date: '3 days ago',
      status: 'Completed',
    },
    { id: 4, title: 'Cardiac MRI Flow Dynamics', date: 'Last week', status: 'Draft' },
    { id: 5, title: 'Spine Fracture Localization', date: 'Last week', status: 'Completed' },
    { id: 6, title: 'Retinal Scan Diabetic Retinopathy', date: '2 weeks ago', status: 'Draft' },
  ];

  return (
    <div className={styles.container}>
      {/* Background Texture */}
      <div
        className="pointer-events-none absolute inset-0"
        style={styles.bgTexture}
      />
      <div
        className="pointer-events-none absolute inset-0"
        style={styles.bgGradient}
      />

      {/* Nav Bar */}
      <div className={styles.navBar}>
        <div className={styles.navLogo}>
          <button
            onClick={() => (onBack ? onBack() : navigate('/home'))}
            className="rounded-full p-2 text-[#566a84] transition-colors hover:bg-[#1f2d40] hover:text-white"
            title="Back to Startup"
            aria-label="Back to Startup"
          >
            <ArrowLeftIcon size={20} />
          </button>
          <BoxIcon
            size={20}
            className="text-[#5acce6]"
          />
          <span>
            CUBE <span className="font-normal opacity-50">| Agentic Mode</span>
          </span>
        </div>
        <div
          className={styles.navProfile}
          title="AI"
          aria-label="AI persona"
        >
          AI
        </div>
      </div>

      {/* Hero / Input Section */}
      <div className={styles.heroSection}>
        <div>
          <p className={styles.heroSubtitle}>
            Describe your research question or hypothesis, and CUBE will construct the workflow.
          </p>
        </div>

        <div className={styles.searchContainer}>
          <div className={styles.searchInputWrapper}>
            <SearchIcon
              className={styles.searchIcon}
              size={24}
            />
            <input
              type="text"
              className={styles.searchInput}
              placeholder="Enter your research question or hypothesis..."
              value={hypothesis}
              onChange={e => setHypothesis(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSearch()}
            />
            <button
              onClick={handleSearch}
              className={styles.searchButton}
            >
              Start Research <ArrowRightIcon size={16} />
            </button>
          </div>
        </div>
      </div>

      {/* Recent Projects Grid */}
      <div className={styles.gridSection}>
        <div className={styles.gridHeader}>
          <h2 className={styles.gridTitle}>
            <ClockIcon
              size={20}
              className="text-[#5acce6]"
            />{' '}
            Recent Hypotheses
          </h2>
          <button className="flex items-center gap-1 text-xs text-[#566a84] transition-colors hover:text-[#5acce6]">
            View All <ArrowRightIcon size={12} />
          </button>
        </div>

        <div className={styles.projectsGrid}>
          {/* History Items */}
          {MOCK_HISTORY.map(project => (
            <div
              key={project.id}
              onClick={() => handleProjectClick(project)}
            >
              <Card
                title={project.title}
                description={`Edited ${project.date}`}
                icon={
                  <BotIcon
                    size={24}
                    className="text-[#5acce6] opacity-80"
                  />
                }
                ctaText={project.status}
                onClick={() => handleProjectClick(project)}
                className="group"
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

// =========================================================================
// PREVIEW WRAPPER (used for local previews during development)
// This preview will navigate back to the startup route so the back button behaves as expected.
// =========================================================================
export function PreviewAgenticHome() {
  const navigate = useNavigate();

  const handleNavigation = (path, state) => {
    console.log('Navigating to:', path, 'with state:', state);
    if (path.includes('workspace')) {
      alert(
        `Simulating navigation to Workspace. \nHypothesis: ${state?.hypothesis || 'None'}\nProjectID: ${state?.projectId || 'None'}`
      );
    }
  };

  return (
    <AgenticHome
      onNavigate={handleNavigation}
      onBack={() => navigate('/home')}
    />
  );
}

// Export the real component as the default so the app router uses it directly
export default AgenticHome;
