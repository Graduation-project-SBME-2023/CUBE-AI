# Sam2 Model Deployment Documentation  

## **Table of Contents**  
1. [Installation](#installation)  
2. [Configuration](#configuration)  
3. [Usage](#usage)  

---

## **Installation**  

To deploy and use Sam2, ensure the following requirements are met:  

### **Prerequisites**  
- **Python**: `python>=3.10` (preferably Python 3.10 for compatibility with MONAILabel)  
- **PyTorch**: `torch>=2.3.1`  
- **TorchVision**: `torchvision>=0.18.1`  

Follow the [PyTorch installation instructions](https://pytorch.org/get-started/locally/) to install both PyTorch and TorchVision.  

### **Steps to Install Sam2**  

1. **Clone the Sam2 Repository**:  
   Run the following command to clone the repository:  
   ```bash
   git clone https://github.com/facebookresearch/sam2.git && cd sam2
   ```  

2. **Install Sam2**:  
   Install Sam2 on a GPU-enabled machine using:  
   ```bash
   pip install -e .
   ```  

3. **Install Additional Dependencies for Jupyter Notebooks** *(Optional)*:  
   If you plan to use the Sam2 predictor or run example notebooks, install `jupyter` and `matplotlib` with:  
   ```bash
   pip install -e ".[notebooks]"
   ```  

4. **Download Model Checkpoints**:  
   Download all necessary model checkpoints from [here](https://github.com/facebookresearch/sam2).  

---

## **Configuration**  

### **Radiology Folder Setup**  

1. Copy the following folders from the **.\radiology\lib**:  
   - `configs`  
   - `infers`  
   - `trainers`  

   Paste them into their corresponding locations in the **Radiology app** folder.  

2. Copy the `sam2` folder from the **.\radiology** and paste it into the **Radiology app**.  

---



## **Troubleshooting**  
If you encounter issues during installation or deployment:  
- Ensure all dependencies are correctly installed and compatible with your Python version  (you can check the attached sam2_dependencied.tsx file).  
- Verify that the model checkpoints are downloaded and accessible.  
