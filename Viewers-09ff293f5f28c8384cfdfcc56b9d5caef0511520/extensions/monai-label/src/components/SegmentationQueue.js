import QueueOptionsManager from '../../../../platform/ui/src/components/QueueOptionsManager';

class SegmentationQueue {
  constructor() {
    this.queue = [];
    this.request = {};
    this.isProcessing = false;
  }

  // Add a new segmentation request to the queue
  enqueue(request) {
    this.queue.push(request);
    // const ui_comboboxInstance = 'request.model';
    // const QueueOptions = getQueueOptions();
    // QueueOptions.append(ui_comboboxInstance);
    this.request = request;
    QueueOptionsManager.addQueueOption(request);

    console.log('queue length: ', this.queue.length);
  }

  static getRequest() {
    return this.request;
  }

  removeRequest(value, label) {
    this.queue = this.queue.filter(
      (request) => request.image !== value || request.model !== label
    );
    console.log(
      `Removed request from SegmentationQueue: { value: ${value}, label: ${label} }`
    );
  }

  // Process the queue, executing one request at a time
  async processQueue() {
    if (this.queue.length === 0) {
      console.log('empty queue');

      const notification = {
        title: 'MONAI Label',
        message: 'The queue is empty. Please add a segmentation request.',
        type: 'warning',
        duration: 5000,
      };

      // Display the notification using a global notification system or a custom function
      if (window.snackbar) {
        window.snackbar.show(notification);
      } else {
        alert(notification.message); // Fallback to using a basic alert if no notification system is available
      }

      this.isProcessing = false;
      return;
    }

    this.isProcessing = true;
    const currentTask = this.queue.shift(); // Get the first task in the queue
    console.log(currentTask);

    try {
      const nid = currentTask.notification.show({
        title: 'MONAI Label',
        message: 'Running Auto-Segmentation...',
        type: 'info',
        duration: 60000,
      });

      const response = await currentTask.client.segmentation(
        currentTask.model,
        currentTask.image,
        currentTask.params
      );

      if (!nid) {
        window.snackbar.hideAll();
      } else {
        currentTask.notification.hide(nid);
      }

      if (response.status !== 200) {
        currentTask.notification.show({
          title: 'MONAI Label',
          message: 'Failed to Run Segmentation',
          type: 'error',
          duration: 5000,
        });
      } else {
        currentTask.notification.show({
          title: 'MONAI Label',
          message: 'Run Segmentation - Successful',
          type: 'success',
          duration: 2000,
        });

        await currentTask.updateView(response, currentTask.labels);
      }
    } catch (error) {
      currentTask.notification.show({
        title: 'MONAI Label',
        message: 'An error occurred during segmentation',
        type: 'error',
        duration: 5000,
      });
    } finally {
      console.log('queue length: ', this.queue.length);
      QueueOptionsManager.updateQueueOptions(this.queue); //update the elements in queue after processing each task
      this.processQueue(); // Continue processing the next task
    }
  }
}
let globalSegmentationQueue = null;

globalSegmentationQueue = globalSegmentationQueue || null;

// Check if an instance already exists, and create a new one if it doesn't
if (!globalSegmentationQueue) {
  globalSegmentationQueue = new SegmentationQueue();
}
// const globalSegmentationQueue = new SegmentationQueue();

export default globalSegmentationQueue;
