import SegmentationQueue from '../../../../extensions/monai-label/src/components/SegmentationQueue';

class QueueOptionsManager {
  constructor() {
    this.queueOptions = [{}];
    this.onChangeCallbacks = [];
  }

  // Method to get the current queue options
  getQueueOptions() {
    return this.queueOptions;
  }

  onChange(callback) {
    this.onChangeCallbacks.push(callback);
  }

  // Method to notify all subscribers of changes
  notifyChange() {
    this.onChangeCallbacks.forEach(callback => callback(this.queueOptions));
  }

  addQueueOption(request) {
    if (request && request.image && request.model) {
      const value = request.image;
      const label = request.model;

      this.queueOptions.push({ value, label });

      console.log(`Added to QueueOptions: { value: ${value}, label: ${label} }`);
      this.notifyChange();
    } else {
      console.log('No valid request found to add to QueueOptions.');
    }
  }

  // Method to remove an option from the queue using the Remove Button
  removeQueueOption(value, label) {
    this.queueOptions = this.queueOptions.filter(
      option => option.value !== value || option.label !== label
    );
    console.log(`Removed from QueueOptions: { value: ${value}, label: ${label} }`);
    this.notifyChange();
  }

  // Method to update the options with a new queue
  updateQueueOptions(queue) {
    this.queueOptions = [];

    // Iterate over the queue and add each request's image and model
    queue.forEach(request => {
      if (request && request.image && request.model) {
        const value = request.image;
        const label = request.model;

        this.queueOptions.push({ value, label });
        console.log(`Updated QueueOptions: Added { value: ${value}, label: ${label} }`);
      }
    });
    this.notifyChange();
  }
}

let globalSegmentationQueueUI = null;

globalSegmentationQueueUI = globalSegmentationQueueUI || null;

if (!globalSegmentationQueueUI) {
  globalSegmentationQueueUI = new QueueOptionsManager();
}

export default globalSegmentationQueueUI;
