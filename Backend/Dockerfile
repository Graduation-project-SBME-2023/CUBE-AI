# monailabel.Dockerfile
FROM projectmonai/monailabel:0.8.5

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --upgrade pip && \
    pip install batchgenerators acvl-utils asyncpg && \
    pip install pydicom==1.2.2

# Clone and install nnUNet
RUN git clone https://github.com/MIC-DKFZ/nnUNet.git --branch master && \
    cd nnUNet && \
    pip install -e . && \
    cd ..

# Copy custom code and configurations

COPY ./radiology/ /apps/radiology
COPY ./monailabel/ /usr/local/lib/python3.10/dist-packages/monailabel/

# Setup environment variables
ENV nnUNet_preprocessed=/apps/radiology/nnunet/nnUNet_preprocessed \
    nnUNet_results=/apps/radiology/nnunet/nnUNet_results \
    nnUNet_raw=/apps/radiology/nnunet/nnUNet_raw \
    DATABASE_URL=postgresql://workflow_user:password@postgres:5432/workflow_db

# Expose ports
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl --fail http://localhost:8000/api/health/ || exit 1

# Start command with GPU support
CMD ["monailabel", "start_server", \
    "--app", "/apps/radiology", \
    "--studies", "http://orthanc:8042/dicom-web", \
    "--conf", "models", "all"]