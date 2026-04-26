# Base image with Python 3.11.3
FROM python:3.11.3-slim

# Install system dependencies:
# - texlive-full for pdflatex support
# - build tools (gcc, g++, cmake) for compiling PyRadiomics C extensions
# - git for installing PyRadiomics from source
RUN apt-get update && apt-get install -y --no-install-recommends \
    texlive-latex-base \
    texlive-latex-recommended \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    gcc \
    g++ \
    cmake \
    make \
    git \
    python3-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for layer caching
COPY ./Backend/agent_system/requirements.txt .

# Step 1: Install dependencies from requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Step 2: Install PyRadiomics from source (builds C extensions)
RUN pip install --no-cache-dir git+https://github.com/AIM-Harvard/pyradiomics.git

# Step 3: Install pinned numeric libraries LAST (must not be overwritten)
RUN pip install --no-cache-dir numpy==1.26.4 pandas==2.1.4 pycaret==3.3.2

# Copy application source
COPY ./Backend/agent_system ./Backend/agent_system
COPY ./Backend/mcp ./Backend/mcp

# Default command
CMD ["python", "./Backend/agent_system/app.py"]