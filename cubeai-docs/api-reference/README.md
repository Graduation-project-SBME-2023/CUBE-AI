# 📡 API Reference

This document covers all REST and WebSocket APIs exposed by CUBE.AI backend services.

---

## Services & Base URLs

| Service | Base URL | Auth |
|---|---|---|
| Application API | `http://localhost:8001` (dev), `/api` (prod) | Existing deployment policy |
| Agent System | `http://localhost:8150` (dev), `/agents` (prod) | Existing deployment policy |
| MONAI Inference | `http://localhost:8000` (dev), `/inference` (prod) | Existing deployment policy |
| Orthanc | `http://localhost:8043` (dev), `/pacs` (prod) | Basic Auth (`orthanc:orthanc`) |

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

Base URL: `http://localhost:8001` in development or `/api` through Nginx

### Workflow Management

#### Create Workflow
```http
POST /workflow/list_workflow/add
Content-Type: application/json

{ "task_name": "BrainTumor" }
```

#### List All Workflows
```http
GET /workflow/list_workflow
```

#### Delete Workflow
```http
DELETE /workflow/list_workflow/delete
Content-Type: application/json

{ "task_name": "BrainTumor" }
```

---

### Study Assignment

#### Add Study to Workflow
```http
POST /workflow/add
Content-Type: application/json

{
  "case_id": "PATIENT_001",
  "task_name": "BrainTumor",
  "series_instance_uid": "1.2.3.4.5",
  "instance_uid": null,
  "train": false
}
```

#### Update Study UIDs
```http
POST /workflow/update-uids
Content-Type: application/json

{
  "case_id": "PATIENT_001",
  "task_name": "BrainTumor",
  "series_instance_uid": "1.2.3.4.5",
  "instance_uid": "1.2.3.4.5.6",
  "modality": "CT"
}
```

#### Get Studies in Workflow
```http
POST /workflow/filter
Content-Type: application/json

{ "task_name": "BrainTumor" }
```

**Response:**
```json
["PATIENT_001", "PATIENT_002", "PATIENT_003"]
```

#### Get Annotation Status
```http
POST /workflow/filter-annotated
Content-Type: application/json

{ "task_name": "BrainTumor" }
```

**Response:**
```json
{
  "PATIENT_001": true,
  "PATIENT_002": false
}
```

---

### Data Transfer

#### Download Images from PACS
```http
POST /datastore/images?task_name=BrainTumor
Content-Type: application/json

["series_uid_1", "series_uid_2"]
```

#### Download Labels from PACS
```http
POST /datastore/labels?task_name=BrainTumor
Content-Type: application/json

["instance_uid_1", "instance_uid_2"]
```

---

## Orthanc REST API

Base URL: `http://localhost:8042`  
Auth: Basic `orthanc:orthanc`

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
