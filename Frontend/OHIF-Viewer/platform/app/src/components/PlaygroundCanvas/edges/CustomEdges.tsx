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

  const offset = 30;
  const startX = sourceX + offset;
  const endX = targetX; 
  const startY = sourceY;
  const endY = targetY;

  const cX = (sourceX + targetX) / 2;

  const path = `M${startX},${startY} 
                C${cX},${sourceY} 
                 ${cX},${targetY} 
                 ${endX},${endY}`;

  return (
    <g>
      <path d={path} stroke="#1f2d40" strokeWidth="4" fill="none" />
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