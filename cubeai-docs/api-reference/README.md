# 📡 API Reference

This document covers all REST and WebSocket APIs exposed by CUBE.AI backend services.

---

## Services & Base URLs

| Service | Base URL | Auth |
|---|---|---|
| Application API | `http://localhost:8001` (dev), `/api` (prod) | Existing deployment policy |
| Agent System | `http://localhost:8150` (dev), `/agents` (prod) | Existing deployment policy |
| MONAI Inference | `http://localhost:8000` (dev), `/inference` (prod) | Existing deployment policy |
| Orthanc | `http://localhost:8043` (dev), `/pacs` (prod) | Disabled by default (`AuthenticationEnabled: false` in `Backend/orthanc/orthanc.json`) — see `cubeai-docs/backend/database_audit.md` finding D-30/P-3 before exposing this beyond the docker-compose network |

---

## Agent System API

### Health Check

```http
GET /health
```

**Response:**
```json
{ "status": "ok" }
```

---

### Research Sessions

#### Create Session
```http
POST /sessions
Content-Type: application/json

{
  "study_ids": ["1.2.3.4.5", "1.2.3.4.6"],
  "research_goal": "Predict tumor malignancy from CT features"
}
```

**Response:**
```json
{
  "session_id": "abc123",
  "workspace_dir": "/app/agents_workspace/abc123",
  "status": "created"
}
```

#### Get Session
```http
GET /sessions/{session_id}
```

---

### Report Compilation

#### Compile LaTeX to PDF
```http
POST /compile_report
Content-Type: application/json

{
  "session_id": "abc123",
  "title": "Radiomics Study Report"
}
```

**Response:**
```json
{
  "status": "success",
  "pdf_path": "/app/agents_workspace/abc123/reports/report.pdf",
  "download_url": "/reports/abc123"
}
```

#### Download Report
```http
GET /reports/{session_id}
```

Returns the compiled PDF file.

---

## Agent System WebSocket API

Connect to the native FastAPI WebSocket at `ws://localhost:8150/ws` in
development or `/ws` on the production origin. The `session_id` query
parameter is mandatory and binds the socket for its lifetime.

### Connection
```javascript
const sessionId = '3d1df5d1-8ff5-46f5-8ac6-f6c5dc44f35a';
const socket = new WebSocket(
  `ws://localhost:8150/ws?session_id=${encodeURIComponent(sessionId)}`
);
```

### Events — Client → Server

#### Send Chat Message
```javascript
socket.send(JSON.stringify({
  type: 'user',
  message_id: crypto.randomUUID(),
  session_id: sessionId,
  payload: { text: 'Analyze CT features in brain tumor patients' }
}));
```

Start, restart, pause, resume, and stop the workflow through the corresponding
REST endpoints. Unsupported WebSocket control events are ignored.

---

### Events — Server → Client

```javascript
socket.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  if (message.session_id !== sessionId) return;
  console.log(message.type, message.payload);
});
```

Every server message contains `version`, `event_id`, `sequence`, `session_id`,
`run_id`, `type`, `payload`, `timestamp`, and `replaying`. Clients should ignore
events for another session and deduplicate by `event_id`.

The current no-Redis deployment supports one Agent System worker. WebSocket
registries and execution queues are process-local, so multiple workers require a
shared event broker and distributed capacity controller.

#### Heavy-Resource Status

Heavy stages emit `resource_status` messages with `state` equal to
`WAITING_FOR_RESOURCE`, `ACQUIRED`, or `RELEASED`:

```json
{
  "type": "resource_status",
  "payload": {
    "session_id": "abc123",
    "agent": "segmentation_agent",
    "operation": "segmentation_agent.infer:pair-1",
    "resources": ["gpu_segmentation", "cpu_heavy"],
    "state": "WAITING_FOR_RESOURCE",
    "queue_position": 1
  }
}
```

`GET /status/{session_id}` includes that session's active and waiting leases in
`resource_capacity`. `GET /ws-status` exposes the global resource-capacity
snapshot for operations and diagnostics.

---

## MONAI Label API

Base URL: `http://localhost:8000/api/v1`

### Server Info
```http
GET /info
```

### Run Inference (Segmentation)
```http
POST /infer/{model}?image={series_uid}
```

### List Models
```http
GET /models
```

### Custom Endpoints (CUBE.AI Extensions)

#### Create Dynamic Model
```http
POST /monailabel/create-model?model_name=tumor&labels=["tumor","background"]&fold=0&plan_type=3d_fullres
```

#### Initialize Inference Task
```http
POST /monailabel/initialize-inference-task?model_name=tumor
```

---

## Workflow Data API (Application API)

Base URL: `http://localhost:8001` in development or `/api` through Nginx. Owned entirely by the
Application API (`Backend/application_api/api/routes/workflow.py`) — MONAI Label never receives
`DATABASE_URL` and does not expose these routes.

### Workflow Management

#### Register a New Workflow
```http
POST /workflow/add
Content-Type: application/json

{ "task_name": "BrainTumor" }
```

#### List All Workflows
```http
GET /workflow/?skip=0&limit=100
```

