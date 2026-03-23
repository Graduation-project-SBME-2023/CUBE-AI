import React, { useEffect, useState } from 'react';
import { Target, Cpu, Percent, Check, X, CheckSquare, XSquare, Save, Image as ImageIcon, Loader2 } from 'lucide-react';
import websocketService from '../../../utils/WebsocketService';

// --- Types ---
export interface VolumeGif {
    seriesInstanceUid: string;
    gifUrl: string;
}

export interface ReviewViewProps {
    targetRoi: string;
    selectedModel: string;
    diceScore: number | null;
    volumes: VolumeGif[];
    onSave: (results: { accepted: string[]; rejected: string[] }) => void;
    isLoading: boolean;
}

// --- Main UI Component ---
function ReviewView({
    targetRoi,
    selectedModel,
    diceScore,
    volumes = [], 
    onSave,
    isLoading
}: ReviewViewProps) {
    const [reviewStatus, setReviewStatus] = useState<Record<string, 'accepted' | 'rejected' | 'pending'>>({});

    // Initialize state when volumes load
    useEffect(() => {
        const initialStatus: Record<string, 'accepted' | 'rejected' | 'pending'> = {};
        volumes.forEach(vol => { initialStatus[vol.seriesInstanceUid] = 'pending'; });
        setReviewStatus(initialStatus);
    }, [volumes]);

    const handleToggle = (uid: string, status: 'accepted' | 'rejected') => {
        setReviewStatus(prev => ({ ...prev, [uid]: status }));
    };

    const handleAcceptAll = () => {
        const newStatus: Record<string, 'accepted' | 'rejected' | 'pending'> = {};
        volumes.forEach(v => { newStatus[v.seriesInstanceUid] = 'accepted'; });
        setReviewStatus(newStatus);
    };

    const handleRejectAll = () => {
        const newStatus: Record<string, 'accepted' | 'rejected' | 'pending'> = {};
        volumes.forEach(v => { newStatus[v.seriesInstanceUid] = 'rejected'; });
        setReviewStatus(newStatus);
    };

    const handleSave = () => {
        const accepted = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'accepted');
        const rejected = Object.keys(reviewStatus).filter(uid => reviewStatus[uid] === 'rejected');
        onSave({ accepted, rejected });
    };

    const isEmpty = volumes.length === 0;

    return (
        <div className="p-6 w-full h-screen flex flex-col gap-6 bg-[#000000] overflow-hidden">
            
            {/* MAIN CONTENT AREA (Scrollable Grid + Static Right Panel) */}
            <div className="flex gap-6 flex-1 min-h-0">
                
                {/* LEFT: Main Review Area (Grid & Actions) */}
                <div className="flex-1 bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4 flex flex-col min-h-0 relative overflow-hidden">
                    
                    {/* Top Bar: Bulk Actions */}
                    <div className="flex justify-start gap-3 mb-4 shrink-0 border-b border-[#3c5d80]/50 pb-4">
                        <button 
                            onClick={handleRejectAll}
                            disabled={isEmpty || isLoading}
                            className="px-4 py-2 bg-[#1a1515] border border-red-900/50 text-red-400 hover:bg-red-900/20 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm flex items-center gap-2 transition-colors"
                        >
                            <XSquare size={16} /> Reject All
                        </button>
                        <button 
                            onClick={handleAcceptAll}
                            disabled={isEmpty || isLoading}
                            className="px-4 py-2 bg-[#151f1a] border border-[#6fe5b8]/30 text-[#6fe5b8] hover:bg-[#6fe5b8]/10 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm flex items-center gap-2 transition-colors"
                        >
                            <CheckSquare size={16} /> Accept All
                        </button>
                    </div>

                    {/* Empty / Loading State Overlay */}
                    {(isEmpty || isLoading) && (
                        <div className="absolute inset-0 top-[70px] flex flex-col items-center justify-center z-20 bg-[#151a1f]/90 backdrop-blur-sm">
                            <div className="bg-[#0b1015] p-8 rounded-2xl border border-[#3c5d80] flex flex-col items-center text-center max-w-md shadow-2xl">
                                <div className="w-16 h-16 bg-[#3c5d80]/20 rounded-full flex items-center justify-center mb-4">
                                    <Loader2 size={32} className="text-[#5acce6] animate-spin" />
                                </div>
                                <h3 className="text-xl font-bold text-white mb-2">
                                    {isLoading ? "Fetching Data..." : "Awaiting Segmentation"}
                                </h3>
                                <p className="text-gray-400 text-sm">
                                     Please Wait until the segmentation agent finishes and sends the segmentation results.
                                </p>
                            </div>
                        </div>
                    )}

                    {/* Scrollable GIF Grid */}
                    <div className="flex-1 overflow-y-auto pr-2">
                        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4 pb-4">
                            {volumes.map((volume) => {
                                const status = reviewStatus[volume.seriesInstanceUid];
                                const isAccepted = status === 'accepted';
                                const isRejected = status === 'rejected';

                                return (
                                    <div 
                                        key={volume.seriesInstanceUid} 
                                        className={`relative bg-[#0b1015] rounded-xl overflow-hidden border-2 transition-colors aspect-square flex flex-col ${
                                            isAccepted ? 'border-[#6fe5b8]' : 
                                            isRejected ? 'border-red-500' : 
                                            'border-[#3c5d80]/50'
                                        }`}
                                    >
                                        <div className="absolute top-0 w-full bg-black/80 backdrop-blur-sm p-2 flex justify-between items-center z-10 border-b border-[#3c5d80]/30">
                                            <span className="text-[10px] text-gray-300 font-mono truncate px-1 max-w-[60%]">
                                                {volume.seriesInstanceUid.slice(-8)}
                                            </span>
                                            <div className="flex gap-1 shrink-0">
                                                <button 
                                                    onClick={() => handleToggle(volume.seriesInstanceUid, 'rejected')}
                                                    className={`p-1 rounded ${isRejected ? 'bg-red-500 text-white' : 'text-gray-400 hover:text-red-400 hover:bg-red-900/30'}`}
                                                >
                                                    <X size={14} />
                                                </button>
                                                <button 
                                                    onClick={() => handleToggle(volume.seriesInstanceUid, 'accepted')}
                                                    className={`p-1 rounded ${isAccepted ? 'bg-[#6fe5b8] text-black' : 'text-gray-400 hover:text-[#6fe5b8] hover:bg-[#6fe5b8]/20'}`}
                                                >
                                                    <Check size={14} strokeWidth={3} />
                                                </button>
                                            </div>
                                        </div>
                                        {/* GIF Container */}
                                        <div className="flex-1 w-full bg-black flex items-center justify-center p-2 pt-10">
                                            <img 
                                                src={volume.gifUrl} 
                                                alt="Overlay" 
                                                className="w-full h-full object-contain"
                                                loading="lazy"
                                            />
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                </div>

                {/* RIGHT: Meta Information Panel */}
                <div className="w-64 flex flex-col gap-4 shrink-0">
                    <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4 flex flex-col gap-6 h-full">
                        <div>
                            <h3 className="text-gray-500 text-[10px] uppercase flex items-center gap-1 mb-1">
                                <Target size={12} /> Target ROI
                            </h3>
                            <div className="text-lg font-bold text-[#5acce6]">{targetRoi}</div>
                        </div>
                        <div>
                            <h3 className="text-gray-500 text-[10px] uppercase flex items-center gap-1 mb-1">
                                <Cpu size={12} /> Selected Model
                            </h3>
                            <div className="text-md font-bold text-white">{selectedModel}</div>
                        </div>
                        <div>
                            <h3 className="text-gray-500 text-[10px] uppercase flex items-center gap-1 mb-1">
                                <Percent size={12} /> Benchmarking Dice Score
                            </h3>
                            <div className="text-xl font-mono font-bold text-[#6fe5b8]">
                                {diceScore !== null ? diceScore.toFixed(3) : '-'}
                            </div>
                        </div>
                    </div>
                </div>

            </div>

            {/* BOTTOM: Save Button Panel */}
            <div className="w-full flex justify-center shrink-0 pt-2">
                <button 
                    onClick={handleSave}
                    disabled={isEmpty || isLoading}
                    className="px-12 py-3 bg-[#5acce6] hover:bg-[#4ab5cc] disabled:bg-[#3c5d80] disabled:text-gray-400 disabled:cursor-not-allowed text-black font-bold rounded-full flex items-center gap-2 transition-colors shadow-[0_0_15px_rgba(90,204,230,0.3)]"
                >
                    <Save size={18} /> Save Review Results
                </button>
            </div>
        </div>
    );
}

// --- Container (Centralized WS Connection) ---
interface FeaturesViewContainerProps {
    featuresData?: any;
    importanceData?: any;
}

export default function ReviewContainer({ featuresData, importanceData }: FeaturesViewContainerProps) {
    const [data, setData] = useState({
        targetRoi: "-",
        selectedModel: "-",
        diceScore: null,
        volumes: []
    });

    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        console.log("FeaturesView received props:", { featuresData, importanceData });

        if (importanceData?.volumes?.length > 0) {
            setData({
                targetRoi: importanceData.targetRoi || "-",
                selectedModel: importanceData.selectedModel || "-",
                diceScore: importanceData.diceScore ?? null,
                volumes: importanceData.volumes
            });
            setIsLoading(false);
            return;
        }

        if (featuresData?.volumes?.length > 0) {
            setData({
                targetRoi: featuresData.targetRoi || "-",
                selectedModel: featuresData.selectedModel || "-",
                diceScore: featuresData.diceScore ?? null,
                volumes: featuresData.volumes
            });
            setIsLoading(false);
            return;
        }

        setIsLoading(true);
    }, [featuresData, importanceData]);
    const handleSaveResults = (results: { accepted: string[]; rejected: string[] }) => {
        try {
            // We stringify the results directly because your backend does:
            // json.loads(user_response_raw) and looks directly for the "accepted" key.
            const messagePayload = JSON.stringify({
                accepted: results.accepted,
                rejected: results.rejected
            });

            // Send directly through the websocket to wake up the backend agent
            websocketService.send(messagePayload);
            
            // Optional: clear the data or show a loading state while the agent resumes
            alert("Review saved successfully! The AI Module will now resume.");
            
        } catch (error) {
            console.error("Failed to send review results over WebSocket:", error);
            alert("Failed to connect to the AI agent. Please try again.");
        }
    };

    return (
        <ReviewView 
            targetRoi={data.targetRoi}
            selectedModel={data.selectedModel}
            diceScore={data.diceScore}
            volumes={data.volumes} 
            onSave={handleSaveResults}
            isLoading={isLoading}
        />
    );
}