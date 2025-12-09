// CustomEdge.jsx
import React from "react";

const CustomEdge = ({ sourceX, sourceY, targetX, targetY, data }) => {
  const isAnimated = data?.animated;

  // Offset curve slightly so wires don't overlap nodes
  const offset = 30;
  const startX = sourceX + offset;
  const endX = targetX + offset;
  const startY = sourceY;
  const endY = targetY;

  // Mid control points for smooth curved path
  const cX = (sourceX + targetX) / 2;

  const path = `M${startX},${startY} 
                C${cX},${sourceY} 
                 ${cX},${targetY} 
                 ${endX},${endY}`;

  return (
    <g>
      {/* Background thick line */}
      <path d={path} stroke="#1f2d40" strokeWidth="4" fill="none" />

      {/* Foreground animated or static line */}
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
