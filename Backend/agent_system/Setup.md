# Setup & Run Guide

## Prerequisites

This project requires **Python 3.11.3**. A `.python-version` file is included in the repo, so if you have [pyenv](https://github.com/pyenv/pyenv) installed it will automatically activate the correct version when you enter the project directory.

If you don't have pyenv, and wish to use it, install it and then run:

```bash
pyenv install 3.11.3
```

You can verify the active Python version at any time with:

```bash
python --version
```

---

## Installation

**1. Install dependencies from requirements.txt**

```bash
pip install -r requirements.txt
```

**2. Install PyRadiomics from source**

```bash
pip install git+https://github.com/AIM-Harvard/pyradiomics.git
```

**3. Install pinned numeric libraries**

```bash
pip install numpy==1.26.4 pandas==2.1.4 pycaret==3.3.2
```

> These versions must be installed last to avoid being overwritten by other packages.

---

## LLM Client installation

We use the ollama client by default, download the client:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

then run it once to ensure it is listening on the default port. Select the model that is used by our agents app, and text it at least once so that it is registered.
We use the `deepseek-v3.1:671b-cloud` model by default.

---

## Running the App

From the **root directory** of the project, run:

```bash
python ./Backend/agent_system/app.py
```