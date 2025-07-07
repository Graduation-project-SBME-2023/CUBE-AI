import React from 'react';
import PropTypes from 'prop-types';

const Modal = ({ isOpen, onClose, onConfirm, workFlowName, setWorkflowName, errorMessage }) => {
  if (!isOpen) return null; // Render nothing if modal is not open

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50">
      <div className="z-60 rounded bg-white p-4 shadow-lg">
        <h2 className="mb-4 text-lg font-bold">Create Workflow</h2>
        <input
          type="text"
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
