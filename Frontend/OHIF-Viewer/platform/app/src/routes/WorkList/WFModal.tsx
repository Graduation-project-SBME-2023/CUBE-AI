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
      className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50"
      onKeyDown={handleKeyDown}
      tabIndex={-1} // Make the div focusable
    >
      <div className="z-60 rounded bg-white p-4 shadow-lg">
        <h2 className="mb-4 text-lg font-bold">Create Workflow</h2>
        <input
          type="text"
          ref={inputRef}
          value={workFlowName}
          onChange={e => setWorkflowName(e.target.value)}
          placeholder="Workflow Name"
          className="mb-4 w-full rounded border p-2"
        />
        {errorMessage && <p className="text-red-500">{errorMessage}</p>}
        <div className="flex justify-end">
          <button
            onClick={onClose}
            className="mr-2 rounded bg-gray-300 px-4 py-2"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="rounded bg-blue-600 px-4 py-2 text-white"
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
