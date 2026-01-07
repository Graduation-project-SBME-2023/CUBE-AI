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

  // STRAIGHT LINE PATH
  // Simply draw a line from Source(Bottom) to Target(Top)
  const path = `M${sourceX},${sourceY} L${targetX},${targetY}`;

  return (
    <g>
      {/* Background/Border Line */}
      <path d={path} stroke="#1f2d40" strokeWidth="4" fill="none" />
      
      {/* Main Foreground Line */}
      <path
        d={path}
        stroke={isAnimated ? "#5acce6" : "#566a84"}
        strokeWidth="2"
        fill="none"
        strokeDasharray={isAnimated ? "5,5" : "none"}
        className={isAnimated ? "animate-flow" : ""}
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