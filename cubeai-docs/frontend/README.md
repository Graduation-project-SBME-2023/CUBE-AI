# 🎨 Frontend Documentation

The frontend is a customized **OHIF Viewer** built with React 18 and TypeScript. It extends the standard OHIF framework with custom extensions for the CUBE.AI platform.

---

## Technology Stack

| Technology | Purpose |
|---|---|
| React 18 | Core UI framework |
| TypeScript | Type safety |
| Vite | Build tool & dev server |
| Yarn | Package manager |
| ReactFlow | Node-graph canvas for Research Playground |
| Socket.IO Client | WebSocket communication with Agent System |
| OHIF Extensions | Custom panels and viewers |

---

## Project Structure

```
Frontend/OHIF-Viewer/
│
├── platform/
│   ├── app/                      ← Main application shell
│   │   └── src/
│   │       ├── components/       ← Shared UI components (PlaygroundCanvas, etc.)
│   │       ├── routes/           ← Page routes (Playground, Workflow, WorkList)
│   │       └── utils/            ← Utilities (WebsocketService, PlanTransformationUtils)
│   └── ui/                       ← OHIF UI component library
│
├── extensions/
│   ├── research-playground/      ← 🔑 Core custom extension
│   │   └── src/components/
│   │       └── PlaygroundCanvas/ ← Canvas + ChatSidebar
│   └── monai-label/              ← MONAI Label integration panel
│
└── modes/
    └── monai-label/              ← Assisted Segmentation mode
```

---

## Custom Extensions

### Research Playground Extension

**Path:** `extensions/research-playground/`

The most critical custom addition. Provides:

- **`PlaygroundCanvas`** — A ReactFlow-powered node graph that visualizes the agent pipeline. Each node represents a research agent (segmentation, feature extraction, modeling, etc.)
- **`ChatSidebar`** — A conversational UI where users describe their research question and receive real-time updates

The canvas supports three modes:
1. **Planning** — View and edit the proposed pipeline before execution
2. **Executing** — Watch agents run in real-time with live log streaming
3. **Results** — Review outputs and download the generated PDF report

### MONAI Label Extension

**Path:** `extensions/monai-label/`

Integrates MONAI Label's AI segmentation directly into the OHIF viewer:
- Connection status indicator
- Model selection
- Run segmentation button
- **Upload Labels** — Upload annotated segments back to the MONAI Label server
- Workflow name extraction from URL parameters

Display name has been updated from "MonAI Label" to **"Assisted Segmentation"**.

---

## Key Components

### PlaygroundCanvas

**File:** `platform/app/src/components/PlaygroundCanvas.tsx`

The central UI component for the Research Playground. Manages:
- ReactFlow node/edge state
- Three-mode workflow (planning / executing / results)
- WebSocket message handling (PLAN_UPDATE, NODE_UPDATE, etc.)
- Node edit, delete, and expand operations
- Execution log streaming per node

### ChatSidebar

**File:** `extensions/research-playground/src/components/PlaygroundCanvas/ChatSidebar.tsx`

Conversational interface that:
- Sends user research queries to the Agent System via WebSocket
- Displays chat history with markdown rendering
- Shows agent progress notifications

### WebsocketService

**File:** `platform/app/src/utils/WebsocketService.js`

Singleton service that manages the Socket.IO connection:
```javascript
websocketService.connect();
websocketService.isConnected();
websocketService.emit('START_WORKFLOW', payload);
websocketService.on('plan', handlePlan);
```

### PlanTransformationUtils

**File:** `platform/app/src/utils/PlanTransformationUtils.ts`

Shared utility for converting backend plan JSON into ReactFlow nodes/edges:
```typescript
import { transformPlanToFlow } from '../utils/PlanTransformationUtils';

const { nodes, edges } = transformPlanToFlow(planData);
```

---

## Workflow Features

### Worklist (Study List)

The home page (`WorkList.tsx`) has been extended with:
- **Workflow creation** — Create named annotation workflows
- **Study assignment** — Assign studies to workflows via combobox
- **Workflow list panel** — Right sidebar showing all created workflows

### Segmentation Queue

A queue system for batch segmentation tasks:
- Add multiple studies to queue from the viewer
- Execute all queued segmentations sequentially from the home page
- Manage queue (view, delete items)

See [Patch Processing Documentation](./patch_processing.md) for implementation details.

---

## Running the Frontend

### Development
```bash
cd Frontend/OHIF-Viewer
yarn install
yarn run dev
# http://localhost:3000
```

### Production Build
```bash
yarn build
# Output in: platform/app/dist/
```

### Docker (Production)
```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.frontend.prod.yml up --build
```

---

## Viewer Header Navigation

The viewer header (`ViewerHeader.tsx`) has been modified to support workflow-aware navigation:

```typescript
// If arriving from a workflow page, return button goes back to workflow
const workflowName = query.get('workflowName');
if (workflowName) {
  navigate({ pathname: `/workflow/${workflowName}` });
  return;
}
// Otherwise, return to the home page
navigate({ pathname: '/' });
```

---

## See Also

- [Research Playground](../research-playground/README.md) — Full Research Playground documentation
- [Agent System](../agent-system/README.md) — Backend that powers the canvas
- [API Reference](../api-reference/README.md) — WebSocket protocol & REST endpoints
