# # monailabel.Dockerfile
# FROM projectmonai/monailabel:0.8.5

# # Install system dependencies
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     git build-essential \
#     && apt-get purge -y build-essential \
#     && apt-get autoremove -y \
#     && apt-get clean \
#     && rm -rf /var/lib/apt/lists/*

# # Install Python dependencies
# RUN pip install --no-cache-dir -U pip && \
#     pip install --no-cache-dir batchgenerators acvl-utils asyncpg nnunetv2 nnInteractive pydicom==2.4.4

# ## Clone and install nnUNet
# #RUN git clone https://github.com/MIC-DKFZ/nnUNet.git --branch master && \
# #    cd nnUNet && \
# #    pip install -e . && \
# #    cd ..

# # Copy custom code and configurations

# COPY ./apps/radiology/ /apps/radiology
# COPY ./monailabel/ /usr/local/lib/python3.10/dist-packages/monailabel/
# COPY ./reader.py /usr/local/lib/python3.10/dist-packages/pydicom_seg/reader.py
# COPY ./segmentation_dataset.py /usr/local/lib/python3.10/dist-packages/pydicom_seg/segmentation_dataset.py

# # Setup environment variables
# ENV nnUNet_preprocessed=/apps/radiology/nnunet/nnUNet_preprocessed \
#     nnUNet_results=/apps/radiology/nnunet/nnUNet_results \
#     nnUNet_raw=/apps/radiology/nnunet/nnUNet_raw \
#     DATABASE_URL=postgresql://workflow_user:password@postgres:5432/workflow_db

# # Expose ports
# EXPOSE 8000

# # Start command with GPU support
# CMD ["monailabel", "start_server", \
#     "--app", "/apps/radiology", \
#     "--studies", "http://orthanc:8042/dicom-web", \
#     "--conf", "models", "all"]

# monailabel.Dockerfile
FROM projectmonai/monailabel:0.8.5

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir batchgenerators acvl-utils asyncpg nnunetv2 nnInteractive pydicom==2.4.4

# Copy custom code and configurations

COPY ./apps/radiology/ /apps/radiology
COPY ./monailabel/ /monailabel/
COPY ./reader.py /usr/local/lib/python3.10/dist-packages/pydicom_seg/reader.py
COPY ./segmentation_dataset.py /usr/local/lib/python3.10/dist-packages/pydicom_seg/segmentation_dataset.py

# Setup environment variables
ENV nnUNet_preprocessed=/apps/radiology/nnunet/nnUNet_preprocessed \
    nnUNet_results=/apps/radiology/nnunet/nnUNet_results \
    nnUNet_raw=/apps/radiology/nnunet/nnUNet_raw \
    DATABASE_URL=postgresql://workflow_user:password@postgres:5432/workflow_db \
    PYTHONPATH=/apps/radiology

# Expose ports
EXPOSE 8000

# Start command with GPU support
CMD ["python", "/monailabel/server_start.py"]