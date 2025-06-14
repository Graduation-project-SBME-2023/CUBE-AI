### Patch Documentation: OHIF Patch Processing with Queue Feature for Segmentation Tasks

#### Overview

This patch introduces a new feature in the OHIF UI to support patch processing of segmentation tasks by adding a "Queue" functionality. The feature allows users to enqueue multiple segmentation tasks, view and manage the queue, and execute all tasks sequentially. The following elements have been added:
- **Queue label and ComboBox**: Displays all segmentation tasks currently in the queue.
- **Run Queue Button**: Executes all tasks sequentially from the queue.
- **Delete Button**: Removes a selected segmentation task from the queue.
- **Add to Queue Button**: Allows appending a segmentation task to the queue from the study page.

This modification enhances the usability of segmentation workflows, especially for patch processing, by providing a queue management system for handling multiple tasks.

#### Usage Guide

To use the Queue feature, follow these steps:

1. **Browse the Study Page**: Navigate to the study where you want to append tasks to the queue.
2. **Connect to MONAI Label**: Ensure you're connected to MONAI for model inference.
3. **Select the Model**: From the ComboBox on the study page, select the model for segmentation.
4. **Add Task to Queue**: Click the "Add to Queue" button to enqueue the task for processing.
5. **Repeat**: Repeat the steps for all the segmentation tasks you want to add to the queue.
6. **Navigate to OHIF Home Page**: After enqueuing all desired tasks, return to the OHIF home page.
7. **Run Queue**: Press the "Run Queue" button to execute all queued segmentation tasks sequentially.

#### Modified and Added Files

- **Modified Files:**
  - `.\extensions\monai-label\src\components\actions\AutoSegmentation.tsx`
  - `.\platform\ui\src\components\StudyListPagination\StudyListPagination.tsx`

- **Added Files:**
  - `.\extensions\monai-label\src\components\SegmentationQueue.js`
  - `.\platform\ui\src\components\QueueOptionsManager.js`

#### Code Changes

1. **AutoSegmentation.tsx**:
   - Added the `onSegmentationEnqueue` function, which mirrors the functionality of the existing `onSegmentation` function. However, instead of running the model directly, this function collects the necessary data for segmentation and stores it in a structure. This structure is passed to a higher layer, `SegmentationQueue`, where the task is appended to the queue.
   - Added a new button, **Add to Queue**, which triggers the `onSegmentationEnqueue` function to append the task to the queue.

2. **SegmentationQueue.js**:
   - **Backend queue functionality**:
     - `enqueue(request)`: Adds a segmentation request to the queue.
     - `removeRequest(value, label)`: Removes a specific request from the queue based on its value and label.
     - `processQueue`: Runs all the requests in the queue sequentially. This function is triggered by pressing the **Run Queue** button.
   - A single instance of the queue is created and maintained throughout the runtime.

3. **StudyListPagination.tsx**:
   - Added new UI components to the home page:
     - **Queue Label**: A label to indicate the queue.
     - **ComboBox**: Displays all queued segmentation tasks.
     - **Run Queue Button**: Executes the tasks in the queue sequentially.
     - **Delete Button**: Removes the selected segmentation task from the ComboBox and the backend queue.
     - **Add to Queue Button**: Available on the study page to enqueue a segmentation task.
   - Utilized the `useEffect` hook to listen for changes in the queue and update the ComboBox options accordingly.
   - Implemented handlers for removing a selected option from the ComboBox and from the backend queue.

4. **QueueOptionsManager.js**:
   - This file manages the front-end operations of the queue. It handles the synchronization between the ComboBox options and the backend queue. Tasks can be appended or deleted from the ComboBox, ensuring that changes are reflected in both the front-end UI and backend queue.

