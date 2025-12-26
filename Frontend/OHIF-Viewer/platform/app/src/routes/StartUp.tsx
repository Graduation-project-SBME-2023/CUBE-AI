import React, { useState } from 'react';
import { Activity, Bot, Layout, ArrowRight, Box, ArrowLeft, CheckCircle2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

// ==========================================
// STYLE CONFIGURATION (Centralized Theme)
// ==========================================
const styles = {
  // Layout & Backgrounds
  wrapper: (hovered) => `w-full h-screen flex items-center justify-center p-6 text-white font-sans relative overflow-hidden transition-colors duration-700 ease-in-out ${
    hovered === 'standard' ? 'bg-[#020410]' : hovered === 'agentic' ? 'bg-[#010a05]' : 'bg-[#000000]'
  }`,
  
  gradients: {
    standard: 'radial-gradient(circle at 30% 50%, rgba(52, 140, 253, 0.15), transparent 60%)',
    agentic: 'radial-gradient(circle at 70% 50%, rgba(111, 229, 184, 0.15), transparent 60%)',
    default: 'radial-gradient(circle at 50% 50%, rgba(255, 255, 255, 0.03), transparent 70%)'
  },
  
  gridTexture: {
    backgroundImage: 'radial-gradient(#44626f 1px, transparent 1px)',
    backgroundSize: '30px 30px'
  },

  // Header Elements
  header: {
    container: "max-w-4xl w-full z-10 flex flex-col items-center animate-in fade-in zoom-in-95 duration-500",
    content: "mb-12 text-center space-y-4",
    badge: "inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[#151a1f] border border-[#3c5d80] text-[#5acce6] text-xs font-bold uppercase tracking-widest shadow-lg",
    titleWrapper: "flex items-center justify-center gap-4",
    logoBox: "p-2 bg-[#151a1f] border border-[#3c5d80] rounded-xl shadow-lg shadow-[#348cfd]/20",
    mainTitle: "text-5xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-white to-[#91b9cd]",
    subTitle: "text-[#91b9cd] text-xl font-medium tracking-wide",
    description: "text-[#566a84] text-sm max-w-xl mx-auto pt-2"
  },

  // Selection Cards
  cards: {
    grid: "grid grid-cols-1 md:grid-cols-2 gap-8 w-full",
    
    // Standard Card Styles
    standardContainer: (hovered) => `group relative p-8 rounded-2xl border transition-all duration-500 text-left flex flex-col gap-4 shadow-xl cursor-pointer ${
      hovered === 'standard' 
        ? 'bg-[#151a1f] border-[#348cfd] shadow-[#348cfd]/20 scale-105 z-10' 
        : hovered === 'agentic' 
        ? 'bg-[#151a1f]/50 border-[#3c5d80] opacity-50 scale-95 blur-[1px]'
        : 'bg-[#151a1f] border-[#3c5d80] hover:border-[#348cfd]'
    }`,
    standardIconBox: (hovered) => `w-14 h-14 rounded-xl border flex items-center justify-center transition-colors duration-300 ${
        hovered === 'standard' ? 'bg-[#348cfd] border-transparent' : 'bg-[#003f5c] border-[#005f7d]'
    }`,
    standardTitle: (hovered) => `text-xl font-bold mb-2 transition-colors ${
        hovered === 'standard' ? 'text-[#5acce6]' : 'text-white'
    }`,
    standardFooter: (hovered) => `mt-auto pt-4 flex items-center text-xs font-bold uppercase tracking-wider transition-colors ${
        hovered === 'standard' ? 'text-white' : 'text-[#566a84]'
    }`,

    // Agentic Card Styles
    agenticContainer: (hovered) => `group relative p-8 rounded-2xl border transition-all duration-500 text-left flex flex-col gap-4 shadow-xl cursor-pointer ${
      hovered === 'agentic' 
        ? 'bg-[#151a1f] border-[#6fe5b8] shadow-[#6fe5b8]/20 scale-105 z-10' 
        : hovered === 'standard' 
        ? 'bg-[#151a1f]/50 border-[#3c5d80] opacity-50 scale-95 blur-[1px]'
        : 'bg-[#151a1f] border-[#3c5d80] hover:border-[#6fe5b8]'
    }`,
    agenticIconBox: (hovered) => `w-14 h-14 rounded-xl border flex items-center justify-center transition-colors duration-300 ${
        hovered === 'agentic' ? 'bg-[#6fe5b8] border-transparent' : 'bg-[#06422d] border-[#085a3c]'
    }`,
    agenticTitle: (hovered) => `text-xl font-bold mb-2 transition-colors ${
        hovered === 'agentic' ? 'text-[#6fe5b8]' : 'text-white'
    }`,
    agenticFooter: (hovered) => `mt-auto pt-4 flex items-center text-xs font-bold uppercase tracking-wider transition-colors ${
        hovered === 'agentic' ? 'text-white' : 'text-[#566a84]'
    }`,

    description: "text-[#91b9cd] text-sm leading-relaxed",
    
    // Animation elements
    pingWrapper: "absolute top-0 right-0 p-3",
    pingDot: (hovered) => `animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${hovered === 'agentic' ? 'bg-[#6fe5b8]' : 'bg-[#3c5d80]'}`,
    staticDot: (hovered) => `relative inline-flex rounded-full h-3 w-3 ${hovered === 'agentic' ? 'bg-[#6fe5b8]' : 'bg-[#566a84]'}`,
  }
};

// ==========================================
// CUBE STARTUP COMPONENT (Main Export)
// ==========================================
const CubeStartUp = () => {
    const navigate = useNavigate();
    const [hovered, setHovered] = useState(null);

    // Handle mode selection and navigation
    const handleModeSelection = (mode) => {
        console.log(`[CUBE] Mode selected: ${mode}`);
        
        if (mode === 'standard') {
            // Navigate to worklist for standard pipeline
            navigate('/worklist');
        } else if (mode === 'agentic') {
            // Navigate to playground for agentic researcher
            // Using a default chat_id or you can generate one
            navigate('/agentic-home');
        }
    };

    return (
        <div className={styles.wrapper(hovered)}>
             {/* Dynamic Background Gradient */}
             <div 
                className="absolute inset-0 pointer-events-none transition-all duration-1000 ease-in-out opacity-60"
                style={{
                    background: hovered === 'standard' 
                        ? styles.gradients.standard
                        : hovered === 'agentic'
                        ? styles.gradients.agentic
                        : styles.gradients.default
                }}
             />
             
             {/* Grid Texture */}
             <div className="absolute inset-0 opacity-20" style={styles.gridTexture} />

             <div className={styles.header.container}>
                {/* Header Section */}
                <div className={styles.header.content}>
                    <div className={styles.header.badge}>
                        <Box size={12} /> CUBE Research Platform
                    </div>
                    <div className={styles.header.titleWrapper}>
                        <div className={styles.header.logoBox}>
                            <Box size={42} className="text-[#5acce6]" strokeWidth={1.5} />
                        </div>
                        <h1 className={styles.header.mainTitle}>
                            CUBE
                        </h1>
                    </div>
                    <p className={styles.header.subTitle}>
                        Your AI Research Assistant
                    </p>
                    <p className={styles.header.description}>
                        Select an operation mode to begin your research session.
                    </p>
                </div>

                {/* Mode Selection Cards */}
                <div className={styles.cards.grid}>
                    {/* Standard Pipeline → Worklist */}
                    <button 
                        onClick={() => handleModeSelection('standard')}
                        onMouseEnter={() => setHovered('standard')}
                        onMouseLeave={() => setHovered(null)}
                        className={styles.cards.standardContainer(hovered)}
                    >
                        <div className={styles.cards.standardIconBox(hovered)}>
                            <Layout size={28} className={`${hovered === 'standard' ? 'text-white' : 'text-[#5acce6]'}`} />
                        </div>
                        <div>
                            <h3 className={styles.cards.standardTitle(hovered)}>Standard Pipeline</h3>
                            <p className={styles.cards.description}>
                                Execute fixed, pre-defined research protocols. Ideal for data preprocessing, standard validation, and deterministic analysis.
                            </p>
                        </div>
                        <div className={styles.cards.standardFooter(hovered)}>
                            Enter Worklist <ArrowRight size={14} className="ml-2 group-hover:translate-x-1 transition-transform" />
                        </div>
                    </button>

                    {/* Agentic Researcher → Playground */}
                    <button 
                        onClick={() => handleModeSelection('agentic')}
                        onMouseEnter={() => setHovered('agentic')}
                        onMouseLeave={() => setHovered(null)}
                        className={styles.cards.agenticContainer(hovered)}
                    >
                         <div className={styles.cards.pingWrapper}>
                             <span className="flex h-3 w-3 relative">
                                <span className={styles.cards.pingDot(hovered)}></span>
                                <span className={styles.cards.staticDot(hovered)}></span>
                             </span>
                         </div>

                        <div className={styles.cards.agenticIconBox(hovered)}>
                            <Bot size={28} className={`${hovered === 'agentic' ? 'text-[#06422d]' : 'text-[#6fe5b8]'}`} />
                        </div>
                        <div>
                            <h3 className={styles.cards.agenticTitle(hovered)}>Agentic Researcher</h3>
                            <p className={styles.cards.description}>
                                Delegate complex tasks to CUBE. It autonomously formulates plans, analyzes findings, and adapts the study workflow dynamically.
                            </p>
                        </div>
                         <div className={styles.cards.agenticFooter(hovered)}>
                            Launch Playground <ArrowRight size={14} className="ml-2 group-hover:translate-x-1 transition-transform" />
                        </div>
                    </button>
                </div>
             </div>
        </div>
    );
};

export default CubeStartUp;