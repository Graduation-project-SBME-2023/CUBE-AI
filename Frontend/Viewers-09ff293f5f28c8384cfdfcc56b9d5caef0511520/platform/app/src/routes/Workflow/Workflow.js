class Workflow {
  constructor(name, studies, trainFlag = false) {
    this.workflowName = name;
    this.Studies = studies;
    this.trained = trainFlag;
  }
  addStudy(studyUID) {
    // Check if the studyUID already exists in the Studies list
    if (this.Studies.includes(studyUID)) {
      console.log(`Study with UID ${studyUID} already exists in the workflow.`);
      return; // Exit the function if it's a duplicate
    }

    // Add the studyUID to the list if it's not a duplicate
    this.Studies.push(studyUID);
  }
  deleteStudy(studyUID) {}
  trainWF() {
    this.trained = true;
    console.log('train');
  }
}

export default Workflow;