#### Delete Workflow
```http
DELETE /workflow/delete
Content-Type: application/json

{ "task_name": "BrainTumor" }
```

Cascades to every `workflow_details` row for that `task_name`.

---

### Study Assignment

#### Add a Study/Series to a Workflow
```http
POST /workflow/{task_name}/add
Content-Type: application/json

{
  "study_uid": "1.2.3.4",
  "task_name": "BrainTumor",
  "series_instance_uid": "1.2.3.4.5",
  "instance_uid": null,
  "train": false
}
```
The `task_name` in the body must match the path parameter, or the request is rejected with 400.

#### Update Series/Instance UIDs
```http
POST /workflow/update-uids
Content-Type: application/json

{
  "study_uid": "1.2.3.4",
  "task_name": "BrainTumor",
  "series_instance_uid": "1.2.3.4.5",
  "instance_uid": "1.2.3.4.5.6",
  "modality": "CT"
}
```

#### Get Study UIDs in a Workflow
```http
POST /workflow/{task_name}/filter-uid
```

**Response:**
```json
["1.2.3.4", "1.2.3.4.9", "1.2.3.4.12"]
```

#### Get Full Workflow Data
```http
POST /workflow/{task_name}/filter
```

**Response:**
```json
{
  "study_uid": ["1.2.3.4"],
  "series_instance_uid": ["1.2.3.4.5"],
  "instance_uid": ["1.2.3.4.5.6"],
  "modality": ["CT"]
}
```

#### Get Annotation Status
```http
POST /workflow/{task_name}/filter-annotated
```

**Response:**
```json
{
  "1.2.3.4": true,
  "1.2.3.4.9": false
}
```

#### Update / Get Train Flag
```http
POST /workflow/{task_name}/update-train?study_uid=1.2.3.4&train=true
GET  /workflow/{task_name}/get-train?study_uid=1.2.3.4
```

#### Start nnUNet Training
```http
POST /workflow/{task_name}/train
```
Returns `202 Accepted` with a `job_id`; poll `GET /workflow/training-jobs/{job_id}` for status.
Proxies to MONAI Label's inference service (`INFERENCE_API_URL`) — the Application API never runs
the training itself.

---

## Orthanc Sync API (Application API)

Base URL: `http://localhost:8001` in development or `/api` through Nginx. This is the boundary
between Orthanc (the PACS) and the `patients`/`studies`/`volume_series`/`segmentations` tables — see
`cubeai-docs/database/README.md` and `cubeai-docs/backend/database_audit.md` for the full
consistency model and its known gaps.

#### Trigger a Full Reconciliation Sync
```http
POST /sync/sync-all
```
Runs in the background; returns immediately. Also runs automatically on a timer from the
Application API's startup lifespan (`RECONCILIATION_INTERVAL_SECONDS`, default 300s) — this endpoint
is for forcing an out-of-cycle pass, not the only way sync happens.

#### Check Sync Status
```http
GET /sync/sync-status
```

**Response:**
```json
{
  "database": { "patients": 12, "studies": 20, "volumes": 45, "segmentations": 8 },
  "orthanc": { "patients": 12, "studies": 20, "series": 53, "instances": 9120 },
  "synced": true
}
```
`synced` compares both study counts and total series counts (volumes + segmentations combined)
against Orthanc's own statistics.

#### Orthanc Webhook Receiver
```http
POST /sync/orthanc-webhook
X-Webhook-Token: <ORTHANC_WEBHOOK_TOKEN, if configured>
Content-Type: application/json

{ "change_type": "stable_series", "resource_id": "<orthanc-internal-id>", "level": "series" }
```
Called by `Backend/orthanc/lua/on-stable-series.lua` on `OnStableSeries` / `OnStableStudy` /
`OnDeletedPatient` / `OnDeletedStudy` / `OnDeletedSeries`. If `ORTHANC_WEBHOOK_TOKEN` is set in the
Application API's environment, the same value must be set in the Lua script's `WEBHOOK_TOKEN`
constant, or every call is rejected with 401.

---

## Orthanc REST API

Base URL: `http://localhost:8042` (or `http://localhost:8043` from the host, per the compose port
mapping).
Auth: disabled by default (`AuthenticationEnabled: false`) — see
`cubeai-docs/backend/database_audit.md` findings D-30 and P-1/P-3 before relying on this in any
environment reachable from outside the docker-compose network. If authentication is enabled, note
that the agent system's and MONAI Label's Orthanc clients currently send no credentials at all and
must be updated in the same change.

### Upload DICOM
```http
POST /instances
Content-Type: application/dicom
Body: <raw DICOM bytes>
```

### List Studies
```http
GET /studies
```

### Get Study Details
```http
GET /studies/{study_id}
```

### DICOMweb — WADO-RS
```
GET /dicom-web/studies
GET /dicom-web/studies/{study_uid}/series
GET /dicom-web/studies/{study_uid}/series/{series_uid}/instances
```

---

## See Also

- [Agent System](../agent-system/README.md) — Agent pipeline details
- [Research Playground](../research-playground/README.md) — Frontend WebSocket usage
- [Backend](../backend/README.md) — Service configuration
