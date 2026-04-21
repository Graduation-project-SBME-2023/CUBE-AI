import React from 'react';
import { CheckCircle, XCircle } from 'lucide-react';
import './PlanApprovalModal.css';

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
    <div className="plan-approval-modal-overlay">
      <div className="plan-approval-modal">
        <div className="plan-approval-modal-header">
          <h2>Review Pipeline Plan</h2>
        </div>
        
        <div className="plan-approval-modal-content">
          <p>
            Your research pipeline has been designed. Would you like to approve this plan and proceed with execution?
          </p>
          <p className="plan-approval-modal-hint">
            You can review the pipeline flow in the canvas above before confirming.
          </p>
        </div>

        <div className="plan-approval-modal-actions">
          <button
            className="plan-approval-btn plan-approval-btn-reject"
            onClick={onReject}
            disabled={isLoading}
          >
            <XCircle size={18} />
            <span>Reject & Modify</span>
          </button>
          <button
            className="plan-approval-btn plan-approval-btn-approve"
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
