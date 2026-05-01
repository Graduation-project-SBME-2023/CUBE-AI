FROM projectmonai/monailabel:0.8.5

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir batchgenerators acvl-utils asyncpg nnunetv2 nnInteractive pydicom==2.4.4 

COPY ./reader.py /usr/local/lib/python3.10/dist-packages/pydicom_seg/reader.py
COPY ./segmentation_dataset.py /usr/local/lib/python3.10/dist-packages/pydicom_seg/segmentation_dataset.py

# Setup environment variables
ENV nnUNet_preprocessed=/NN_Unet/nnUNet_preprocessed \
    nnUNet_results=/NN_Unet/nnUNet_results \
    nnUNet_raw=/NN_Unet/nnUNet_raw \
    DATABASE_URL=postgresql://workflow_user:password@postgres:5432/workflow_db \
    PYTHONPATH=/apps/radiology

# Expose ports
EXPOSE 8000
