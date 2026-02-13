import React, { useState } from 'react';
import { Activity, Bot, Layout, ArrowRight, Box, ArrowLeft, CheckCircle2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import Card from '../components/ui/Card';

// Icon aliases for lucide-react to satisfy JSX typing in this project
const BoxIcon: any = Box;
const LayoutIcon: any = Layout;
const BotIcon: any = Bot;

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
    // Agentic gradient switched to OHIF cyan (unified)
    agentic: 'radial-gradient(circle at 70% 50%, rgba(90, 204, 230, 0.15), transparent 60%)',
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
    badge: "inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[#5acce6] border border-[#023a7d] text-[#000000] text-xs font-bold uppercase tracking-widest shadow-lg",
    titleWrapper: "flex items-center justify-center gap-4",
    logoBox: "p-2 bg-[#151a1f] border border-[#023a7d] rounded-xl shadow-lg shadow-[#5acce6]/20",
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
        hovered === 'standard' ? 'text-[#348cfd]' : 'text-white'
    }`,
    standardFooter: (hovered) => `mt-auto pt-4 flex items-center text-xs font-bold uppercase tracking-wider transition-colors ${
        hovered === 'standard' ? 'text-white' : 'text-[#566a84]'
    }`,

    // Agentic Card Styles
    agenticContainer: (hovered) => `group relative p-8 rounded-2xl border transition-all duration-500 text-left flex flex-col gap-4 shadow-xl cursor-pointer ${
      hovered === 'agentic' 
        ? 'bg-[#151a1f] border-[#348cfd] shadow-[#348cfd]/12 scale-105 z-10' 
        : hovered === 'standard' 
        ? 'bg-[#151a1f]/50 border-[#3c5d80] opacity-50 scale-95 blur-[1px]'
        : 'bg-[#151a1f] border-[#3c5d80] hover:border-[#348cfd]'
    }`,
    agenticIconBox: (hovered) => `w-14 h-14 rounded-xl border flex items-center justify-center transition-colors duration-300 ${
        hovered === 'agentic' ? 'bg-[#5acce6] border-transparent' : 'bg-[#023a7d] border-[#0f3354]'
    }`,
    agenticTitle: (hovered) => `text-xl font-bold mb-2 transition-colors ${
        hovered === 'agentic' ? 'text-[#5acce6]' : 'text-white'
    }`,
    agenticFooter: (hovered) => `mt-auto pt-4 flex items-center text-xs font-bold uppercase tracking-wider transition-colors ${
        hovered === 'agentic' ? 'text-white' : 'text-[#566a84]'
    }`,

    description: "text-[#91b9cd] text-sm leading-relaxed",
    
    // Animation elements
    pingWrapper: "absolute top-0 right-0 p-3",
    pingDot: (hovered) => `animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${hovered === 'agentic' ? 'bg-[#5acce6]' : 'bg-[#3c5d80]'}`,
    staticDot: (hovered) => `relative inline-flex rounded-full h-3 w-3 ${hovered === 'agentic' ? 'bg-[#5acce6]' : 'bg-[#566a84]'}`,
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
            navigate('/');
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
                        <BoxIcon size={12} /> CUBE Research Platform
                    </div>
                    <div className={styles.header.titleWrapper}>
                        <div className={styles.header.logoBox}>
                            <BoxIcon size={42} className="text-[#5acce6]" strokeWidth={1.5} />
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

                {/* Mode Selection Cards (refactored to use shared Card) */}
                <div className={styles.cards.grid}>
                    {/* Standard Pipeline → Worklist */}
                    <div onMouseEnter={() => setHovered('standard')} onMouseLeave={() => setHovered(null)}>
                      <Card
                        title="Standard Pipeline"
                        description="Execute fixed, pre-defined research protocols. Ideal for data preprocessing, standard validation, and deterministic analysis."
                        icon={<LayoutIcon size={28} className={`${hovered === 'standard' ? 'text-white' : 'text-[#5acce6]'}`} />}
                        ctaText="Enter Worklist"
                        onClick={() => handleModeSelection('standard')}
                        variant="standard"
                        className={`${hovered === 'standard' ? 'scale-105 z-10 border-[#348cfd]' : hovered === 'agentic' ? 'bg-[#151a1f]/50 border-[#3c5d80] opacity-50 scale-95 blur-[1px]' : 'border-[#3c5d80] hover:border-[#348cfd]'}`}
                      />
                    </div>

                    {/* Agentic Researcher → Playground */}
                    <div onMouseEnter={() => setHovered('agentic')} onMouseLeave={() => setHovered(null)} style={hovered === 'agentic' ? { boxShadow: '0 20px 40px rgba(90,204,230,0.12)' } : undefined}>
                      <Card
                        title="Agentic Researcher"
                        description="Delegate complex tasks to CUBE. It autonomously formulates plans, analyzes findings, and adapts the study workflow dynamically."
                        icon={<div className="relative w-full h-full flex items-center justify-center"><span className="flex h-3 w-3 relative mr-3"><span className={hovered === 'agentic' ? 'animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 bg-[#5acce6]' : 'hidden'}></span><span className={hovered === 'agentic' ? 'relative inline-flex rounded-full h-3 w-3 bg-[#5acce6]' : 'relative inline-flex rounded-full h-3 w-3 bg-[#566a84]'}></span></span><BotIcon size={28} className={`${hovered === 'agentic' ? 'text-[#023a7d]' : 'text-[#5acce6]'}`} /></div>}
                        ctaText="Launch Playground"
                        onClick={() => handleModeSelection('agentic')}
                        variant="agentic"
                        className={`${hovered === 'agentic' ? 'scale-105 z-10 border-[#5acce6] shadow-[#5acce6]/12' : hovered === 'standard' ? 'bg-[#151a1f]/50 border-[#3c5d80] opacity-50 scale-95 blur-[1px]' : 'border-[#3c5d80] hover:border-[#5acce6]'}`}
                      />
                    </div>
                </div>
             </div>
        </div>
    );
};

export default CubeStartUp;