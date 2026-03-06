# Setup & Run Guide

## Prerequisites

This project requires **Python 3.11.3**. A `.python-version` file is included in the repo, so if you have [pyenv](https://github.com/pyenv/pyenv) installed it will automatically activate the correct version when you enter the project directory.

If you don't have pyenv, install it and then run:

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
pip install numpy==1.26.4 pandas==2.1.4
```

> These versions must be installed last to avoid being overwritten by other packages.

---

## Running the App

From the **root directory** of the project, run:

```bash
python ./Backend/agent_system/app.py
```