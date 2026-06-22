# 🔬 Research Playground

The Research Playground is CUBE.AI's flagship feature — an interactive, canvas-based interface where clinicians and researchers describe a medical imaging research question and watch an AI multi-agent system conduct the study autonomously.

---

## Overview

The Research Playground consists of two core UI areas:

```
┌──────────────────────────────────────────────────────────┐
│                    Research Playground                    │
│                                                           │
│  ┌────────────────────────────────┐  ┌─────────────────┐ │
│  │                                │  │                 │ │
│  │       Agent Canvas             │  │  Chat Sidebar   │ │
│  │    (ReactFlow Node Graph)      │  │                 │ │
│  │                                │  │  ┌───────────┐  │ │
│  │  [Start]→[Seg]→[Feature]→[ML] │  │  │ User Msg  │  │ │
│  │              ↓                 │  │  ├───────────┤  │ │
│  │          [Report]              │  │  │ AI Reply  │  │ │
│  │                                │  │  └───────────┘  │ │
│  └────────────────────────────────┘  └─────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

---

## Key Components

### PlaygroundCanvas

**File:** `extensions/research-playground/src/components/PlaygroundCanvas/`

The main canvas is powered by **ReactFlow** and displays the research pipeline as an interactive directed graph.

#### Canvas Modes

| Mode | Description |
|---|---|
| `planning` | Pipeline plan is visible; nodes can be edited or deleted |
| `executing` | Agents are running; node status updates in real-time |
| `results` | Pipeline complete; PDF report available for download |

#### Node Types

| Type | Appearance | Meaning |
|---|---|---|
| `start` | Green circle | User's research question (entry point) |
| `agent` | Blue card | A research agent step |

#### Node States

| Status | Color | Meaning |
|---|---|---|
| `pending` | Gray | Not yet started |
| `running` | Blue + spinner | Currently executing |
| `done` | Green | Completed successfully |
| `error` | Red | Failed with error |

---

### ChatSidebar

**File:** `extensions/research-playground/src/components/PlaygroundCanvas/ChatSidebar.tsx`

The conversational interface that:
- Accepts natural language research questions
- Shows agent reasoning and status updates
- Displays final summaries

**Features:**
- Markdown rendering for rich text responses
- Timestamp on each message
- Auto-scroll to latest message
- Loading indicator during agent processing

---

## Using the Research Playground

### Step 1 — Open a Study

Navigate to the OHIF worklist and open a study (or select multiple studies for a cohort study). Then click **Research Playground** to enter the playground with the study pre-loaded.

### Step 2 — Ask a Research Question

In the Chat Sidebar, type your research question:

> *"Can we predict patient sex from spleen radiomic features in CT scans?"*

> *"Analyze whether tumor volume correlates with recurrence risk in this brain MRI cohort."*

### Step 3 — Review the Pipeline Plan

The **Hypothesis Architect** formulates a hypothesis, then the **Pipeline Architect** creates a step-by-step plan, visualized as nodes on the canvas.

You can:
- **Edit a node** — Click the edit icon on a node to modify its description
- **Delete a node** — Remove steps you don't need
- **Expand a node** — View the full description and expected outputs

### Step 4 — Execute

Click **Execute** (or **Start Workflow**) to begin the pipeline. Watch as:
- Nodes transition from gray → blue (running) → green (done)
- The Chat Sidebar shows real-time agent logs
- Each node's logs can be inspected by clicking on it

### Step 5 — View Results & Download Report

When all nodes are complete (mode = `results`):
- A summary appears in the Chat Sidebar
- Click **Download Report** to get the generated PDF

---

## WebSocket Message Flow

```
Client                          Server (Agent System)
  │                                      │
  │── socket.connect() ─────────────────►│
  │                                      │
  │── emit('message', {content: "..."}) ►│
  │                                      │── Hypothesis Architect runs
  │◄─ on('plan', {steps: [...]}) ────────│  Plan ready
  │                                      │
  │   [User reviews plan on canvas]      │
  │                                      │
  │── emit('START_WORKFLOW', {id}) ─────►│
  │                                      │── Execution agents run
  │◄─ on('NODE_UPDATE', {id, status}) ──│  Per-agent updates
  │◄─ on('NODE_UPDATE', {id, status}) ──│
  │◄─ on('NODE_UPDATE', {id, status}) ──│
  │                                      │
  │◄─ on('message', {content: "Done"}) ─│  Final summary
  │                                      │
```

---

## Plan Data Format

The backend sends plan data in this format:

```json
{
  "hypothesis_id": "session_abc123",
  "study_goal": "Predict patient sex from spleen CT radiomics",
  "steps": [
    {
      "name": "Study Population Planner",
      "description": "Identify all CT studies with spleen annotations",
      "status": "pending",
      "matched_module": "study_population_planner_agent"
    },
    {
      "name": "Segmentation Agent",
      "description": "Run nnUNet spleen segmentation",
      "status": "pending",
      "matched_module": "segmentation_agent"
    }
  ]
}
```

The `transformPlanToFlow()` utility converts this into ReactFlow nodes and edges:

```typescript
import { transformPlanToFlow } from '../utils/PlanTransformationUtils';

const { nodes, edges } = transformPlanToFlow(planData);
// nodes: FlowNode[] — positioned nodes for ReactFlow
// edges: FlowEdge[] — connections between nodes
```

---

## Node Positioning Algorithm

Nodes are positioned with alternating Y-offsets to create visual hierarchy:
- **Start node** — positioned at `(x: 0, y: 250)`
- **Agent nodes** — positioned at increasing X with alternating Y (`200` or `300`)

This creates a visual "wave" pattern that makes the pipeline easy to follow left-to-right.

---

## Extending the Playground

### Adding a New Agent Type

1. Create the agent in `Backend/agent_system/agents/`
2. Register it in the team configuration in `Backend/agent_system/teams/`
3. Add its I/O contract to `Backend/agent_system/agents/agent_io_docs/`
4. The Pipeline Architect will automatically include it in plans when appropriate

### Adding a New Node Visual Type

1. Create a new node component in `PlaygroundCanvas.tsx`
2. Register it in the `nodeTypes` object:
   ```typescript
   const nodeTypes = {
     start: StartNode,
     agent: AgentNode,
     myNewType: MyNewNode,  // ← add here
   };
   ```

---

## See Also

- [Agent System](../agent-system/README.md) — Backend pipeline details
- [Frontend](../frontend/README.md) — All frontend components
- [API Reference](../api-reference/README.md) — WebSocket protocol reference
