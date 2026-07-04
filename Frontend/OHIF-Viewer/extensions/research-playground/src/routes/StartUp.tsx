import React, { useState } from 'react';
import { Bot, Layout, Box } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import Card, { SystemVariant } from '../components/ui/Card';
import Badge from '../components/shared/Badge';
import theme from '../components/ui/theme';

// Icon aliases for lucide-react to satisfy JSX typing in this project
const BoxIcon: any = Box;
const LayoutIcon: any = Layout;
const BotIcon: any = Bot;

const DEFAULT_GRADIENT = 'radial-gradient(circle at 50% 50%, rgba(255, 255, 255, 0.03), transparent 70%)';
const GRID_TEXTURE = {
  backgroundImage: 'radial-gradient(#44626f 1px, transparent 1px)',
  backgroundSize: '30px 30px',
};

const getBackgroundGradient = (hovered: SystemVariant | null) => {
  if (hovered === 'standard') return theme.gradients.standard;
  if (hovered === 'agentic') return theme.gradients.agentic;
  return DEFAULT_GRADIENT;
};

const getCardClassName = (hovered: SystemVariant | null, mode: SystemVariant) => {
  if (hovered && hovered !== mode) {
    return 'bg-[#151a1f]/50 border-[#3c5d80] opacity-50 scale-95 blur-[1px]';
  }

  if (mode === 'standard') {
    return hovered === mode
      ? 'bg-[#151a1f] border-[#348cfd] shadow-[#348cfd]/20 scale-105 z-10'
      : 'bg-[#151a1f] border-[#3c5d80] hover:border-[#348cfd]';
  }

  return hovered === mode
    ? 'bg-[#151a1f] border-[#5acce6] shadow-[#5acce6]/12 scale-105 z-10'
    : 'bg-[#151a1f] border-[#3c5d80] hover:border-[#5acce6]';
};

// ==========================================
// CUBE STARTUP COMPONENT (Main Export)
// ==========================================
const CubeStartUp = () => {
  const [hovered, setHovered] = useState<SystemVariant | null>(null);

    // Handle mode selection and navigation
    const handleModeSelection = (mode: SystemVariant) => {
        console.log(`[CUBE] Mode selected: ${mode}`);
        
        if (mode === 'standard') {
            // Navigate to worklist for standard pipeline
            window.location.href = '/worklist';
        } else if (mode === 'agentic') {
            // Navigate to agentic home
            window.location.href = '/agentic-home';
        }
    };

    return (
        <div className="relative flex h-screen w-full items-center justify-center overflow-hidden bg-black font-sans text-white">
             <div className="pointer-events-none absolute inset-0 opacity-20" style={GRID_TEXTURE} />
             <div
                className="pointer-events-none absolute inset-0 transition-all duration-700 ease-in-out opacity-60"
                style={{ background: getBackgroundGradient(hovered) }}
             />

             <div className="z-10 w-full max-w-4xl px-6">
                <div className="mb-12 text-center space-y-4">
                    <Badge tone="info">
                      <span className="flex items-center gap-2">
                        <BoxIcon size={12} /> CUBE Research Platform
                      </span>
                    </Badge>
                    <div className="flex items-center justify-center gap-4">
                        <div className="rounded-xl border border-[#023a7d] bg-[#151a1f] p-2 shadow-lg shadow-[#5acce6]/20">
                            <BoxIcon size={42} className="text-[#5acce6]" strokeWidth={1.5} />
                        </div>
                        <h1 className="text-5xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-white to-[#91b9cd]">
                            CUBE
                        </h1>
                    </div>
                    <p className="text-[#91b9cd] text-xl font-medium tracking-wide">Your AI Research Assistant</p>
                    <p className="text-[#566a84] text-sm max-w-xl mx-auto pt-2">
                        Select an operation mode to begin your research session.
                    </p>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                    <div onMouseEnter={() => setHovered('standard')} onMouseLeave={() => setHovered(null)}>
                      <Card
                        title="Standard Pipeline"
                        description="View and manage your medical imaging cases, extract regions of interest, build your datasets faster through interactive AI assisted segmentation, and build your custom AI models with ease."
                        icon={<LayoutIcon size={28} className={hovered === 'standard' ? 'text-white' : 'text-[#5acce6]'} />}
                        ctaText="Enter Worklist"
                        onClick={() => handleModeSelection('standard')}
                        variant="standard"
                        className={getCardClassName(hovered, 'standard')}
                      />
                    </div>

                    <div onMouseEnter={() => setHovered('agentic')} onMouseLeave={() => setHovered(null)}>
                      <Card
                        title="Agentic Researcher"
                        description="Perform end-to-end research tasks through natural language instructions, leveraging the full capabilities of CUBE's AI agents to assist you in data analysis, hypothesis testing, and iterative experimentation."
                        icon={<BotIcon size={28} className={hovered === 'agentic' ? 'text-[#023a7d]' : 'text-[#5acce6]'} />}
                        ctaText="Launch Playground"
                        onClick={() => handleModeSelection('agentic')}
                        variant="agentic"
                        className={getCardClassName(hovered, 'agentic')}
                      />
                    </div>
                </div>
             </div>
        </div>
    );
};

export default CubeStartUp;