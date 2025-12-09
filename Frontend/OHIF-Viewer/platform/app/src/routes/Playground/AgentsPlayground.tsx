import React from "react";
import PlaygroundCanvas from "../../components/PlaygroundCanvas";
import websocketService from "../../utils/WebsocketService";

/**
 * AgentsPlayground Component (Refactored)
 * 
 * CONSOLIDATION SUMMARY:
 * This component has been significantly refactored to eliminate code duplication with PlaygroundCanvas.
 * 
 * CHANGES:
 * --------
 * 1. Removed duplicate buildGraph() function
 *    - Moved to PlanTransformationUtils.ts as transformPlanToFlow()
 *    - Extracted transformPlanToFlowLegacy() variant for legacy compatibility
 * 
 * 2. Removed duplicate state management
 *    - Nodes, edges, and plan state now managed exclusively in PlaygroundCanvas
 *    - Reduces prop drilling and state synchronization complexity
 * 
 * 3. Removed duplicate WebSocket subscription
 *    - WebSocket now subscribed/managed only in PlaygroundCanvas
 * 
 * 4. Removed duplicate edit/delete/approve logic
 *    - All workflow controls now in PlaygroundCanvas
 * 
 * 5. Enhanced WebsocketService
 *    - Added isConnected() method (was missing)
 *    - Used by PlaygroundCanvas to determine execution mode
 * 
 * RESULT:
 * -------
 * AgentsPlayground is now a minimal wrapper that initializes WebSocket and renders PlaygroundCanvas.
 * All plan management logic consolidated into a single component for easier maintenance.
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
