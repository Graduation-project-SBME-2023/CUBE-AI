import React, { useState } from "react";
import { X } from "lucide-react";

export function StartNode({ data, isConnectable }) {
    return (
        <div
            style={{
                background: '#4CAF50',
                color: 'white',
                padding: 10,
                borderRadius: '50%',
                width: 60,
                height: 60,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontWeight: 'bold',
                cursor: "default",
            }}
        >
            {data.label || 'Start'}
        </div>
    )
}

export function AgentNode({ data, isConnectable, id }) {
  const { name, description, editable, onDelete, onEdit, status = 'pending', logs = [], isExpanded = false } = data;
  const [hovered, setHovered] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editText, setEditText] = useState(description || '');

  // Status-based colors
  let bgColor = "#94a3b8";
  let borderColor = "#94a3b8";
  let statusIndicator = "⚪";
  let isGlowing = false;

  if (status === 'pending') {
    bgColor = "#94a3b8";
    borderColor = "#94a3b8";
    statusIndicator = "⚪";
  } else if (status === 'active') {
    bgColor = "#2563eb";
    borderColor = "#1d4ed8";
    statusIndicator = "🔵";
    isGlowing = true;
  } else if (status === 'completed') {
    bgColor = "#10b981";
    borderColor = "#059669";
    statusIndicator = "✅";
  }

  const handleSaveEdit = () => {
    onEdit?.(id, editText);
    setIsEditing(false);
  };

  return (
    <div
      className={`relative transition-all duration-300 rounded-xl shadow-md text-white flex flex-col items-center justify-center px-4 ${isGlowing ? 'animate-pulse' : ''}`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: hovered ? bgColor : bgColor, 
        borderColor: borderColor,
        border: `2px solid ${borderColor}`,
        width: 180,
        minHeight: hovered || isExpanded ? 'auto' : 60,
        maxHeight: isExpanded ? '300px' : 'auto',
        overflow: "hidden",
        textAlign: "center",
        cursor: "default",
        boxShadow: isGlowing ? `0 0 20px rgba(37, 99, 235, 0.8), inset 0 0 20px rgba(37, 99, 235, 0.3)` : 'none',
        padding: '12px',
      }}
    >
      {/* Delete Button - Only in edit mode */}
      {editable && !isEditing && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete?.(id);
          }}
          className="absolute -top-1 -right-1 bg-red-500 hover:bg-red-600 text-white rounded-full p-1 shadow-md"
          title="Delete step"
        >
          <X size={14} />
        </button>
      )}

      {/* Status Indicator */}
      <div style={{ fontSize: '16px', marginBottom: '4px' }}>{statusIndicator}</div>

      {/* Title */}
      <div style={{ fontWeight: 'bold', fontSize: '14px', marginBottom: '4px' }}>{name}</div>

      {/* Hover/Expanded Content */}
      {(hovered || isExpanded) && (
        <div style={{ width: '100%', fontSize: '12px' }}>
          {/* Description - Edit Mode */}
          {isEditing ? (
            <div style={{ marginBottom: '8px' }}>
              <textarea
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                style={{
                  width: '100%',
                  minHeight: '60px',
                  padding: '6px',
                  borderRadius: '4px',
                  border: 'none',
                  fontSize: '11px',
                  fontFamily: 'monospace',
                  color: '#333',
                }}
                onClick={(e) => e.stopPropagation()}
              />
              <div style={{ display: 'flex', gap: '4px', marginTop: '6px' }}>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleSaveEdit();
                  }}
                  style={{
                    flex: 1,
                    padding: '4px',
                    background: '#10b981',
                    color: 'white',
                    border: 'none',
                    borderRadius: '3px',
                    fontSize: '10px',
                    cursor: 'pointer',
                  }}
                >
                  Save
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setIsEditing(false);
                    setEditText(description || '');
                  }}
                  style={{
                    flex: 1,
                    padding: '4px',
                    background: '#ef4444',
                    color: 'white',
                    border: 'none',
                    borderRadius: '3px',
                    fontSize: '10px',
                    cursor: 'pointer',
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <>
              {/* Description - View Mode */}
              <div style={{
                background: 'rgba(0,0,0,0.2)',
                padding: '6px',
                borderRadius: '4px',
                marginBottom: '8px',
                lineHeight: '1.3',
                maxHeight: '60px',
                overflow: 'auto',
              }}>
                {description || 'No description'}
              </div>

              {/* Edit Button - Only in edit mode */}
              {editable && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setIsEditing(true);
                  }}
                  style={{
                    width: '100%',
                    padding: '4px',
                    background: '#f59e0b',
                    color: 'white',
                    border: 'none',
                    borderRadius: '3px',
                    fontSize: '11px',
                    cursor: 'pointer',
                    marginBottom: '6px',
                  }}
                >
                  Edit Description
                </button>
              )}
            </>
          )}

          {/* Logs Display - Only when executing and has logs */}
          {logs.length > 0 && (
            <div style={{
              background: 'rgba(255,255,255,0.1)',
              padding: '6px',
              borderRadius: '4px',
              fontSize: '10px',
              fontFamily: 'monospace',
              maxHeight: '80px',
              overflow: 'auto',
              lineHeight: '1.4',
              color: '#fef08a',
            }}>
              <div style={{ fontWeight: 'bold', marginBottom: '4px', color: '#fbbf24' }}>
                Logs (last 5):
              </div>
              {logs.slice(-5).map((log, idx) => (
                <div key={idx}>&gt; {log}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
