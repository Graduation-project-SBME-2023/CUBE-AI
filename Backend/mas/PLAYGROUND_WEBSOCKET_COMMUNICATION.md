# PlaygroundCanvas WebSocket Communication Guide

## Overview

The PlaygroundCanvas component communicates with the backend (Planner) via WebSocket. This guide shows how to integrate the Planner with the PlaygroundCanvas by sending and receiving messages through the WebSocket connection.

## Message Flow Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Frontend (React)                             │
│                      PlaygroundCanvas Component                      │
│                                                                       │
│  handleServerMessage(msg) → Routes messages by type:                │
│  - type: 'plan' → updatePlan()                                      │
│  - type: 'node' → updateNodeData()                                  │
│                                                                       │
│  sendPlanToBackend() → Sends state changes:                         │
│  - type: 'PLAN_UPDATE' → Full plan structure                        │
│  - type: 'START_WORKFLOW' → Execution signal                        │
└─────────────────────────────────────────────────────────────────────┘
                                  ↕
                          WebSocket (/ws)
                                  ↕
┌─────────────────────────────────────────────────────────────────────┐
│                         Backend (FastAPI)                            │
│                    Planner + WebSocket Handler                      │
│                                                                       │
│  Receives messages via websocket_endpoint:                          │
│  - PLAN_UPDATE: Update plan in memory/database                      │
│  - START_WORKFLOW: Trigger plan.run() execution                     │
│                                                                       │
│  Sends messages via send_ws_message():                              │
│  - type: 'plan' → Send plan structure to frontend                   │
│  - type: 'node' → Send node updates (status, logs)                  │
│  - type: 'info' → Send log messages                                 │
│  - type: 'msg' → Send user-facing messages                          │
└─────────────────────────────────────────────────────────────────────┘
```

## 1. Sending Plan to Frontend (Backend → Frontend)

### When to Send
- User creates/loads a plan
- Plan structure changes
- Plan needs to be displayed on canvas

### How to Send

**From Planner:**
```python
from socket_store import send_ws_message

# After plan is created in planner.py
plan_data = self.plan.model_dump()

await send_ws_message("plan", plan_data)
```

**Message Structure:**
```json
{
  "type": "plan",
  "payload": {
    "hypothesis_id": "9e583157-cf68-435d-9f58-4b6017cdc416",
    "study_goal": "Voice analysis for mental health assessment",
    "steps": [
      {
        "name": "Literature Review & Feasibility Assessment",
        "description": "Conduct a systematic review...",
        "matched_module": "ImagingProtocolDesigner",
        "status": "pending"
      },
      {
        "name": "Study Protocol Development",
        "description": "Draft a detailed protocol...",
        "matched_module": "PopulationSelector",
        "status": "pending"
      },
      // ... 8 more steps (10 total)
    ],
    "state": "not_confirmed"
  }
}
```

### Frontend Processing

PlaygroundCanvas automatically transforms this structure:

```typescript
// PlaygroundCanvas.tsx
const updatePlan = (planPayload: any) => {
  if (planPayload.steps) {
    // Creates nodes from steps
    const newNodes: FlowNode[] = [];
    
    // Start node
    newNodes.push({
      id: 'start',
      type: 'start',
      position: { x: 50, y: 50 },
      data: {
        title: 'Start',
        description: planPayload.study_goal
      }
    });
    
    // Step nodes (one per step)
    planPayload.steps.forEach((step, index) => {
      newNodes.push({
        id: String(index + 1),
        type: 'agent',
        position: { x: 50 + index * 250, y: 150 },
        data: {
          title: step.name,
          description: step.description,
          status: step.status,
          logs: [],
          isExpanded: false,
          matched_module: step.matched_module
        }
      });
    });
    
    setNodes(newNodes);
    
    // Creates sequential edges
    const newEdges = [];
    for (let i = 0; i < newNodes.length - 1; i++) {
      newEdges.push({
        id: `e${newNodes[i].id}-${newNodes[i + 1].id}`,
        source: newNodes[i].id,
        target: newNodes[i + 1].id,
        type: 'default'
      });
    }
    setEdges(newEdges);
  }
};
```

## 2. Receiving Plan Updates (Frontend → Backend)

### When Sent
- User edits node description
- User deletes node
- User clicks Execute
- User clicks Reset

### Frontend Sends

**Message Structure:**
```json
{
  "type": "PLAN_UPDATE",
  "nodes": [
    {
      "id": "start",
      "type": "start",
      "position": {"x": 50, "y": 50},
      "data": {
        "title": "Start",
        "description": "Voice analysis for mental health assessment"
      }
    },
    {
      "id": "1",
      "type": "agent",
      "position": {"x": 50, "y": 150},
      "data": {
        "title": "Literature Review & Feasibility Assessment",
        "description": "Conduct a systematic review...",
        "status": "pending",
        "logs": []
      }
    },
    // ... more nodes
  ],
  "edges": [
    {
      "id": "estart-1",
      "source": "start",
      "target": "1",
      "type": "default"
    },
    // ... more edges
  ],
  "mode": "planning|executing|results",
  "timestamp": "2024-01-15T10:30:45.123Z"
}
```

### Backend Handler

**In your WebSocket endpoint (socket_store.py or main.py):**

```python
from fastapi import WebSocket, WebSocketDisconnect
from mas.core.planner import Planner
import json

