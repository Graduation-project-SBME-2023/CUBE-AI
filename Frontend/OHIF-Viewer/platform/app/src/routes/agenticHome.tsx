import React, { useState, useEffect } from 'react';
import { 
  Search, Clock, Plus, ArrowLeft, ArrowRight, Box, Bot 
} from 'lucide-react';
import { useNavigate } from 'react-router';
import websocketService from '../utils/WebsocketService';
import { use } from 'i18next';

// ==========================================
// STYLE CONFIGURATION
// ==========================================
const styles = {
  container: "w-full h-screen bg-[#000000] flex flex-col text-white font-sans overflow-y-auto relative",
  
  // Background Elements
  bgTexture: {
    backgroundImage: 'radial-gradient(#44626f 1px, transparent 1px)',
    backgroundSize: '30px 30px',
    opacity: 0.2
  },
  bgGradient: {
    background: 'radial-gradient(circle at 50% 0%, rgba(111, 229, 184, 0.1), transparent 60%)'
  },

  // Navigation
  navBar: "h-16 bg-[#151a1f]/80 backdrop-blur-md border-b border-[#3c5d80] flex items-center justify-between px-8 sticky top-0 z-50",
  navLogo: "flex items-center gap-3 font-bold text-lg tracking-wide text-[#91b9cd]",
  navProfile: "w-8 h-8 rounded-full bg-[#06422d] border border-[#6fe5b8] flex items-center justify-center text-[#6fe5b8] text-xs font-bold",
  
  // Hero Section
  heroSection: "w-full max-w-5xl mx-auto pt-24 pb-12 px-6 text-center space-y-8 z-10",
  heroSubtitle: "text-2xl md:text-3xl font-medium text-transparent bg-clip-text bg-gradient-to-r from-white to-[#91b9cd] max-w-4xl mx-auto leading-relaxed",
  
  // Search Bar
  searchContainer: "relative max-w-3xl mx-auto w-full pt-4",
  searchInputWrapper: "relative group",
  searchInput: "w-full h-16 pl-14 pr-36 rounded-2xl bg-[#151a1f] border border-[#3c5d80] text-lg text-white placeholder-[#566a84] focus:outline-none focus:border-[#6fe5b8] focus:ring-1 focus:ring-[#6fe5b8] transition-all shadow-2xl",
  searchIcon: "absolute left-5 top-1/2 -translate-y-1/2 text-[#566a84] group-focus-within:text-[#6fe5b8] transition-colors",
  searchButton: "absolute right-2 top-2 bottom-2 px-6 bg-[#6fe5b8] hover:bg-[#59c99f] text-[#000000] font-bold rounded-xl flex items-center gap-2 transition-colors",
  
  // Grid Section
  gridSection: "w-full max-w-6xl mx-auto px-6 pb-20 z-10",
  gridHeader: "flex items-center justify-between mb-6",
  gridTitle: "text-xl font-bold text-white flex items-center gap-2",
  
  // Cards
  projectsGrid: "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6",
  projectCard: "group p-6 rounded-xl bg-[#151a1f] border border-[#3c5d80] hover:border-[#6fe5b8] hover:bg-[#1f2630] transition-all cursor-pointer flex flex-col gap-4 relative overflow-hidden shadow-lg hover:shadow-[#6fe5b8]/10",
  cardStatus: (status) => `inline-flex px-2 py-1 rounded text-[10px] font-bold uppercase tracking-wider w-fit ${
      status === 'Completed' ? 'bg-[#06422d] text-[#6fe5b8] border border-[#085a3c]' :
      status === 'In Progress' ? 'bg-[#3b2d1c] text-[#f8ce46] border border-[#705228]' :
      'bg-[#1f2d40] text-[#91b9cd] border border-[#3c5d80]'
  }`,
  cardTitle: "text-lg font-bold text-white group-hover:text-[#6fe5b8] transition-colors",
  cardDate: "text-xs text-[#566a84] flex items-center gap-1.5 mt-auto",
  cardHoverOverlay: "absolute inset-0 bg-gradient-to-r from-[#6fe5b8]/0 to-[#6fe5b8]/5 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
};

