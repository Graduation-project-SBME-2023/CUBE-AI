# Planning Agents Documentation

This document provides an overview of each agent in the planning pipeline and describes their roles in the research workflow. Planning agents are responsible for designing, critiquing, and organizing the research process, ensuring methodological rigor and efficient execution.

## Phases in the Planning Pipeline

1. **Population Planning Phase**
   - Agent: `population_planner.py`
   - Task: Defines and selects the target population for the study, ensuring appropriate inclusion/exclusion criteria.

2. **Segmentation Planning Phase**
   - Agent: `segmentation_planner.py`
   - Task: Plans the segmentation strategy for medical images, including algorithm selection and validation criteria.

3. **Radiomics Planning Phase**
   - Agent: `radiomics_planner.py`
   - Task: Designs the extraction and analysis of radiomic features from images, specifying feature sets and statistical methods.

4. **Statistics Planning Phase**
   - Agent: `statistics_planner.py`
   - Task: Outlines statistical analysis methods, including hypothesis testing, model evaluation, and reporting standards.

5. **Validation Planning Phase**
   - Agent: `validation_planner.py`
   - Task: Plans validation procedures for models and results, ensuring reproducibility and robustness.

6. **Critique Phase**
   - Agent: `critic_agent.py`
   - Task: Reviews and critiques the planning process, identifying potential flaws and suggesting improvements.

## Agent Descriptions

### 1. `population_planner.py`
Defines the study population, manages inclusion/exclusion criteria, and ensures sample representativeness.

### 2. `segmentation_planner.py`
Plans segmentation approaches, selects algorithms, and sets validation metrics for image processing.

### 3. `radiomics_planner.py`
Designs radiomic feature extraction, selects relevant features, and plans statistical analysis.

### 4. `statistics_planner.py`
Outlines statistical methods, manages hypothesis testing, and ensures proper reporting of results.

### 5. `validation_planner.py`
Plans validation strategies, including cross-validation, external validation, and reproducibility checks.

### 6. `critic_agent.py`
Acts as a reviewer, providing critical feedback on planning decisions and suggesting methodological improvements.

### 7. `planner.py` / `planning_agents.py`
Coordinates the overall planning process, integrating the outputs of specialized planners and managing workflow.

### 8. `models.py`
Defines data models and structures used by planning agents for organizing and storing planning information.

---

This documentation complements the execution agent documentation, providing a clear overview of the planning phase and its modular agent-based architecture for research projects.
