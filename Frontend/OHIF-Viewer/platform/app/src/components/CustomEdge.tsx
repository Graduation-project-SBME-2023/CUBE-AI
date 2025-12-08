import React from "react";
import {
  getBezierPath,
  useStore,
  BaseEdge,
  MarkerType,  
  type EdgeProps,
  type ReactFlowState,
} from "@xyflow/react";

export type GetSpecialPathParams = {
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
};

export const getSpecialPath = (
  { sourceX, sourceY, targetX, targetY }: GetSpecialPathParams,
  offset: number
) => {
  const centerX = (sourceX + targetX) / 2;
  const centerY = (sourceY + targetY) / 2;
  return `M ${sourceX} ${sourceY} Q ${centerX} ${
    centerY + offset
  } ${targetX} ${targetY}`;
};

/**
 * CustomEdge component - Not used in canvas-based rendering
 * Kept for backward compatibility
 */
export default function CustomEdge({
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
}: EdgeProps) {
  const isBiDirectionEdge = useStore((s: ReactFlowState) => {
    const edgeExists = s.edges.some(
      (e) =>
        (e.source === target && e.target === source) ||
        (e.target === source && e.source === target)
    );

    return edgeExists;
  });

  const edgePathParams = {
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  };

  let path = '';

  if (isBiDirectionEdge) {
    path = `M ${sourceX} ${sourceY} Q ${(sourceX + targetX) / 2} ${
      (sourceY + targetY) / 2 + (sourceX < targetX ? 25 : -25)
    } ${targetX} ${targetY}`;
  } else {
    [path] = getBezierPath(edgePathParams);
  }

  const arrowMarker = markerEnd || {
    type: MarkerType.ArrowClosed,
    color: '#333',
    width: 150,
    height: 150,
  };

  return <BaseEdge path={path} markerEnd={arrowMarker} />;
}
