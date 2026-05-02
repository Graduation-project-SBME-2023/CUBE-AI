import Workflow from './Workflow';

class WorkflowManager {
  constructor() {
    this.workflows = [];
  }

  async refreshDatabase() {
    try {
      // Fetch all task names from the list_workflow API
      const response = await fetch('http://localhost:8000/workflow/');

      if (!response.ok) {
        throw new Error(`Error: ${response.status}`);
      }

      const taskRecords = await response.json();
      console.log('Fetched task namess:', taskRecords);

      // Fetch study_uids for each task_name
      const workflows = await Promise.all(
        taskRecords.map(async record => {
          const url = `http://localhost:8000/workflow/${encodeURIComponent(
            record.task_name
          )}/filter-uid`;
          const filterResponse = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
          });

          if (!filterResponse.ok) {
            console.error(`Error fetching studies for ${record.task_name}`);
            return new Workflow(record.task_name, []); // Default empty studies on failure
          }

          const caseIds = await filterResponse.json();
          return new Workflow(record.task_name, caseIds, false);
        })
      );

      this.workflows = workflows;

      return this.workflows; // Return the updated workflows
    } catch (error) {
      console.error('Error fetching workflows from database:', error);
      return [];
    }
  }

  async createWorkflow(workflowName, workflowStudies = [], workflowTrainFlag = false) {
    const apiUrl = 'http://localhost:8000/workflow/add';

    try {
      const response = await fetch(apiUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ task_name: workflowName }),
      });

      if (!response.ok) {
        throw new Error(`Error: ${response.status}`);
      }

      const result = await response.json();
      console.log('API Response:', result);

      // If API call is successful, create the workflow locally
      const newWF = new Workflow(workflowName, workflowStudies, workflowTrainFlag);
      this.workflows.push(newWF);

      return newWF;
    } catch (error) {
      console.error('Error adding workflow:', error);
      alert('Failed to add workflow. Check console for details.');
      return null;
    }
  }

  async getWorkflowByName(name) {
    this.workflows = await this.refreshDatabase();
    const selectedWorkflow = this.workflows.find(workflow => workflow.workflowName === name);
    const SelectedWorkflowObject = new Workflow(
      selectedWorkflow.workflowName,
      selectedWorkflow.Studies,
      selectedWorkflow.trained
    );

    return SelectedWorkflowObject;

  }
  getWorkflowNames() {
    const workflowNames = this.workflows.map(workflow => workflow.workflowName);
    return workflowNames;
  }

  deleteStudyFromWorkflow(workflowName, studyInstanceUid) {
    const workflow = this.workflows.find(wf => wf.workflowName === workflowName);
    if (workflow) {
      workflow.Studies = workflow.Studies.filter(uid => uid !== studyInstanceUid);
    }
  }
}

let g_workflowManagerInstance = null;

g_workflowManagerInstance = g_workflowManagerInstance || null;

// Check if an instance already exists, and create a new one if it doesn't
if (!g_workflowManagerInstance) {
  g_workflowManagerInstance = new WorkflowManager();
}

export default g_workflowManagerInstance;
