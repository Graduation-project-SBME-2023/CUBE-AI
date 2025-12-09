import React from "react";
import PlaygroundCanvas from "../../components/PlaygroundCanvas";
import websocketService from "../../utils/WebsocketService";

/**
 * AgentsPlayground Component (Refactored)
 * 
 * CONSOLIDATION SUMMARY:
 * - Removed duplicate buildGraph() function (now in PlanTransformationUtils.ts)
 * - Removed duplicate state management (nodes, edges, originalData)
 * - Removed duplicate WebSocket subscription handling
 * - Removed duplicate edit/delete/approve logic
 * 
 * This component now serves as a minimal wrapper around PlaygroundCanvas,
 * which handles all plan visualization, editing, execution, and WebSocket communication.
 * 
 * KEY REFACTORING CHANGES:
 * 1. Extracted transformPlanToFlow() to PlanTransformationUtils.ts
 *    - Shared by both PlaygroundCanvas and AgentsPlayground
 *    - Two variants: transformPlanToFlow (modern) and transformPlanToFlowLegacy (legacy)
 * 
 * 2. Consolidated state management in PlaygroundCanvas
 *    - Nodes, edges, and plan state now managed in a single component
 *    - Reduces prop drilling and state synchronization complexity
 * 
 * 3. Added isConnected() method to WebsocketService
 *    - Was missing before, needed by both components
 * 
 * 4. Simplified AgentsPlayground to just initialize WebSocket and render PlaygroundCanvas
 *    - All edit/delete/approve logic now in PlaygroundCanvas
 *    - Agent-specific styling/controls can be added here if needed
 */

function AgentsPlayground() {
  // Initialize WebSocket connection on mount
  React.useEffect(() => {
    websocketService.connect();
  }, []);

  return (
    <div style={{ position: "relative", width: "100vw", height: "100vh" }}>
      {/* PlaygroundCanvas handles all plan visualization, editing, and execution */}
      <PlaygroundCanvas />
    </div>
  );
}

export default AgentsPlayground;
