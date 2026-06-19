import React from 'react';
import { CheckCircle, XCircle } from 'lucide-react';

interface PlanApprovalModalProps {
  isOpen: boolean;
  onApprove: () => void;
  onReject: () => void;
  isLoading?: boolean;
}

export const PlanApprovalModal: React.FC<PlanApprovalModalProps> = ({
  isOpen,
  onApprove,
  onReject,
  isLoading = false,
}) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/70 animate-in fade-in duration-200">
      <div className="w-[90%] max-w-[500px] overflow-hidden rounded-lg border border-[#404040] bg-[#1a1a1a] shadow-[0_20px_60px_rgba(0,0,0,0.5)] animate-in slide-in-from-bottom-3 duration-300">
        <div className="border-b border-[#404040] px-6 pb-4 pt-6">
          <h2 className="text-lg font-semibold text-white">Review Pipeline Plan</h2>
        </div>
        
        <div className="px-6 py-6 text-[#dadada]">
          <p className="mb-3 text-sm leading-6">
            Your research pipeline has been designed. Would you like to approve this plan and proceed with execution?
          </p>
          <p className="text-[13px] italic text-[#808080]">
            You can review the pipeline flow in the canvas above before confirming.
          </p>
        </div>

        <div className="flex justify-end gap-3 border-t border-[#404040] px-6 py-4">
          <button
            className="flex items-center gap-2 rounded-md border border-[#404040] px-[18px] py-2.5 text-sm font-medium text-[#ff6b6b] transition-all hover:border-[#ff6b6b] hover:bg-[rgba(255,107,107,0.1)] hover:text-[#ff8787] disabled:cursor-not-allowed disabled:opacity-50"
            onClick={onReject}
            disabled={isLoading}
          >
            <XCircle size={18} />
            <span>Reject & Modify</span>
          </button>
          <button
            className="flex items-center gap-2 rounded-md border border-[#0944b3] bg-[#0944b3] px-[18px] py-2.5 text-sm font-medium text-white transition-all hover:border-[#0d58d6] hover:bg-[#0d58d6] disabled:cursor-not-allowed disabled:opacity-50"
            onClick={onApprove}
            disabled={isLoading}
          >
            <CheckCircle size={18} />
            <span>{isLoading ? 'Approving...' : 'Approve & Execute'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};

export default PlanApprovalModal;
