import React from "react";
import PlaygroundCanvas from "../../components/PlaygroundCanvas/index";
import websocketService from "../../utils/WebsocketService";



function AgentsPlayground() {
  return (
    <div style={{ position: "relative", width: "100vw", height: "100vh" }}>
      {/* PlaygroundCanvas handles all plan visualization, editing, and execution */}
      <PlaygroundCanvas />
    </div>
  );
}

export default AgentsPlayground;