planner = Planner()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    try:
        while True:
            data = await websocket.receive_json()
            
            # Route by message type
            if data['type'] == 'PLAN_UPDATE':
                await handle_plan_update(websocket, data)
            
            elif data['type'] == 'START_WORKFLOW':
                await handle_workflow_start(websocket, data)
            
            elif data.get('type') == 'msg':
                # Frontend might send chat messages
                await handle_user_message(websocket, data)
    
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
```

**Handle Plan Updates:**

```python
async def handle_plan_update(websocket: WebSocket, message: dict):
    """Process plan updates from frontend"""
    
    nodes = message.get('nodes', [])
    edges = message.get('edges', [])
    mode = message.get('mode')
    timestamp = message.get('timestamp')
    
    logger.info(f"Plan update received: {len(nodes)} nodes, {len(edges)} edges, mode={mode}")
    
    # Transform frontend nodes back to StudyPlan format
    # Skip start node (id='start'), process agent nodes
    steps = []
    for node in nodes:
        if node['type'] == 'agent':
            step_data = {
                'name': node['data']['title'],
                'description': node['data']['description'],
                'matched_module': node['data'].get('matched_module'),
                'status': node['data'].get('status', 'pending')
            }
            steps.append(step_data)
    
    # Update planner's plan
    if steps:
        from mas.core.planner import StudyStep
        planner.plan.steps = [StudyStep(**step) for step in steps]
    
    # Save to state
    planner.save_to_config()
    
    # Send confirmation
    await websocket.send_json({
        'type': 'ack',
        'message': 'Plan received and stored',
        'timestamp': datetime.now().isoformat()
    })
```

## 3. Node Status Updates During Execution

### When to Send
- Node processing starts
- Node processing completes
- Node processing fails

### Backend Sends

```python
async def update_node_status(websocket: WebSocket, node_id: str, status: str, logs: list):
    """Update a node's status and logs"""
    
    await websocket.send_json({
        "type": "node",
        "nodeId": node_id,  # "1", "2", "3", etc.
        "payload": {
            "status": status,  # "pending", "active", "completed", "error"
            "logs": logs       # Array of log strings
        }
    })
