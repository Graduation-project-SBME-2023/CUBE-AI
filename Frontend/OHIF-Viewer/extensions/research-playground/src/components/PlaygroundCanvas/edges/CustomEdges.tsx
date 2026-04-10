import React from "react";

interface CustomEdgeProps {
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  data?: {
    animated?: boolean;
  };
}

const CustomEdge: React.FC<CustomEdgeProps> = ({ sourceX, sourceY, targetX, targetY, data }) => {
  const isAnimated = data?.animated;

  // Smooth cubic path with adaptive curvature for cleaner visual flow.
  const dx = Math.max(30, Math.abs(targetX - sourceX) * 0.35);
  const c1x = sourceX + dx;
  const c2x = targetX - dx;
  const path = `M${sourceX},${sourceY} C${c1x},${sourceY} ${c2x},${targetY} ${targetX},${targetY}`;

  return (
    <g>
      {/*<defs>*/}
      {/*  <marker*/}
      {/*    id="arrowhead"*/}
      {/*    markerWidth="10"*/}
      {/*    markerHeight="7"*/}
      {/*    refX="10"*/}
      {/*    refY="3.5"*/}
      {/*    orient="auto"*/}
      {/*    markerUnits="strokeWidth"*/}
      {/*  >*/}
      {/*    <polygon points="0 0, 10 3.5, 0 7" fill="#566a84" />*/}
      {/*  </marker>*/}
      {/*</defs>*/}
      {/* Background/Border Line */}
      <path
        d={path}
        stroke="#1f2d40"
        strokeWidth="4"
        fill="none"
        strokeLinecap="round"
      />

      {/* Main Foreground Line */}
      <path
        d={path}
        stroke={isAnimated ? '#5acce6' : '#566a84'}
        strokeWidth="2"
        fill="none"
        strokeDasharray={isAnimated ? '5,5' : 'none'}
        strokeLinecap="round"
        className={isAnimated ? 'animate-flow' : ''}
        markerEnd="url(#arrowhead)"
      />
      <style>{`
        .animate-flow {
          animation: dash 1s linear infinite;
        }
        @keyframes dash {
          to { stroke-dashoffset: -10; }
        }
      `}</style>
    </g>
  );
};

export default CustomEdge;