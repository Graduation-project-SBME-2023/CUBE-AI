import React, { useState, useRef, useCallback } from 'react';
import { MiniFlowProps } from './types';

const MiniFlow: React.FC<MiniFlowProps> = ({
  nodes,
  edges,
  onNodesChange,
  nodeTypes,
  edgeTypes,
  children,
  readOnly,
}) => {
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [scale, setScale] = useState(1);
  const [isDraggingCanvas, setIsDraggingCanvas] = useState(false);
  const [lastMousePos, setLastMousePos] = useState({ x: 0, y: 0 });
  const [draggingNodeId, setDraggingNodeId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Handle canvas panning with mouse drag
  const handleCanvasMouseDown = (e: React.MouseEvent) => {
    if (e.button === 0 && !(e.target as HTMLElement).closest('.flow-node')) {
      setIsDraggingCanvas(true);
      setLastMousePos({ x: e.clientX, y: e.clientY });
    }
  };

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (isDraggingCanvas) {
        const dx = e.clientX - lastMousePos.x;
        const dy = e.clientY - lastMousePos.y;
        setOffset(prev => ({ x: prev.x + dx, y: prev.y + dy }));
        setLastMousePos({ x: e.clientX, y: e.clientY });
      }

      if (draggingNodeId) {
        const dx = e.movementX / scale;
        const dy = e.movementY / scale;
        onNodesChange(
          nodes.map(n =>
            n.id === draggingNodeId
              ? { ...n, position: { x: n.position.x + dx, y: n.position.y + dy } }
              : n
          )
        );
      }
    },
    [isDraggingCanvas, lastMousePos, draggingNodeId, scale, nodes, onNodesChange]
  );

  const handleMouseUp = () => {
    setIsDraggingCanvas(false);
    setDraggingNodeId(null);
  };

  // Handle zoom with mouse wheel
  const handleWheel = useCallback(
    (e: React.WheelEvent) => {
      e.preventDefault();

      // If holding Ctrl/Cmd, zoom. Otherwise, pan.
      if (e.ctrlKey || e.metaKey) {
        // Zoom
        const container = containerRef.current;
        if (!container) {
          return;
        }

        const rect = container.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;

        // Calculate zoom factor
        const zoomIntensity = 0.002;
        const delta = -e.deltaY;
        const newScale = Math.min(Math.max(0.1, scale * (1 + delta * zoomIntensity)), 3);

        // Adjust offset to zoom towards mouse position
        const scaleRatio = newScale / scale;
        const newOffsetX = mouseX - (mouseX - offset.x) * scaleRatio;
        const newOffsetY = mouseY - (mouseY - offset.y) * scaleRatio;

        setScale(newScale);
        setOffset({ x: newOffsetX, y: newOffsetY });
      } else {
        // Pan with scroll wheel
        setOffset(prev => ({
          x: prev.x - e.deltaX,
          y: prev.y - e.deltaY,
        }));
      }
    },
    [scale, offset]
  );

  const getNodeDimensions = (type: string) => {
    if (type === 'start') {
      return { w: 300, h: 80 };
    }
    return { w: 280, h: 60 };
  };

  return (
    <div
      ref={containerRef}
      className={`relative h-full w-full select-none overflow-hidden bg-[#000000] ${isDraggingCanvas ? 'cursor-grabbing' : 'cursor-grab'}`}
      onMouseDown={handleCanvasMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onWheel={handleWheel}
    >
      {/* Grid background */}
      <div
        className="pointer-events-none absolute inset-0 opacity-25"
        style={{
          backgroundImage: 'radial-gradient(#44626f 1px, transparent 1px)',
          backgroundSize: `${20 * scale}px ${20 * scale}px`,
          backgroundPosition: `${offset.x}px ${offset.y}px`,
        }}
      />

      {/* Canvas content */}
      <div
        className="origin-top-left transform-gpu"
        style={{
          transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
          width: '100%',
          height: '100%',
        }}
      >
        {/* Edges */}
        <svg className="pointer-events-none absolute top-0 left-0 h-[5000px] w-[5000px] overflow-visible">
          {edges.map(edge => {
            const source = nodes.find(n => n.id === edge.source);
            const target = nodes.find(n => n.id === edge.target);
            if (!source || !target) {
              return null;
            }

            const srcDim = getNodeDimensions(source.type);
            const tgtDim = getNodeDimensions(target.type);

            // Source Handle: Bottom Center
            const sx = source.position.x + srcDim.w / 2;
            const sy = source.position.y + srcDim.h;

            // Target Handle: Top Center
            const tx = target.position.x + tgtDim.w / 2;
            const ty = target.position.y;

            const EdgeComponent = edgeTypes[edge.type] || edgeTypes.default;
            return (
              <EdgeComponent
                key={edge.id}
                sourceX={sx}
                sourceY={sy}
                targetX={tx}
                targetY={ty}
                data={edge.data}
              />
            );
          })}
        </svg>

        {/* Nodes */}
        {nodes.map(node => {
          const NodeComponent = nodeTypes[node.type] || nodeTypes.default;
          return (
            <div
              key={node.id}
              className="flow-node absolute cursor-move"
              style={{ transform: `translate(${node.position.x}px, ${node.position.y}px)` }}
              onMouseDown={e => {
                e.stopPropagation();
                setDraggingNodeId(node.id);
              }}
            >
              <NodeComponent
                data={node.data}
                id={node.id}
              />
            </div>
          );
        })}
      </div>

      {/* Overlay UI elements */}
      <div className="pointer-events-none absolute inset-0">{children}</div>

      {/* Zoom indicator */}
      <div className="pointer-events-none absolute bottom-6 right-6">
        <div className="rounded border border-[#3c5d80] bg-[#151a1f]/90 px-3 py-1.5 text-xs text-[#91b9cd] backdrop-blur">
          {Math.round(scale * 100)}%
        </div>
      </div>
    </div>
  );
};

export default MiniFlow;
