# OHIF + MONAI Label + PostgreSQL + Orthanc Setup

This project uses **Docker Compose** to run four essential services for our CUBE.AI platform:

- **OHIF Viewer** – for viewing DICOM images in the browser
- **MONAI Label** – for AI-assisted medical image labeling
- **PostgreSQL** – to store workflow data
- **Orthanc** – DICOM server to manage and retrieve medical images

---

## Prerequisites

Before running this setup, make sure you have the following installed:

- [Docker](https://www.docker.com/products/docker-desktop)
- [Docker Compose](https://docs.docker.com/compose/install/)

---

## Running the Services

### 1. Clone the project
Here's the revised section with your requested formatting:

---

### 2. To run the Segment Anything Model (SAM) successfully in MONAI Label:

- **Download the SAM checkpoint file**:
  [SAM Checkpoint (Google Drive)](https://drive.google.com/drive/folders/15KWC6C-Wnk6EwWLBHdCi2RiMFnphFhw4)

- **Place the checkpoint file in**:
  ```bash
  platform\app\.recipes\OpenResty-Orthanc\apps\radiology\sam2\checkpoints
  ```

### 3. Navigate to the project directory

```bash
cd path/to/your/project
```

Make sure this directory contains the `docker-compose.yml` file (in platform\app\\.recipes\OpenResty-Orthanc)

---

### 4. Start all services

Run the following command to build and start the containers:

```bash
docker compose up --build
```

This will:

- Build the `ohif_viewer` container
- Pull required images for `monailabel`, `postgres`, and `orthanc`
- Start all services and mount volumes/configs as defined

---

### 5. Access the services

| Service     | URL                       |
| ----------- | ------------------------- |
| OHIF Viewer | http://localhost          |
| MONAI Label | http://localhost:8000     |
| Orthanc Web | http://localhost:8043     |
| PostgreSQL  | localhost:5433 (via psql) |

---

### 6. Stopping the services

To stop the services gracefully:

```bash
docker compose down
```

This will stop and remove the containers.

---

## Additional Notes

- The `monailabel` container includes startup commands to:
  - Install required packages
  - Clone and install `nnUNet`
  - Start the MONAI Label server
- PostgreSQL initializes with `workflow_db` and credentials defined in the environment variables.

---

## Tips

- To view logs from a specific service (e.g., `monailabel`):

  ```bash
  docker compose logs -f monailabel
  ```

- To restart a single service:
  ```bash
  docker compose restart ohif_viewer
  ```

---

## Volumes

| Volume Name | Purpose                             |
| ----------- | ----------------------------------- |
| postgres-db | Stores PostgreSQL data persistently |

---
