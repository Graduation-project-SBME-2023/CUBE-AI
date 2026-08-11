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

Connect to the FastAPI WebSocket at `ws://localhost:8150/ws` in development or `/ws` on the production origin.

### Connection
```javascript
import { io } from 'socket.io-client';
const socket = new WebSocket('ws://localhost:8150/ws');
```

### Events — Client → Server

#### Send Chat Message
```javascript
socket.emit('message', {
  content: "Analyze CT features in brain tumor patients"
});
```

#### Start Workflow Execution
```javascript
socket.emit('START_WORKFLOW', {
  session_id: "abc123"
});
```

#### Reset Workflow
```javascript
socket.emit('RESET_WORKFLOW', {});
```

---

### Events — Server → Client

#### Plan Ready
```javascript
socket.on('plan', (data) => {
  // data.steps = [{name, description, status, matched_module}, ...]
});
```

#### Plan Updated
```javascript
socket.on('PLAN_UPDATE', (data) => {
  // Same structure as 'plan'
});
```

#### Node Status Update
```javascript
socket.on('node', (data) => {
  // data = { id: "step_1", status: "running"|"done"|"error", logs: "..." }
});
```

```javascript
socket.on('NODE_UPDATE', (data) => {
  // Same structure as 'node'
});
```

#### Chat Message
```javascript
socket.on('message', (data) => {
  // data = { role: "assistant", content: "..." }
});
```

#### Error
```javascript
socket.on('error', (data) => {
  // data = { message: "..." }
});
```

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
