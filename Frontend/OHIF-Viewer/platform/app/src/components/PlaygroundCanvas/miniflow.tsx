import React, { useState, useRef } from 'react';
import { MiniFlowProps } from './types';

const MiniFlow: React.FC<MiniFlowProps> = ({ 
  nodes, edges, onNodesChange, nodeTypes, edgeTypes, children, readOnly 
}) => {
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [scale] = useState(1);
  const [isDraggingCanvas, setIsDraggingCanvas] = useState(false);
  const [lastMousePos, setLastMousePos] = useState({ x: 0, y: 0 });
  const [draggingNodeId, setDraggingNodeId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleCanvasMouseDown = (e: React.MouseEvent) => {
    if (e.button === 0 && !(e.target as HTMLElement).closest('.flow-node')) {
      setIsDraggingCanvas(true);
      setLastMousePos({ x: e.clientX, y: e.clientY });
    }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDraggingCanvas) {
      const dx = e.clientX - lastMousePos.x;
      const dy = e.clientY - lastMousePos.y;
      setOffset(prev => ({ x: prev.x + dx, y: prev.y + dy }));
      setLastMousePos({ x: e.clientX, y: e.clientY });
    }

    if (draggingNodeId && !readOnly) {
      const dx = (e.movementX) / scale;
      const dy = (e.movementY) / scale;
      onNodesChange(nodes.map(n => 
        n.id === draggingNodeId 
          ? { ...n, position: { x: n.position.x + dx, y: n.position.y + dy } } 
          : n
      ));
    }
  };

  const handleMouseUp = () => {
    setIsDraggingCanvas(false);
    setDraggingNodeId(null);
  };

  return (
    <div 
      ref={containerRef}
      className={`w-full h-full bg-[#000000] relative overflow-hidden select-none ${readOnly ? 'cursor-default' : 'cursor-grab active:cursor-grabbing'}`}
      onMouseDown={handleCanvasMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      <div 
        className="absolute inset-0 pointer-events-none opacity-25"
        style={{
          backgroundImage: 'radial-gradient(#44626f 1px, transparent 1px)',
          backgroundSize: `${20 * scale}px ${20 * scale}px`,
          backgroundPosition: `${offset.x}px ${offset.y}px`
        }}
      />

      <div 
        className="transform-gpu origin-top-left"
        style={{ 
          transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
          width: '100%', 
          height: '100%' 
        }}
      >
        <svg className="absolute top-0 left-0 w-[5000px] h-[5000px] pointer-events-none overflow-visible">
          {edges.map(edge => {
            const source = nodes.find(n => n.id === edge.source);
            const target = nodes.find(n => n.id === edge.target);
            if (!source || !target) return null;

            const sx = source.position.x + (edge.sourceHandleX || 150);
            const sy = source.position.y + 30; 
            const tx = target.position.x;
            const ty = target.position.y + 30;
            
            const EdgeComponent = edgeTypes[edge.type] || edgeTypes.default;
            return (
              <EdgeComponent 
                key={edge.id}
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
              className="absolute flow-node"
              style={{ transform: `translate(${node.position.x}px, ${node.position.y}px)` }}
              onMouseDown={(e) => {
                if (!readOnly) {
                  e.stopPropagation();
                  setDraggingNodeId(node.id);
                }
              }}
            >
              <NodeComponent data={node.data} id={node.id} />
            </div>
          );
        })}
      </div>
      
      <div className="absolute inset-0 pointer-events-none">
        {children}
      </div>
    </div>
  );
};

export default MiniFlow;