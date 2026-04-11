import React, { useState, useRef } from 'react';
import './MiniFlow.css';

const MiniFlow = ({ nodes, edges, onNodesChange, nodeTypes, edgeTypes, children, readOnly }) => {
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [scale, setScale] = useState(1);
  const [isDraggingCanvas, setIsDraggingCanvas] = useState(false);
  const [lastMousePos, setLastMousePos] = useState({ x: 0, y: 0 });
  const [draggingNodeId, setDraggingNodeId] = useState(null);
  const containerRef = useRef(null);

  const handleCanvasMouseDown = (e) => {
    if (e.button === 0 && !e.target.closest('.flow-node')) {
      setIsDraggingCanvas(true);
      setLastMousePos({ x: e.clientX, y: e.clientY });
    }
  };
  const handleMouseMove = (e) => {
    if (isDraggingCanvas) {
      const dx = e.clientX - lastMousePos.x;
      const dy = e.clientY - lastMousePos.y;
      setOffset(prev => ({ x: prev.x + dx, y: prev.y + dy }));
      setLastMousePos({ x: e.clientX, y: e.clientY });
    }
    if (draggingNodeId && !readOnly) {
      const dx = (e.movementX) / scale;
      const dy = (e.movementY) / scale;
      onNodesChange(nodes.map(n => n.id === draggingNodeId ? { ...n, position: { x: n.position.x + dx, y: n.position.y + dy } } : n));
    }
  };
  const handleMouseUp = () => {
    setIsDraggingCanvas(false);
    setDraggingNodeId(null);
  };

  return (
    <div 
      ref={containerRef}
      className={`w-miniflow-container${readOnly ? ' cursor-default' : ' cursor-grab active:cursor-grabbing'}`}
      onMouseDown={handleCanvasMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      <div 
        className="w-miniflow-canvas"
        style={{
          backgroundImage: 'radial-gradient(#44626f 1px, transparent 1px)',
          backgroundSize: `${20 * scale}px ${20 * scale}px`,
          backgroundPosition: `${offset.x}px ${offset.y}px`
        }}
      />
      <div 
        className="w-miniflow-transform"
        style={{ 
          transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
        }}
      >
        <svg className="w-miniflow-svg">
          {edges.map(edge => {
            const source = nodes.find(n => n.id === edge.source);
            const target = nodes.find(n => n.id === edge.target);
            if (!source || !target) return null;
            const sx = source.position.x + (140); 
            const sy = source.position.y + (source.type === 'start' ? 40 : 60); 
            const tx = target.position.x + (140);
            const ty = target.position.y;
            const EdgeComponent = edgeTypes[edge.type] || edgeTypes.default;
            return (
              <EdgeComponent 
                key={edge.id}
                id={edge.id}
                sourceX={sx} sourceY={sy}
                targetX={tx} targetY={ty}
                data={edge.data}
              />
            );
          })}
        </svg>
        {nodes.map(node => {
          const NodeComponent = nodeTypes[node.type] || nodeTypes.default;
          return (
            <div
              key={node.id}
              className="w-miniflow-node"
              style={{ transform: `translate(${node.position.x}px, ${node.position.y}px)` }}
              onMouseDown={(e) => {
                if (!readOnly) {
                  e.stopPropagation();
                  setDraggingNodeId(node.id);
                }
              }}
            >
              <NodeComponent data={node.data} id={node.id} mode={readOnly ? 'executing' : 'planning'} />
            </div>
          );
        })}
      </div>
      <div className="w-miniflow-overlay">
        {children}
      </div>
    </div>
  );
};
export default MiniFlow;
