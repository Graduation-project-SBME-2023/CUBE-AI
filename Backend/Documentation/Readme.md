# MONAI Label Radiology App - Custom Setup Guide

This guide explains how to set up and run the MONAI Label server with a modified Radiology App.

## Prerequisites
- **Python Version**: Ensure you have Python **3.10** installed.
- **MONAI Label Version**: Install **MONAI Label v0.8.4**.
- **MONAI Version**: Install **MONAI v1.4.0**.

## 1. Enable Dynamic Model Creation Endpoints
Before running the server, follow the steps in the **Framework Inference Guide** to activate the endpoints for dynamically creating new models.
[Link to the guide](#) 

## 2. Download and Prepare Backend
- Download the `backend` folder containing the `radiology` application.
- Navigate to the `backend` directory in your terminal.

## 3. Start MONAI Label Server
Run the following command to start the MONAI Label server:

```sh
monailabel start_server --app radiology --studies http://localhost:8042/dicom-web --conf models all
```

- The server will start running on **port 8000**.

## 4. Verification
Ensure the server is running correctly by checking the logs or accessing `http://localhost:8000`.

---
For troubleshooting or additional configurations, refer to the MONAI Label documentation.

