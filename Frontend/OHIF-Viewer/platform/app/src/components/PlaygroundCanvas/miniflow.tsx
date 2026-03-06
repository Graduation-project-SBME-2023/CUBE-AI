import React, { useState, useRef, useCallback, useEffect } from 'react';
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

  // Map of nodeId → measured { w, h }
  const [nodeDims, setNodeDims] = useState<Record<string, { w: number; h: number }>>({});
  const nodeRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const containerRef = useRef<HTMLDivElement>(null);

  // Measure node dimensions after every render
  useEffect(() => {
    const measured: Record<string, { w: number; h: number }> = {};
    let changed = false;

    for (const [id, el] of Object.entries(nodeRefs.current)) {
      if (!el) continue;
      const { offsetWidth: w, offsetHeight: h } = el;
      if (nodeDims[id]?.w !== w || nodeDims[id]?.h !== h) {
        changed = true;
      }
      measured[id] = { w, h };
    }

    if (changed) {
      setNodeDims(measured);
    }
  });

  // Prevent default touch/wheel behaviors
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const preventDefaultTouch = (e: TouchEvent) => {
      if (e.touches.length > 1) e.preventDefault();
    };
    const preventDefaultWheel = (e: WheelEvent) => {
      e.preventDefault();
    };

    container.addEventListener('touchstart', preventDefaultTouch, { passive: false });
    container.addEventListener('touchmove', preventDefaultTouch, { passive: false });
    container.addEventListener('wheel', preventDefaultWheel, { passive: false });

    return () => {
      container.removeEventListener('touchstart', preventDefaultTouch);
      container.removeEventListener('touchmove', preventDefaultTouch);
      container.removeEventListener('wheel', preventDefaultWheel);
    };
  }, []);

  // Returns the canvas-space XY where an edge should attach to a node side.
  // Anchoring to measured center keeps edges visually centered for all node heights.
  const getEdgeAnchors = useCallback(
    (nodeId: string, side: 'right' | 'left') => {
      const node = nodes.find(n => n.id === nodeId);
      if (!node) return { x: 0, y: 0 };

      const dim = nodeDims[nodeId];
      const w = dim?.w ?? 220;
      const h = dim?.h ?? 80;

      return {
        x: node.position.x + (side === 'right' ? w : 0),
        y: node.position.y + h / 2,
      };
    },
    [nodes, nodeDims]
  );

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

  const handleWheel = useCallback(
    (e: React.WheelEvent) => {
      if (e.ctrlKey || e.metaKey) {
        const container = containerRef.current;
        if (!container) return;

        const rect = container.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;

        const zoomIntensity = 0.002;
        const delta = -e.deltaY;
        const newScale = Math.min(Math.max(0.1, scale * (1 + delta * zoomIntensity)), 3);

        const scaleRatio = newScale / scale;
        setScale(newScale);
        setOffset({
          x: mouseX - (mouseX - offset.x) * scaleRatio,
          y: mouseY - (mouseY - offset.y) * scaleRatio,
        });
      } else {
        setOffset(prev => ({
          x: prev.x - e.deltaX,
          y: prev.y - e.deltaY,
        }));
      }
    },
    [scale, offset]
  );

  return (
    <div
      ref={containerRef}
      className={`relative h-full w-full select-none overflow-hidden bg-[#000000] ${isDraggingCanvas ? 'cursor-grabbing' : 'cursor-grab'}`}
      style={{ touchAction: 'none' }}
      onMouseDown={handleCanvasMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onWheel={handleWheel}
      onTouchStart={e => {
        if (e.touches.length > 1) e.preventDefault();
      }}
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
            const src = getEdgeAnchors(edge.source, 'right');
            const tgt = getEdgeAnchors(edge.target, 'left');
            const EdgeComponent = edgeTypes[edge.type] || edgeTypes.default;
            return (
              <EdgeComponent
                key={edge.id}
                sourceX={src.x}
                sourceY={src.y}
                targetX={tgt.x}
                targetY={tgt.y}
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
              ref={el => { nodeRefs.current[node.id] = el; }}
              className="flow-node absolute cursor-move"
              style={{ transform: `translate(${node.position.x}px, ${node.position.y}px)` }}
              onMouseDown={e => {
                e.stopPropagation();
                setDraggingNodeId(node.id);
              }}
            >
              <NodeComponent data={node.data} id={node.id} />
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