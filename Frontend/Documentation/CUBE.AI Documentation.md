# Workflow Implementation Guide

## 1. WorkflowList Component

### Overview

The `WorkflowList` component displays all created workflow names on the right side of the homepage.

### File Structure

Create a new folder at:

```
platform\ui\src\components
```

Name it `WorkflowList` and add two files to that folder:

- `index.js`

```javascript
export { default } from "./WorkflowList";
```

- `WorkflowList.tsx`: This file contains the `WorkflowList` component, which will later be integrated into the `WorkList.tsx` file (home page). **File Link:** [WorkflowList.tsx](https://drive.google.com/file/d/1axMA5D7_pD6PLCvGbQa_v81LUM4enjOR/view?usp=sharing)

### Necessary Imports

Update `platform\ui\src\index.js` to export `WorkflowList`:

```javascript
/** COMPONENTS */
export {
  ..
  WorkflowList,
} from './components';
```

In `platform\ui\src\components\index.js`:

add the following import:

```javascript
import WorkflowList from "./WorkflowList";
```

then add `WorkflowList` to the export list:

```javascript
export {
  ..
  WorkflowList,
};
```

## 2. WFModal

### Purpose

Manages user input for creating workflows.

### File Location

Create a new file at:

```
platform\app\src\routes\WorkList
```

Name it: `WFModal.tsx`

**File Link:** [WFModal.tsx](https://drive.google.com/file/d/1c3bXseJ2kEjVWtZ5ChdjRACi3ujZoV-u/view?usp=sharing)

## 3. WorkList File

### Overview

`WorkList.tsx` is an existing file where we integrate three components:

- `WFModal` – handles workflow creation.
- `WorkflowList` – displays all workflow names.
- A combobox – allows adding studies to workflows.

**Path:** `platform\app\src\routes\WorkList\WorkList.tsx`

### Key Integrations

#### 1. State Management

Defines state for:

- Workflow list
- Modal visibility
- Workflow name input
- Error messages

#### 2. Fetching Workflow Names

Fetches and updates workflow names.

#### 3. Creating a Workflow

Checks for duplicates, creates workflows, and updates the list.

#### 4. Integrating Workflow Names in Study Table

Enhances the table structure to display workflow names per study.

#### 5. Appending a Study to a Workflow

Sends a request to associate studies with workflows and updates the UI.

**File Link:** [Worklist.tsx](https://drive.google.com/file/d/1b1_x3TOeFAlEApptlrSRElvNr18154WM/view?usp=sharing)

## 4. Workflow Pages Routes

### Updating Routes

Update the routes in:

```
platform\app\src\routes\index.tsx
```

#### Import `WorkflowDetailPage`:

```javascript
import WorkflowDetailPage from "./Workflow/WorkflowDetails";
```

#### Add workflow routes in `createRoutes` function:

```javascript
const workflowRoutes = [
  {
    path: "/workflow/:workflowName",
    children: DataSourceWrapper,
    private: true,
    props: { children: WorkflowDetailPage, servicesManager, extensionManager },
  },
];
```

#### Integrate into `allRoutes`:

```javascript
const allRoutes = [
  ...routes,
  ...workflowRoutes,
  ...(showStudyList ? [WorkListRoute] : []),
  ...(customRoutes?.routes || []),
  ...bakedInRoutes,
  customRoutes?.notFoundRoute || notFoundRoute,
];
```

## 5. Workflow Folder

### Structure

Create a new folder at:

```
platform\app\src\routes
```

Name it: `Workflow`

### Files

#### `Workflow.js`

- Defines a class with attributes for `workflowName`, associated studies, and a trained flag.
- Includes methods for managing workflows and training configurations.

**File Link:** [Workflow.js](https://drive.google.com/file/d/1OYZlU0nabgMtF8vH9XXprKvJ3cOV21pu/view?usp=sharing)

#### `WorkflowManager.js`

Manages workflow-related operations such as:

- Creating workflows
- Fetching workflow names
- Appending studies to workflows

**File Link:** [WorkflowManager.js](https://drive.google.com/file/d/1OSlp3dG5s9PQs1PP-MSh8ZyM_bk1cN_2/view?usp=sharing)

#### `WorkflowDetails.tsx`

Handles UI rendering for workflow details and manages:

- Server communication
- Model creation
- Training process

**File Link:** [WorkflowDetails.tsx](https://drive.google.com/file/d/1PHeg9MVflOsLEJQVWSyyqMIY79jaSpm8/view?usp=sharing)

## 6. MonaiLabelPanel Updates

### File Path

```
extensions\monai-label\src\components\MonaiLabelPanel.tsx
```

### Key Updates

#### 1. Workflow Name Handling

- Extracts `workflowName` from the URL query string.
- Stores `workflowName` as a class property for use in `uploadLabels`.

#### 2. Connection Status Handling

- Introduces `isMONAILabelConnected` to track connection status.
- Conditionally disables the "Upload Labels" button if the server is not connected.

#### 3. Upload Labels Functionality

- Adds `uploadLabels` method to handle label uploads to the MONAI Label server.
- Sends a POST API request to update workflow information, including `series` and `instance uid` of the study along with `modality`.

#### 4. Component Rendering

- Conditionally renders the "Upload Labels" button based on the presence of `workflowName`.

#### 5. Disabling Training and Active Learning Functionalities

Comment out the following components:

```jsx
<OptionTable>
<ActiveLearning>
<SmartEdit>
```

**File Link:** [MonaiLabelPanel.tsx](https://drive.google.com/file/d/1G7OHqVDfoPFeFOBEGfPOrOgwFJjIsTiR/view?usp=sharing)

## Change Display Name of MonaiLabel to Assisted Segmentation

**File Path:**

```
modes\monai-label\src\index.tsx
```

Update the `displayName`:

```javascript
displayName: 'Assisted Segmentationn',
```
