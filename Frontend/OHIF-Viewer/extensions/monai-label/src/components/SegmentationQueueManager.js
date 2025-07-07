import SegmentationQueue from '../components/SegmentationQueue';

class QueueManager {
  constructor() {
    // A dictionary to store multiple queues, where the key: name of the queue and the value: the queue itself
    this.queues = {};
    this.queuesUI = []; // the queues in the ui (names of the queues only)
  }

  createQueue(name) {
    console.log('Create queue entered');
    if (this.queues[name]) {
      return false; // Indicate that the queue already exists
    } else {
      this.queues[name] = new SegmentationQueue(name);
      this.queuesUI.push({ value: name, label: name }); // Push the queue name
      console.log(`Created queue: ${name}`);
      return true; // Indicate successful creation
    }
  }

  getQueue(name) {
    return this.queues[name];
  }

  removeQueue(name) {
    if (this.queues[name]) {
      delete this.queues[name];
      console.log(`Deleted queue: ${name}`);
      this.queuesUI = this.queuesUI.filter(
        (option) => option.value !== name || option.label !== name
      );
    }
  }

}

let globalSegmentationQueueManager = null;

globalSegmentationQueueManager = globalSegmentationQueueManager || null;

// Check if an instance already exists, and create a new one if it doesn't
if (!globalSegmentationQueueManager) {
  globalSegmentationQueueManager = new QueueManager();
}

export default globalSegmentationQueueManager;
