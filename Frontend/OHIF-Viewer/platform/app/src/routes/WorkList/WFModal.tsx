import React, { useEffect } from 'react';
import PropTypes from 'prop-types';

const Modal = ({ isOpen, onClose, onConfirm, workFlowName, setWorkflowName, errorMessage }) => {
  const inputRef = React.useRef(null);

  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isOpen]);

  const handleKeyDown = (event) => {
    if (event.key === 'Enter') {
      event.preventDefault(); // Prevent form submission
      onConfirm();
    } else if (event.key === 'Escape') {
      event.preventDefault(); // Prevent default escape behavior
      onClose();
    }
  };

  if (!isOpen) return null; // Render nothing if modal is not open

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onKeyDown={handleKeyDown}
      tabIndex={-1}
    >
      <div className="z-60 w-[400px] rounded-lg border border-secondary-light bg-primary-dark p-6 shadow-2xl">
        <h2 className="mb-5 text-lg font-bold text-white">Create Workflow</h2>
        <input
          type="text"
          ref={inputRef}
          value={workFlowName}
          onChange={e => setWorkflowName(e.target.value)}
          placeholder="Workflow Name"
          className="mb-5 w-full rounded border border-secondary-light bg-black p-2.5 text-[14px] text-white placeholder-gray-500 outline-none transition-all focus:border-primary-active focus:ring-1 focus:ring-primary-active"
        />
        {errorMessage && <p className="mb-5 text-[13px] text-[#e05a5a]">{errorMessage}</p>}
        <div className="flex justify-end gap-3">
          <button
            onClick={onClose}
            className="rounded-[4px] border border-secondary-light bg-transparent px-5 py-2 text-[13px] font-medium text-white transition-colors hover:bg-secondary-main hover:text-primary-active"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="rounded-[4px] border-none bg-[#1a4a8a] px-6 py-2 text-[13px] font-bold text-[#c8d8f0] transition-all duration-200 hover:bg-[#1e5aaa] active:scale-[0.98]"
          >
            Create
          </button>
        </div>
      </div>
    </div>
  );
};

Modal.propTypes = {
  isOpen: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onConfirm: PropTypes.func.isRequired,
  workFlowName: PropTypes.string.isRequired,
  setWorkflowName: PropTypes.func.isRequired,
};

export default Modal;