```

### Example: Executing a Plan Step

```python
async def handle_workflow_start(websocket: WebSocket, message: dict):
    """Start workflow execution"""
    
    logger.info("Starting workflow execution...")
    
    # Get current plan from planner
    steps = planner.plan.steps
    
    for step_index, step in enumerate(steps, 1):
        # Node ID matches frontend: "1", "2", "3", etc.
        node_id = str(step_index)
        
        # Update: Node starting
        await update_node_status(
            websocket, 
            node_id, 
            "active", 
            [f"Starting: {step.name}..."]
        )
        
        # Simulate processing (replace with actual work)
        logs = [f"Starting: {step.name}..."]
        
        try:
            # Step 1: Processing
            logs.append("Processing...")
            await update_node_status(websocket, node_id, "active", logs)
            await asyncio.sleep(1)
            
            # Step 2: Module work
            logs.append(f"Using module: {step.matched_module}")
            await update_node_status(websocket, node_id, "active", logs)
            
            # Simulate actual module execution
            await run_module(step)  # Your actual execution logic
            
            logs.append(f"✓ Completed: {step.name}")
            await update_node_status(websocket, node_id, "completed", logs)
        
        except Exception as e:
            logs.append(f"✗ Error: {str(e)}")
            await update_node_status(websocket, node_id, "error", logs)
            break
    
    # All nodes done
    await websocket.send_json({
        "type": "info",
        "payload": "Workflow execution completed"
    })
```

## 4. Sending Plan Confirmation Requests

### When User Confirms Plan

Backend asks user to confirm the plan before execution:

```python
async def request_plan_confirmation(websocket: WebSocket):
    """Ask frontend to display confirmation dialog"""
    
    await websocket.send_json({
        "type": "msg",
        "payload": "Plan is ready. Click 'Execute' to begin the workflow."
    })
```

## 5. Current Planner Integration Points

### In `planner.py` - After creating plan:

**Current code (around line 337):**
```python
async def run(self, user_query: str):
    # ... PHASE 3: STUDY PLAN CREATION & CONFIRMATION
    if self.plan.hypothesis_id == "":
        await manager.send_ws_message("info", "Designing study plan from hypothesis...")
        message = await self.create_study_plan()
        await manager.send_ws_message("plan", format_plan(self.plan.model_dump()))
        await manager.send_ws_message("msg", message)
```

**Should be updated to:**
```python
async def run(self, user_query: str):
    # ... PHASE 3: STUDY PLAN CREATION & CONFIRMATION
    if self.plan.hypothesis_id == "":
        await manager.send_ws_message("info", "Designing study plan from hypothesis...")
        message = await self.create_study_plan()
        
        # Send FULL plan structure (not formatted)
        await manager.send_ws_message("plan", {
            "hypothesis_id": self.plan.hypothesis_id,
            "study_goal": self.plan.study_goal,
            "steps": [s.model_dump() for s in self.plan.steps],
            "state": self.plan.state
        })
        
        await manager.send_ws_message("msg", message)
```

### In `planner.py` - After matching modules:

**Current code (around line 355):**
```python
# Match steps to available modules
match_prompt = {...}
match_response = await self.steps_matcher.run(task=...)

# After matching:
# Send updated plan with matched_module values
await manager.send_ws_message("plan", {
    "hypothesis_id": self.plan.hypothesis_id,
    "study_goal": self.plan.study_goal,
    "steps": [s.model_dump() for s in self.plan.steps],
    "state": self.plan.state
})
```

## 6. Complete Integration Example

### Backend WebSocket Handler (socket_store.py)

```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from mas.core.planner import Planner
import asyncio
import json
from datetime import datetime

app = FastAPI()
planner = Planner()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    try:
        while True:
            data = await websocket.receive_json()
            message_type = data.get('type')
            
            if message_type == 'PLAN_UPDATE':
                await handle_plan_update(websocket, data)
            
            elif message_type == 'START_WORKFLOW':
                await handle_workflow_start(websocket)
            
            else:
                logger.warning(f"Unknown message type: {message_type}")
    
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")

async def handle_plan_update(websocket: WebSocket, message: dict):
    """Handle plan updates from PlaygroundCanvas"""
    
    nodes = message.get('nodes', [])
    edges = message.get('edges', [])
    mode = message.get('mode')
    timestamp = message.get('timestamp')
    
    logger.info(f"[{timestamp}] Plan updated: {len(nodes)} nodes, mode={mode}")
    
    # Transform frontend nodes → backend steps
    from mas.core.planner import StudyStep
    
    steps = []
    for node in nodes:
        if node['type'] == 'agent':
            steps.append(StudyStep(
                name=node['data']['title'],
                description=node['data']['description'],
                matched_module=node['data'].get('matched_module'),
                status=node['data'].get('status', 'pending')
            ))
    
    # Update planner
    if steps:
        planner.plan.steps = steps
        planner.save_to_config()
    
    # Send confirmation
    await websocket.send_json({
        'type': 'ack',
        'message': 'Plan received',
        'timestamp': datetime.now().isoformat()
    })

