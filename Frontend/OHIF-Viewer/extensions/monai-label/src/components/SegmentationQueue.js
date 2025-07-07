class SegmentationQueue {
  constructor(queueName) {
    this.queue = [];
    this.queueUI = [],
      this.request = {};
    this.isProcessing = false;
    this.name = queueName;
  }

  // Add a new segmentation request to the queue
  enqueue(request) {

    console.log(request.PatientName)
    this.queue.push(request);

    this.queueUI.push({ "value": request.image, "label": request.PatientName + '-' + request.model })

    this.request = request;
    console.log('queue length: ', this.queue.length);
  }

  static getRequest() {
    return this.request;
  }

  removeRequest(value, label, PatientName) {

    this.queueUI = this.queueUI.filter(
      option => option.value !== value || option.label !== label
    );

    const modelFromLabel = label.split('-')[1];

    console.log("modelFromLabel:", modelFromLabel)

    console.log("model", modelFromLabel)
    // Filter out the request from this.queue based on image and model
    this.queue = this.queue.filter(
      (request) => request.image !== value || request.model !== modelFromLabel
    );
    console.log(
      `Removed request from SegmentationQueue: { value: ${value}, label: ${label} }`
    );
    console.log(this.queueUI)
  }

  // Process the queue, executing one request at a time
  async processQueue() {
    if (this.queue.length === 0) {
      console.log('Empty Queue now!');

      const notification = {
        title: 'MONAI Label',
        message: 'Empty Queue now!.',
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
    this.queueUI.shift();
    const currentTask = this.queue.shift(); // Get the first task in the queue
    console.log(currentTask);


    const response = await currentTask.client.segmentation(
      currentTask.model,
      currentTask.image,
      currentTask.params
    );
    try {
      const nid = currentTask.notification.show({
        title: 'MONAI Label',
        message: `Running Model:${currentTask.model}....`,
        type: 'info',
        duration: 60000,
      });

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
          duration: 5000,
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
      if (this.queue.length > 0) {
        this.processQueue(); // Continue processing the next task
      }
      else {
        return;
      }
    }
  }
}

export default SegmentationQueue;
