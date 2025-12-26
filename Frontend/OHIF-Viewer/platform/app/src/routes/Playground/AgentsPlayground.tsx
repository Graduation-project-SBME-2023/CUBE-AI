import React from "react";
import PlaygroundCanvas from "../../components/PlaygroundCanvas/index";
import websocketService from "../../utils/WebsocketService";



function AgentsPlayground() {
  // Initialize WebSocket connection on mount
  React.useEffect(() => {
    websocketService.connect();
  }, []);

  React.useEffect(() => {
    const handleWS = async (data: any) => {

    };

    const unsubscribe = websocketService.subscribe(handleWS);

    return () => {
      unsubscribe();
    }
  })

  return (
    <div style={{ position: "relative", width: "100vw", height: "100vh" }}>
      {/* PlaygroundCanvas handles all plan visualization, editing, and execution */}
      <PlaygroundCanvas />
    </div>
  );
}

export default AgentsPlayground;