async def handle_workflow_start(websocket: WebSocket):
    """Execute workflow and send real-time updates"""
    
    logger.info("Starting workflow execution...")
    
    for step_index, step in enumerate(planner.plan.steps, 1):
        node_id = str(step_index)
        
        try:
            # Node active
            await websocket.send_json({
                "type": "node",
                "nodeId": node_id,
                "payload": {
                    "status": "active",
                    "logs": [f"Starting: {step.name}..."]
                }
            })
            
            # Simulate work
            await asyncio.sleep(1)
            
            # Node complete
            await websocket.send_json({
                "type": "node",
                "nodeId": node_id,
                "payload": {
                    "status": "completed",
                    "logs": [f"Starting: {step.name}...", "✓ Complete"]
                }
            })
        
        except Exception as e:
            await websocket.send_json({
                "type": "node",
                "nodeId": node_id,
                "payload": {
                    "status": "error",
                    "logs": [f"✗ Error: {str(e)}"]
                }
            })
```

## 7. Message Type Reference

| Type | Direction | Used For | Payload |
|------|-----------|----------|---------|
| `plan` | Backend → Frontend | Send/update plan structure | `{hypothesis_id, study_goal, steps, state}` |
| `node` | Backend → Frontend | Update node status/logs | `{nodeId, payload: {status, logs}}` |
| `PLAN_UPDATE` | Frontend → Backend | User modified plan | `{nodes, edges, mode, timestamp}` |
| `START_WORKFLOW` | Frontend → Backend | Start execution | Empty or `{nodes, edges}` |
| `msg` | Backend → Frontend | User-facing messages | `{payload: "message text"}` |
| `info` | Backend → Frontend | System/debug info | `{payload: "info text"}` |
| `hypothesis` | Backend → Frontend | Send hypothesis | Hypothesis object |
| `ack` | Backend → Frontend | Acknowledge receipt | `{message, timestamp}` |

## 8. Node ID Mapping

When backend sends node updates, use these IDs based on step order:

```
Frontend Node ID    Backend Reference
──────────────────────────────────────
"start"            → Start node (dummy)
"1"                → plan.steps[0]
"2"                → plan.steps[1]
"3"                → plan.steps[2]
...
"10"               → plan.steps[9]
```

## 9. Status Codes

Use these status values for nodes:

| Status | Frontend Visual | Meaning |
|--------|-----------------|---------|
| `pending` | Gray circle | Ready to process |
| `active` | Cyan spinning circle | Currently processing |
| `completed` | Green checkmark | Finished successfully |
| `error` | Red icon | Failed/error occurred |

## 10. Testing Integration

### Test 1: Send Plan to Frontend
```python
# In your backend handler
plan_data = {
    "hypothesis_id": "test-id",
    "study_goal": "Test study",
    "steps": [
        {"name": "Step 1", "description": "Test step", "status": "pending"}
    ],
    "state": "not_confirmed"
}
await websocket.send_json({"type": "plan", "payload": plan_data})
```

### Test 2: Receive Plan Update
```python
# Frontend sends this
{
    "type": "PLAN_UPDATE",
    "nodes": [...],
    "edges": [...],
    "mode": "planning",
    "timestamp": "..."
}
# Backend logs and confirms receipt
```

### Test 3: Execute Workflow
```python
# Frontend sends
{"type": "START_WORKFLOW"}

# Backend responds with node updates
{"type": "node", "nodeId": "1", "payload": {"status": "active", "logs": [...]}}
```

---

## Summary

The PlaygroundCanvas and Planner communicate through a WebSocket that:

1. **Backend sends plan** → Frontend displays as interactive nodes
2. **Frontend sends edits** → Backend stores and validates
3. **Backend sends execution updates** → Frontend displays real-time progress
4. **All state stays synchronized** via messages

This enables real-time collaborative workflow planning and execution monitoring.