// ==========================================
// AGENTIC HOME PAGE COMPONENT
// ==========================================
const AgenticHome = ({ onNavigate, onBack }) => {
    const [hypothesis, setHypothesis] = useState("");
    const navigate = useNavigate();
    useEffect(() => {
        // Connect to WebSocket on mount
        websocketService.connect();
        return () => {
            // Optionally close or cleanup on unmount
            if (websocketService.socket) {
                websocketService.socket.close();
            }
        };
    }, []);

    const handleSearch = () => {
        if (!hypothesis.trim()) return;
        // Send the hypothesis as a user chat message to backend via WebSocket
        websocketService.sendMessage({ type: 'msg', role: 'user', content: hypothesis });
        // Navigate to playground, passing the hypothesis
        navigate('/playground', { state: { hypothesis } });
    };

    const handleProjectClick = (project) => {
        // Use the prop to navigate
        if (onNavigate) {
            onNavigate('/cube-workflow', { state: { projectId: project.id } });
        }
    };

    const MOCK_HISTORY = [
        { id: 1, title: "Liver Tumor Segmentation Analysis", date: "2 hours ago", status: "In Progress" },
        { id: 2, title: "Brain MRI Anomaly Detection v2", date: "Yesterday", status: "Completed" },
        { id: 3, title: "Chest X-Ray Pneumonia Classification", date: "3 days ago", status: "Completed" },
        { id: 4, title: "Cardiac MRI Flow Dynamics", date: "Last week", status: "Draft" },
        { id: 5, title: "Spine Fracture Localization", date: "Last week", status: "Completed" },
        { id: 6, title: "Retinal Scan Diabetic Retinopathy", date: "2 weeks ago", status: "Draft" },
    ];

    return (
        <div className={styles.container}>
            {/* Background Texture */}
            <div className="absolute inset-0 pointer-events-none" style={styles.bgTexture} />
            <div className="absolute inset-0 pointer-events-none" style={styles.bgGradient} />

            {/* Nav Bar */}
            <div className={styles.navBar}>
                <div className={styles.navLogo}>
                    <button 
                        onClick={onBack} 
                        className="p-2 hover:bg-[#1f2d40] rounded-full transition-colors text-[#566a84] hover:text-white"
                        title="Back to Startup"
                    >
                        <ArrowLeft size={20} />
                    </button>
                    <Box size={20} className="text-[#6fe5b8]" />
                    <span>CUBE <span className="opacity-50 font-normal">| Agentic Mode</span></span>
                </div>
                <div className={styles.navProfile}>AI</div>
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
                        <Search className={styles.searchIcon} size={24} />
                        <input 
                            type="text" 
                            className={styles.searchInput}
                            placeholder="Enter your research question or hypothesis..."
                            value={hypothesis}
                            onChange={(e) => setHypothesis(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                        />
                        <button onClick={handleSearch} className={styles.searchButton}>
                            Start Research <ArrowRight size={16} />
                        </button>
                    </div>
                </div>
            </div>

            {/* Recent Projects Grid */}
            <div className={styles.gridSection}>
                <div className={styles.gridHeader}>
                    <h2 className={styles.gridTitle}>
                        <Clock size={20} className="text-[#6fe5b8]" /> Recent Hypotheses
                    </h2>
                    <button className="text-xs text-[#566a84] hover:text-[#6fe5b8] flex items-center gap-1 transition-colors">
                        View All <ArrowRight size={12} />
                    </button>
                </div>

                <div className={styles.projectsGrid}>
                    {/* History Items */}
                    {MOCK_HISTORY.map((project) => (
                        <div key={project.id} className={styles.projectCard} onClick={() => handleProjectClick(project)}>
                            <div className={styles.cardHoverOverlay}></div>
                            <div className="flex justify-between items-start">
                                <Bot size={24} className="text-[#6fe5b8] opacity-80" />
                                <div className={styles.cardStatus(project.status)}>
                                    {project.status}
                                </div>
                            </div>
                            <h3 className={styles.cardTitle}>{project.title}</h3>
                            <div className={styles.cardDate}>
                                <Clock size={12} /> Edited {project.date}
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};

// =========================================================================
// PREVIEW WRAPPER (This part mimics the Router logic for the preview)
// In your real app, you can export AgenticHome directly.
// =========================================================================
export default function PreviewAgenticHome() {
    // This is just a dummy function to handle the preview interactions
    const handleNavigation = (path, state) => {
        console.log("Navigating to:", path, "with state:", state);
        if (path.includes('workspace')) {
            alert(`Simulating navigation to Workspace. \nHypothesis: ${state?.hypothesis || 'None'}\nProjectID: ${state?.projectId || 'None'}`);
        }
    };

    return <AgenticHome onNavigate={handleNavigation} onBack={() => console.log("Back clicked")} />;
}