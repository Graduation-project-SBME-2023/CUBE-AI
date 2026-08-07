import React from 'react';
import ModelSelector from '../ModelSelector';
import BaseTab from './BaseTab';
import * as cornerstoneTools from '@cornerstonejs/tools';
import ModelBasedRendering from '../../tools/ModelBasedRendering';
import { getRenderingEngines, cache, eventTarget } from '@cornerstonejs/core';
import { getAgentsApiBaseUrl } from '../../utils/runtimeUrls';

const SEGMENTATION_SUBMITTED_MESSAGE = 'cube:segmentation-submitted';

export default class InteractiveSegmentation extends BaseTab {
  modelSelector: React.RefObject<ModelSelector>;
  static instance = null;
  constructor(props) {
    super(props);
    // InteractiveSegmentation.instance = this;

    this.modelSelector = React.createRef<ModelSelector>();
    const models = this.getModels();
    this.state = {
      currentModel: models.length > 0 ? models[0] : null, // null brings so much errors
      currentObject: 1,
      clickPoints: new Map(),
      flagNninteractive: { positive: false, negative: false },
      isSubmitting: false,
      activeTool: null,
      statusMessage: '',
      statusType: 'info',
      hasPrompts: false,
      voxtellTextPrompt: '',

      // availableOrgans: models.length > 0 ? this.getModelLabels(models[0]) : {},
    };

    const { viewportGridService } = this.props.servicesManager.services;

    this.viewportGridService = viewportGridService;
    this.prompts = {};
    this.AuxillaryBox = {};
    this.AuxillaryID = null;
    this.sam2dstate = null;
    this.currentSlice = null;
    this.resetnnInteractiveAll = false;
  }
  // to remove active listner later
  activeListeners = [];
  promptHistory = [];

  handleAnnotationCompleted = (evt) => {
    const annotation = evt.detail?.annotation;
    const toolName = annotation?.metadata?.toolName;
    const isPointPrompt =
      toolName === 'ProbeMONAILabelPos' || toolName === 'ProbeMONAILabelNeg';
    const isSamPrompt =
      this.state.currentModel === 'sam' &&
      (isPointPrompt || toolName === 'RecROIToolMONAILabel');
    const shouldTrackPrompt =
      (isPointPrompt &&
        ['vista', 'nninteractive'].includes(this.state.currentModel)) ||
      isSamPrompt;

    if (
      shouldTrackPrompt &&
      annotation.annotationUID &&
      !this.promptHistory.some(
        (entry) => entry.annotationUID === annotation.annotationUID
      )
    ) {
      this.promptHistory.push({
        annotationUID: annotation.annotationUID,
        toolName,
        objectId: this.state.currentObject,
        viewportId: evt.detail?.viewportId,
      });
    }
    this.setState({ hasPrompts: this.checkHasPrompts() });
  };

  checkHasPrompts = () => {
    try {
      const manager = cornerstoneTools.annotation.state.getAnnotationManager();
      if (!manager) return false;
      const annotations = manager.getAllAnnotations();
      const promptToolNames = [
        'ProbeMONAILabelPos',
        'ProbeMONAILabelNeg',
        'RecROIToolMONAILabel',
      ];
      return annotations.some((a) => promptToolNames.includes(a.metadata?.toolName));
    } catch (e) {
      return false;
    }
  };

  componentDidMount() {
    eventTarget.addEventListener(
      cornerstoneTools.Enums.Events.ANNOTATION_COMPLETED,
      this.handleAnnotationCompleted
    );
  }

  initPromptObject = (objectId) => {
    //[[{POINTS:[[[25,10]],[1]], BOXES:[]}],[],]
    if (!this.prompts[objectId]) {
      this.prompts[objectId] = {
        points: [[], []], // [coords[], labels[]]
        boxes: [],
      };
    }
  };
  getLastAnnotations = (worldToIndex) => {
    const manager = cornerstoneTools.annotation.state.getAnnotationManager();
    const saved = manager.saveAnnotations();
    console.log('iam inside get last annotations', saved);

    const frameOfReferenceUID = Object.keys(saved)[0];
    if (!frameOfReferenceUID) {
      return null;
    }

    const annotations = saved[frameOfReferenceUID];

    const positive_annotations = annotations['ProbeMONAILabelPos'] || {};
    const negative_annotations = annotations['ProbeMONAILabelNeg'] || {};
    const box_annotations = annotations['RecROIToolMONAILabel'] || {};

    const result = { positive_points: [], negative_points: [], boxes: [] };

    // last positive
    const posKeys = Object.keys(positive_annotations);
    if (posKeys.length > 0) {
      const lastKey = posKeys[posKeys.length - 1];
      const pt = positive_annotations[lastKey].data.handles.points[0];
      console.log('the last positive point', pt);
      result.positive_points.push([
        ...this.correct_xy_format(worldToIndex(pt)).map((value, i) =>
          i === 2 ? Math.round(value) : value
        ),
        2,
      ]);
    }

    // last negative
    const negKeys = Object.keys(negative_annotations);
    if (negKeys.length > 0) {
      const lastKey = negKeys[negKeys.length - 1];
      const pt = negative_annotations[lastKey].data.handles.points[0];
      result.negative_points.push([
        ...this.correct_xy_format(worldToIndex(pt)).map((value, i) =>
          i === 2 ? Math.round(value) : value
        ),
        2,
      ]);
    }

    // last box
    const boxKeys = Object.keys(box_annotations);
    if (boxKeys.length > 0) {
      const lastKey = boxKeys[boxKeys.length - 1];
      const pt1 = box_annotations[lastKey].data.handles.points[0];
      const pt2 = box_annotations[lastKey].data.handles.points[3];
      result.boxes.push([
        this.correct_xy_format(worldToIndex(pt1)).map((value, i) =>
          i === 2 ? Math.round(value) : value
        ),
        this.correct_xy_format(worldToIndex(pt2)).map((value, i) =>
          i === 2 ? Math.round(value) : value
        ),
      ]);
    }

    return result;
  };

  cleanupViewportListeners = () => {
    this.activeListeners.forEach(({ element, type, handler }) => {
      element.removeEventListener(type, handler);
    });
    this.activeListeners = [];
    console.log('cleaned and removed old viewport listeners');
  };

  deactivatePromptTools = () => {
    this.cleanupViewportListeners();
    const { toolGroupService, viewportGridService } =
      this.props.servicesManager.services;
    const { viewports, activeViewportId } = viewportGridService.getState();
    const viewport = viewports.get(activeViewportId);
    const toolGroupId = viewport?.viewportOptions?.toolGroupId;
    const toolGroup = toolGroupId
      ? toolGroupService.getToolGroup(toolGroupId)
      : null;

    if (!toolGroup) {
      return;
    }

    [
      'ProbeMONAILabelPos',
      'ProbeMONAILabelNeg',
      'RecROIToolMONAILabel',
    ].forEach((toolName) => {
      try {
        toolGroup.setToolPassive(toolName);
      } catch (error) {
        console.debug(`Unable to set ${toolName} passive`, error);
      }
    });
  };

  handleUpdatePrompts = (mode) => {
    //todo: instead of timeout , use event listner as the cornerstone creation take time not reliable !
    setTimeout(() => {
      const { cornerstoneViewportService } =
        this.props.servicesManager.services;
      const { viewport } = this.props.getActiveViewportInfo();
      const viewportInfo = cornerstoneViewportService.getViewportInfo(
        viewport.viewportId
      );
      const currentViewPort = viewportInfo.viewportId;
      const { worldToIndex } =
        viewportInfo.viewportData.data[0].volume.imageData;
      const { currentObject } = this.state;
      console.log('the current object is ', currentObject);
      const result = this.getLastAnnotations(worldToIndex);
      console.log('the results getted from the function is ', result);
      this.initPromptObject(currentObject);

      if (
        (mode === 'positive' || mode === 'negative') &&
        currentViewPort !== 'mpr-axial'
      ) {
        this.setState({
          statusMessage:
            'Point prompts are supported only in the axial view. Switch to axial and try again.',
          statusType: 'error',
        });
        return; // stop execution, don’t call sam2d
      }

      if (mode === 'positive' && result?.positive_points.length > 0) {
        const pt = result.positive_points[0];
        console.log('Positive point added', pt);

        this.prompts[currentObject].points[0].push(pt); // coords
        this.prompts[currentObject].points[1].push(1); // label
        this.sam2dstate = '2d';
        this.currentSlice = pt[2];
        console.log('the current slice is ', this.currentSlice);
      }

      if (mode === 'negative' && result?.negative_points.length > 0) {
        const pt = result.negative_points[0];
        console.log('Negative point added', pt);

        this.prompts[currentObject].points[0].push(pt); // coords
        this.prompts[currentObject].points[1].push(0); // label
        this.sam2dstate = '2d';
        this.currentSlice = pt[2];
      }
      if (mode === 'box' && result?.boxes.length > 0) {
        const [[x1, y1, z1], [x2, y2, z2]] = result.boxes[0];

        console.log('the z1 is', z1, 'the z2 is', z2);
        console.log('the x1', x1, 'the x2 is', x2, 'the y1', y1, 'the y2', y2);

        if (currentViewPort === 'mpr-axial') {
          const xmin = Math.min(x1, x2);
          const xmax = Math.max(x1, x2);
          const ymin = Math.min(y1, y2);
          const ymax = Math.max(y1, y2);
          const slice_index = z1;
          this.currentSlice = slice_index;

          const boxEntry = [[xmin, ymin, xmax, ymax], slice_index, 2];
          console.log('the boxentry is ', boxEntry);
          this.prompts[currentObject].boxes.push(boxEntry);
          this.sam2dstate = '2d';
        } else {
          let xmin, xmax, ymin, ymax, slice_index, planeIndex;

          if (currentViewPort === 'mpr-sagittal') {
            xmin = Math.min(z1, z2);
            xmax = Math.max(z1, z2);
            ymin = Math.min(y1, y2);
            ymax = Math.max(y1, y2);
            slice_index = x1;
            planeIndex = 0;
          } else {
            // coronal
            xmin = Math.min(z1, z2);
            xmax = Math.max(z1, z2);
            ymin = Math.min(x1, x2);
            ymax = Math.max(x1, x2);
            slice_index = y1 + 1;
            planeIndex = 1;
          }

          this.currentSlice = slice_index;
          const boxEntry = [[xmin, ymin, xmax, ymax], slice_index, planeIndex];

          console.log('the box entry for Aux ', boxEntry);
          this.AuxillaryBox = boxEntry;
          this.sam2dstate = 'Auxillary_Box';
          this.AuxillaryID = currentObject;
        }

        for (let index = this.promptHistory.length - 1; index >= 0; index--) {
          const historyEntry = this.promptHistory[index];
          if (
            historyEntry.toolName === 'RecROIToolMONAILabel' &&
            String(historyEntry.objectId) === String(currentObject)
          ) {
            historyEntry.isAuxiliaryBox = currentViewPort !== 'mpr-axial';
            break;
          }
        }
      }
      console.log('the prompts is ,', this.prompts);
    }, 500);
  };

  configureViewportInteraction = (mode: 'positive' | 'negative') => {
    if (!this.viewportGridService) {
      return;
    }

    this.cleanupViewportListeners(); // remove old listeners

    const viewports = this.viewportGridService.getState().viewports;
    const renderingEngines = getRenderingEngines();

    const handleClick = (evt) => {
      console.log('point clicked in mode', mode);

      if (this.state.currentModel === 'sam') {
        this.handleUpdatePrompts(mode);
      } else if (this.state.currentModel === 'nninteractive') {
        if (mode === 'positive' && !this.state.flagNninteractive.positive) {
          const { toolGroupService, viewportGridService } =
            this.props.servicesManager.services;
          const { viewports, activeViewportId } =
            viewportGridService.getState();
          const viewport = viewports.get(activeViewportId);
          const { viewportOptions } = viewport;
          const toolGroupId = viewportOptions.toolGroupId;

          // Use setToolPassive instead of setToolDisabled

          this.setState((prevState) => ({
            flagNninteractive: {
              ...prevState.flagNninteractive,
              positive: true,
            },
          }));
          setTimeout(() => {
            toolGroupService
              .getToolGroup(toolGroupId)
              .setToolPassive('ProbeMONAILabelPos');
          }, 500);
        } else if (
          mode === 'negative' &&
          !this.state.flagNninteractive.negative
        ) {
          const { toolGroupService, viewportGridService } =
            this.props.servicesManager.services;
          const { viewports, activeViewportId } =
            viewportGridService.getState();
          const viewport = viewports.get(activeViewportId);
          const { viewportOptions } = viewport;
          const toolGroupId = viewportOptions.toolGroupId;

          this.setState((prevState) => ({
            flagNninteractive: {
              ...prevState.flagNninteractive,
              negative: true,
            },
          }));
          setTimeout(() => {
            toolGroupService
              .getToolGroup(toolGroupId)
              .setToolPassive('ProbeMONAILabelNeg');
          }, 500);
        }
      }
    };

    viewports.forEach((vpConfig) => {
      const viewport = renderingEngines[0].getViewport(vpConfig.viewportId);
      if (!viewport?.element) {
        return;
      }

      viewport.element.addEventListener('click', handleClick);
      this.activeListeners.push({
        element: viewport.element,
        type: 'click',
        handler: handleClick,
      });
    });

    console.log(`Viewport listeners configured for ${mode} points`);
  };

  configureViewportBoxInteraction = () => {
    if (!this.viewportGridService) {
      return;
    }

    this.cleanupViewportListeners();

    const viewports = this.viewportGridService.getState().viewports;
    const renderingEngines = getRenderingEngines();

    const holdBoxStartMap = new Map();
    const clickBoxStartMap = new Map();
    // apply threshold to prevent detection twice of the box
    const movementThreshold = 5; // px to detect drag
    const clickDistanceThreshold = 20; // px distance for 2-click mode

    // option one for press hold and release
    const handleDown = (evt) => {
      holdBoxStartMap.set(evt.currentTarget, {
        x: evt.offsetX,
        y: evt.offsetY,
        moved: false,
      });
    };

    const handleMove = (evt) => {
      if (holdBoxStartMap.has(evt.currentTarget)) {
        const start = holdBoxStartMap.get(evt.currentTarget);
        const dx = evt.offsetX - start.x;
        const dy = evt.offsetY - start.y;
        if (
          Math.abs(dx) > movementThreshold ||
          Math.abs(dy) > movementThreshold
        ) {
          start.moved = true; // mark as drag
          console.log(
            `Hold Box still from (${start.x}, ${start.y}) → (${evt.offsetX}, ${evt.offsetY})`
          );
        }
      }
    };

    const handleUp = (evt) => {
      if (holdBoxStartMap.has(evt.currentTarget)) {
        const start = holdBoxStartMap.get(evt.currentTarget);
        if (start.moved) {
          const end = { x: evt.offsetX, y: evt.offsetY };
          console.log(
            `Hold Box finished from (${start.x}, ${start.y}) → (${end.x}, ${end.y})`
          );
          if (this.state.currentModel === 'sam') {
            this.handleUpdatePrompts('box');
          }
        }
        holdBoxStartMap.delete(evt.currentTarget);
      }
    };

    // option two for two clicks
    const handleClick = (evt) => {
      const element = evt.currentTarget;

      if (!clickBoxStartMap.has(element)) {
        clickBoxStartMap.set(element, { x: evt.offsetX, y: evt.offsetY });
        console.log('Click Box start', evt.offsetX, evt.offsetY);
      } else {
        // only if far enough
        const start = clickBoxStartMap.get(element);
        const dx = evt.offsetX - start.x;
        const dy = evt.offsetY - start.y;
        const distance = Math.sqrt(dx * dx + dy * dy);

        if (distance > clickDistanceThreshold) {
          console.log(
            ` Click Box completed from (${start.x}, ${start.y}) → (${evt.offsetX}, ${evt.offsetY})`
          );
          if (this.state.currentModel === 'sam') {
            this.handleUpdatePrompts('box');
          }
        } else {
          console.log('not a complete box ');
        }

        clickBoxStartMap.delete(element);
      }
    };

    viewports.forEach((vpConfig) => {
      const viewport = renderingEngines[0].getViewport(vpConfig.viewportId);
      if (!viewport?.element) {
        return;
      }

      viewport.element.addEventListener('mousedown', handleDown);
      viewport.element.addEventListener('mousemove', handleMove);
      viewport.element.addEventListener('mouseup', handleUp);
      viewport.element.addEventListener('click', handleClick);

      this.activeListeners.push({
        element: viewport.element,
        type: 'mousedown',
        handler: handleDown,
      });
      this.activeListeners.push({
        element: viewport.element,
        type: 'mousemove',
        handler: handleMove,
      });
      this.activeListeners.push({
        element: viewport.element,
        type: 'mouseup',
        handler: handleUp,
      });
      this.activeListeners.push({
        element: viewport.element,
        type: 'click',
        handler: handleClick,
      });
    });
  };

  resetProps = () => {
    this.prompts = {};
    this.AuxillaryBox = {};
    this.AuxillaryID = null;
    this.sam2dstate = null;
    this.currentSlice = null;
    this.resetnnInteractiveAll = false;
    this.voxtellTextPrompt = '';
    this.promptHistory = [];
  };

  onSelectModel = (model) => {
    // console.log('Selecting  (Point) Interaction Model...');
    this.deactivatePromptTools();
    const currentObject = 1;
    const clickPoints = new Map();
    this.setState({
      currentModel: model,
      currentObject: currentObject,
      clickPoints: clickPoints,
      // availableOrgans: this.getModelLabels(model),
      flagNninteractive: { positive: false, negative: false },
      activeTool: null,
      statusMessage: '',
      statusType: 'info',
    });
    this.resetProps();
    this.clearAllPoints();
  };

  onEnterActionTab = () => {
    this.deactivatePromptTools();
    this.setState({ activeTool: null, hasPrompts: this.checkHasPrompts() });
  };

  onLeaveActionTab = () => {
    this.cleanupViewportListeners();
    this.onChangeLabel(null);

    const { toolGroupService, viewportGridService } =
      this.props.servicesManager.services;

    const { viewports, activeViewportId } = viewportGridService.getState();
    const viewport = viewports.get(activeViewportId);
    const { viewportOptions } = viewport;
    const toolGroupId = viewportOptions.toolGroupId;

    toolGroupService
      .getToolGroup(toolGroupId)
      .setToolDisabled('ProbeMONAILabelPos');

    toolGroupService
      .getToolGroup(toolGroupId)
      .setToolDisabled('ProbeMONAILabelNeg');

    toolGroupService
      .getToolGroup(toolGroupId)
      .setToolDisabled('RecROIToolMONAILabel');

    console.info('Here we deactivate the probe');
  };

  enablePositiveProbe = (objectId) => {
    console.log('Enable Positive Probe for Object', objectId);
    this.props.commandsManager.runCommand('setToolActive', {
      toolName: 'ProbeMONAILabelPos',
    });
    this.setState({ activeTool: 'positive', statusMessage: '' });
    this.configureViewportInteraction('positive');
  };

  enableNegativeProbe = (objectId) => {
    console.log('Enable Negative Probe for Object', objectId);
    this.props.commandsManager.runCommand('setToolActive', {
      toolName: 'ProbeMONAILabelNeg',
    });
    this.setState({ activeTool: 'negative', statusMessage: '' });
    this.configureViewportInteraction('negative');
  };

  enableBoxTool = (objectId) => {
    console.log('Enable Box Tool for Object', objectId);
    this.props.commandsManager.runCommand('setToolActive', {
      toolName: 'RecROIToolMONAILabel',
    });
    this.setState({ activeTool: 'box', statusMessage: '' });
    this.configureViewportBoxInteraction();
  };
  renderAnnotations = () => {
    this.props.servicesManager.services.cornerstoneViewportService
      .getRenderingEngine()
      .render();
  };

  getPointAnnotations = () => {
    const manager = cornerstoneTools.annotation.state.getAnnotationManager();
    return manager
      .getAllAnnotations()
      .filter((annotation) =>
        ['ProbeMONAILabelPos', 'ProbeMONAILabelNeg'].includes(
          annotation.metadata?.toolName
        )
      );
  };

  syncNnInteractiveFlags = () => {
    const annotations = this.getPointAnnotations();
    this.setState({
      flagNninteractive: {
        positive: annotations.some(
          (annotation) => annotation.metadata?.toolName === 'ProbeMONAILabelPos'
        ),
        negative: annotations.some(
          (annotation) => annotation.metadata?.toolName === 'ProbeMONAILabelNeg'
        ),
      },
    });
  };

  removeSamPromptFromPayload = (historyEntry) => {
    if (!historyEntry) {
      return;
    }

    const { toolName, objectId, viewportId, isAuxiliaryBox } = historyEntry;
    const objectPrompts = this.prompts[objectId];

    if (
      toolName === 'ProbeMONAILabelPos' ||
      toolName === 'ProbeMONAILabelNeg'
    ) {
      if (!objectPrompts?.points) {
        return;
      }

      const labelToRemove = toolName === 'ProbeMONAILabelPos' ? 1 : 0;
      const labels = objectPrompts.points[1];
      const pointIndex = labels.lastIndexOf(labelToRemove);
      if (pointIndex >= 0) {
        objectPrompts.points[0].splice(pointIndex, 1);
        labels.splice(pointIndex, 1);
      }
      return;
    }

    if (toolName === 'RecROIToolMONAILabel') {
      const shouldRemoveAuxiliaryBox =
        isAuxiliaryBox === true ||
        (isAuxiliaryBox === undefined &&
          viewportId &&
          viewportId !== 'mpr-axial') ||
        (isAuxiliaryBox === undefined &&
          !viewportId &&
          this.AuxillaryID === objectId);
      if (shouldRemoveAuxiliaryBox) {
        this.AuxillaryBox = {};
        this.AuxillaryID = null;
      } else {
        objectPrompts?.boxes?.pop();
      }
    }
  };

  undoLastPoint = (objectId) => {
    const manager = cornerstoneTools.annotation.state.getAnnotationManager();
    let historyEntry = null;

    while (this.promptHistory.length && !historyEntry) {
      let candidateIndex = this.promptHistory.length - 1;
      if (objectId !== undefined && objectId !== null) {
        candidateIndex = -1;
        for (let index = this.promptHistory.length - 1; index >= 0; index--) {
          if (String(this.promptHistory[index].objectId) === String(objectId)) {
            candidateIndex = index;
            break;
          }
        }
      }

      if (candidateIndex < 0) {
        break;
      }

      const [candidate] = this.promptHistory.splice(candidateIndex, 1);
      if (manager.getAnnotation(candidate.annotationUID)) {
        historyEntry = candidate;
      }
    }

    if (!historyEntry) {
      const promptToolNames =
        this.state.currentModel === 'sam'
          ? ['ProbeMONAILabelPos', 'ProbeMONAILabelNeg', 'RecROIToolMONAILabel']
          : ['ProbeMONAILabelPos', 'ProbeMONAILabelNeg'];
      const annotations = manager
        .getAllAnnotations()
        .filter((annotation) =>
          promptToolNames.includes(annotation.metadata?.toolName)
        );
      const annotation = annotations[annotations.length - 1];
      if (annotation) {
        historyEntry = {
          annotationUID: annotation.annotationUID,
          toolName: annotation.metadata?.toolName,
          objectId: objectId ?? this.state.currentObject,
        };
      }
    }

    if (!historyEntry) {
      return;
    }

    if (this.state.currentModel === 'sam') {
      this.removeSamPromptFromPayload(historyEntry);
    }
    cornerstoneTools.annotation.state.removeAnnotation(
      historyEntry.annotationUID
    );
    this.renderAnnotations();
    this.syncNnInteractiveFlags();
    this.setState({ hasPrompts: this.checkHasPrompts() });
  };

  removeAllPromptAnnotations = () => {
    const manager = cornerstoneTools.annotation.state.getAnnotationManager();
    manager
      .getAllAnnotations()
      .filter((annotation) =>
        [
          'ProbeMONAILabelPos',
          'ProbeMONAILabelNeg',
          'RecROIToolMONAILabel',
        ].includes(annotation.metadata?.toolName)
      )
      .forEach((annotation) =>
        cornerstoneTools.annotation.state.removeAnnotation(
          annotation.annotationUID
        )
      );
    this.promptHistory = [];
    this.renderAnnotations();
    this.setState({ hasPrompts: false });
  };

  correct_xy_format = (point) => {
    return [point[1], point[0], point[2]];
  };

  getPointPrompts = (worldToIndex, swapXY = false) => {
    return this.getPointAnnotations().map((annotation) => {
      const toolName = annotation.metadata?.toolName;
      let point = worldToIndex(annotation.data.handles.points[0]);
      if (swapXY) {
        point = this.correct_xy_format(point);
      }

      return {
        point: point.map((value, index) =>
          !swapXY || index === 2 ? Math.round(value) : value
        ),
        label: toolName === 'ProbeMONAILabelPos' ? 1 : 0,
      };
    });
  };

  getNnInteractivePrompts = (worldToIndex) => {
    return this.getPointPrompts(worldToIndex);
  };

  getVistaPrompts = (worldToIndex) => {
    return this.getPointPrompts(worldToIndex, true);
  };

  onRunInference = async () => {
    console.log(' run inference start');
    this.setState({ statusMessage: '', statusType: 'info' });
    const { currentModel, currentObject, clickPoints } = this.state;
    const { info } = this.props;
    const { viewport, displaySet } = this.props.getActiveViewportInfo();

    const models = this.getModels();
    let selectedModel = 0;
    for (const model of models) {
      if (!currentModel || model === currentModel) {
        break;
      }
      selectedModel++;
    }

    const model = models.length > 0 ? models[selectedModel] : null;
    if (!model) {
      this.setState({
        statusMessage: 'Select a model before running inference.',
        statusType: 'error',
      });
      return;
    }

    const { cornerstoneViewportService } = this.props.servicesManager.services;
    const viewportInfo = cornerstoneViewportService.getViewportInfo(
      viewport.viewportId
    );
    const { worldToIndex } = viewportInfo.viewportData.data[0].volume.imageData;

    const manager = cornerstoneTools.annotation.state.getAnnotationManager();
    clickPoints[currentObject] = manager.saveAnnotations();

    // =============== need to test why those probes are undefined =========

    // const positive_points = {};
    // const negative_points = {};
    // const boxes = {};
    // const object_ids = [];
    // const framOfReferenceUID = Object.keys(clickPoints[currentObject])[0];
    // for (const object_id in clickPoints) {
    //   const negative_annotations =
    //     clickPoints[object_id][framOfReferenceUID]['ProbeMONAILabelNeg'];
    //   const positive_annotations =
    //     clickPoints[object_id][framOfReferenceUID]['ProbeMONAILabelPos'];
    //   const box_annotations =
    //     clickPoints[object_id][framOfReferenceUID]['RecROIToolMONAILabel'];
    //   negative_points[object_id] = [];
    //   for (const neg_annotation in negative_annotations) {
    //     console.log(neg_annotation);
    //     const pt = negative_annotations[neg_annotation].data.handles.points[0];
    //     negative_points[object_id].push(this.correct_xy_format(worldToIndex(pt)));
    //     // negative_points[object_id].push(this.correct_xy_format(worldToIndex(pt).map(Math.round)));
    //   }

    //   positive_points[object_id] = [];
    //   for (const pos_annotation in positive_annotations) {
    //     const pt = positive_annotations[pos_annotation].data.handles.points[0];
    //       positive_points[object_id].push(this.correct_xy_format(worldToIndex(pt)));
    //     // positive_points[object_id].push(this.correct_xy_format(worldToIndex(pt).map(Math.round)));
    //   }

    //   boxes[object_id] = [];
    //   for (const box_annotation in box_annotations) {
    //     const pt1 = box_annotations[box_annotation].data.handles.points[0];
    //     const pt2 = box_annotations[box_annotation].data.handles.points[3];
    //     boxes[object_id].push([
    //       this.correct_xy_format(worldToIndex(pt1).map(Math.round)),
    //       this.correct_xy_format(worldToIndex(pt2).map(Math.round)),
    //     ]);
    //   }
    //   object_ids.push(object_id);
    // }
    // // debuging
    // console.log('positive points: ', positive_points);
    // const points = positive_points[1];
    // const lastPoint = points?.[points.length - 1];  // safe access
    // console.log("Last point for object", 0, "is", lastPoint);
    // console.log('negative points: ', negative_points);
    // console.log('boxes: ', boxes);

    // =====================================================================
    const config = this.props.onOptionsConfig();
    const params = {
      ...(config && config.infer && config.infer[model]
        ? config.infer[model]
        : {}),
    };
    if (model === 'vista') {
      const prompts = this.getVistaPrompts(worldToIndex);
      params['Points'] = {
        points: prompts.map(({ point }) => point),
        labels: prompts.map(({ label }) => label),
      };
      params['Mode'] = 'interactive';
      params['Class'] = 133;
    } else if (model === 'sam') {
      params['sam2config'] = this.prompts;
      params['State'] = '3d';
    } else if (model === 'nninteractive') {
      // nnInteractive consumes the volume IJK coordinates directly.  The
      // previous implementation referenced disabled variables
      // (positive_points/negative_points), so no valid request could be sent.
      const prompts = this.getNnInteractivePrompts(worldToIndex);
      if (!prompts.length) {
        this.setState({
          statusMessage:
            'Add a positive or negative point before running inference.',
          statusType: 'error',
        });
        return;
      }
      params['Points'] = {
        points: prompts.map(({ point }) => point),
        labels: prompts.map(({ label }) => label),
        viewport: viewportInfo.viewportId,
      };
      params['resetInteractions'] = this.resetnnInteractiveAll;
    } else if (model === 'voxtell') {
      params['text_prompts'] = this.voxtellTextPrompt;
    } else {
      this.setState({
        statusMessage: 'This model does not support point inference yet.',
        statusType: 'error',
      });
      return;
    }

    let response;
    try {
      response = await this.props
        .client()
        .infer(model, displaySet.SeriesInstanceUID, params);
    } catch (error) {
      console.error(`Failed to run ${model} inference`, error);
      this.setState({
        statusMessage: 'Inference could not be completed. Please try again.',
        statusType: 'error',
      });
      return;
    }
    const labels = info.models[model].labels;
    // console.log(response);
    console.log('the parameters for model interactive run', params);

    if (response.status !== 200) {
      this.setState({
        statusMessage: 'Inference failed. Check the prompts and try again.',
        statusType: 'error',
      });
      return;
    }
    await this.props.updateView(response, labels, model);
    if (model === 'nninteractive') {
      this.resetnnInteractiveAll = false;
    }
    this.setState({ statusMessage: '', statusType: 'info' });

    this.notification.show({
      title: 'MONAI Label - ' + model,
      message: 'Interactive segmentation completed successfully.',
      type: 'success',
      duration: 4000,
    });
  };

  // initPoints = () => {
  //   const object = this.state.currentObject;
  //   if (!object) {
  //     console.log('Current Label is Null (No need to init)');
  //     return;
  //   }

  //   // const { toolGroupService, viewportGridService } =
  //   //   this.props.servicesManager.services;
  //   // const { viewports, activeViewportId } = viewportGridService.getState();
  //   // const viewport = viewports.get(activeViewportId);
  //   // const { viewportOptions } = viewport;
  //   // const toolGroupId = viewportOptions.toolGroupId;

  //   // const colorMap = this.segmentInfo();
  //   // const customColor = this.segColorToRgb(colorMap[label]);
  //   // toolGroupService.setToolConfiguration(toolGroupId, 'ProbeMONAILabel', {
  //   //   customColor: customColor,
  //   // });

  //   const annotations = this.state.clickPoints[object];
  //   if (annotations) {
  //     const manager = cornerstoneTools.annotation.state.getAnnotationManager();
  //     manager.restoreAnnotations(annotations);
  //   }
  // };
  onResetAllClick = () => {
    if (this.state.currentModel === 'nninteractive') {
      this.resetnnInteractiveAll = true;
    }
    this.prompts = {};
    this.AuxillaryBox = {};
    this.AuxillaryID = null;
    this.removeAllPromptAnnotations();
    this.setState({
      clickPoints: new Map(),
      flagNninteractive: { positive: false, negative: false },
      statusMessage: '',
    });
  };
  clearAllPoints = () => {
    this.removeAllPromptAnnotations();
    this.setState({
      clickPoints: new Map(),
      flagNninteractive: { positive: false, negative: false },
      activeTool: null,
    });
  };

  segColorToRgb(s) {
    const c = s ? s.color : [0, 0, 0];
    return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
  }

  onChangeObject = (id) => {
    const { currentObject } = this.state;
    console.log('the id is ', id, 'the current object is ', currentObject);
    if (id === currentObject) {
      console.log('Both new and prev are same');
      return;
    }

    // const prev = currentObject;
    // const clickPoints = this.state.clickPoints;
    // if (prev) {
    //   const manager = cornerstoneTools.annotation.state.getAnnotationManager();
    //   this.state.clickPoints[prev] = manager.saveAnnotations();
    //   console.log('Saving Prev annotations...', this.state.clickPoints[prev]);
    //   this.clearPoints();
    // }
    console.log('the id selected is ', id);
    this.setState({ currentObject: id });
    // this.initPoints();
  };

  onChangeLabel = (name) => {
    console.log(name, this.state.currentLabel);
    if (name === this.state.currentLabel) {
      console.log('Both new and prev are same');
      return;
    }

    const prev = this.state.currentLabel;
    const clickPoints = this.state.clickPoints;
    if (prev) {
      const manager = cornerstoneTools.annotation.state.getAnnotationManager();
      const annotations = manager.saveAnnotations(null, 'ProbeMONAILabelPos');
      console.log('Saving Prev annotations...', annotations);

      this.state.clickPoints[prev] = annotations;
      this.clearPoints();
    }

    this.state.currentLabel = name;
    this.setState({ currentLabel: name, clickPoints: clickPoints });
    // this.initPoints();
  };

  getModels() {
    const { info } = this.props;
    const allowed = ['vista', 'sam', 'nninteractive', 'voxtell'];
    console.log(info.models);
    const models = Object.keys(info.models).filter((key) =>
      allowed.includes(key)
    );
    return models;
  }

  getModelLabels(model) {
    const { info } = this.props;
    if (model && info.models[model].labels) {
      const entries = Object.entries(info.models[model].labels);
      const sorted = entries.sort((a, b) => a[1] - b[1]);
      return sorted.map(([label]) => label);
    }
    return info.labels;
  }

  getSelectedModel() {
    let selectedModel = 0;
    const models = this.getModels();
    for (const model of models) {
      if (!this.state.currentModel || model === this.state.currentModel) {
        break;
      }
      selectedModel++;
    }
    const model = models.length > 0 ? models[selectedModel] : null;
    // console.log('Selected Model: ', model);
    if (!model) {
      console.log('Something went error..');
      return null;
    }
    return model;
  }
  componentWillUnmount() {
    eventTarget.removeEventListener(
      cornerstoneTools.Enums.Events.ANNOTATION_COMPLETED,
      this.handleAnnotationCompleted
    );
    this.cleanupViewportListeners();
    this.resetProps();
    this.removeAllPromptAnnotations();
  }

  render() {
    const models = this.getModels();
    const display = models.length > 0 ? 'block' : 'none';
    // const segInfo = this.segmentInfo();
    return (
      <div className="tab" style={{ display: display }}>
        <input
          type="radio"
          name="rd"
          id={this.tabId}
          className="tab-switch"
          defaultValue="InteractiveSegmentation"
          onClick={this.onSelectActionTab}
        />
        <label htmlFor={this.tabId} className="tab-label">
          Interactive Segmentation
        </label>
        <div className="tab-content">
          <ModelSelector
            ref={this.modelSelector}
            name="InteractiveSegmentation"
            title="InteractiveSegmentation"
            models={models}
            currentModel={this.state.currentModel}
            onClick={this.onRunInference}
            onSelectModel={this.onSelectModel}
            statusMessage={this.state.statusMessage}
            statusType={this.state.statusType}
            isRunDisabled={
              (!this.state.hasPrompts && this.state.currentModel !== 'voxtell') ||
              (this.state.currentModel === 'voxtell' && !this.state.voxtellTextPrompt?.trim())
            }
            usage={
              <ModelBasedRendering
                mode={this.state.currentModel}
                activeTool={this.state.activeTool}
                positiveDisabled={
                  this.state.currentModel === 'nninteractive' &&
                  this.state.flagNninteractive.positive
                }
                negativeDisabled={
                  this.state.currentModel === 'nninteractive' &&
                  this.state.flagNninteractive.negative
                }
                onBoxClick={(id) => this.enableBoxTool(id)}
                onPositiveClick={(id) => this.enablePositiveProbe(id)}
                onNegativeClick={(id) => this.enableNegativeProbe(id)}
                onResetClick={this.undoLastPoint}
                onResetAllClick={this.onResetAllClick}
                onObjectIdChange={this.onObjectIdChange}
                onColorChange={this.onColorChange}
                onAddObjectClick={this.onAddObject}
                onRowSelect={this.onChangeObject}
                onTextPromptChange={(text) => {
                  this.voxtellTextPrompt = text;
                  this.setState({ voxtellTextPrompt: text });
                }}
              />
            }
          />
          <br />
          {new URLSearchParams(window.location.search).get('session_id') &&
            new URLSearchParams(window.location.search).get('embedded') !==
              '1' && (
              <button
                className="actionInput"
                style={{
                  backgroundColor: '#010839',
                  color: '#7bcfe9',
                  fontWeight: 'bold',
                  fontSize: '14px',
                  width: '100%',
                  padding: '8px 14px',
                  borderRadius: '6px',
                  border: '2px solid #010839',
                  cursor: 'pointer',
                  marginTop: '10px',
                  display: 'flex',
                  justifyContent: 'center',
                  alignItems: 'center',
                  transition: 'all 0.2s ease-in-out',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = 'rgb(72, 193, 230)';
                  e.currentTarget.style.color = 'rgb(255, 255, 255)';
                  e.currentTarget.style.borderColor = 'rgb(72, 193, 230)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = '#010839';
                  e.currentTarget.style.color = '#7bcfe9';
                  e.currentTarget.style.borderColor = '#010839';
                }}
                onClick={this.onClickSubmitToAgent}
                disabled={this.state.isSubmitting}
              >
                {this.state.isSubmitting
                  ? 'Submitting…'
                  : 'Submit Updated Mask'}
              </button>
            )}
        </div>
      </div>
    );
  }

  onClickSubmitToAgent = async () => {
    if (this.state.isSubmitting) {
      return;
    }

    this.setState({
      isSubmitting: true,
      statusMessage: '',
      statusType: 'info',
    });
    try {
      const urlParams = new URLSearchParams(window.location.search);
      const sessionId = urlParams.get('session_id');
      const seriesUid =
        urlParams.get('series_uid') ||
        this.props.viewConstants.SeriesInstanceUID;
      const targetId = urlParams.get('target_id');
      const pairId = urlParams.get('pair_id') || seriesUid;

      if (!sessionId) {
        this.setState({
          statusMessage: 'No session ID was found for this submission.',
          statusType: 'error',
        });
        return;
      }

      const { viewportGridService } = this.props.servicesManager.services;
      const { activeViewportId } = viewportGridService.getState();

      let labelVolume = null;

      // 1. Try to get it from cornerstoneTools segmentation state
      try {
        const segs = cornerstoneTools.segmentation.state.getSegmentations();
        if (segs && segs.length > 0) {
          const seg = segs[0];
          // In OHIF v3 / Cornerstone3D, volumeId is often under representationData
          const volId =
            (seg.representationData &&
              seg.representationData.LABELMAP &&
              seg.representationData.LABELMAP.volumeId) ||
            seg.volumeId;
          if (volId) {
            labelVolume = cache.getVolume(volId);
          }
        }
      } catch (err) {
        console.warn('Could not extract segmentation from state:', err);
      }

      // 2. Fallback to activeViewportId if it's a valid ID
      if (!labelVolume && activeViewportId) {
        try {
          labelVolume = cache.getVolume(activeViewportId);
        } catch (err) {
          console.debug('Active viewport is not backed by a label volume', err);
        }
      }

      // 3. Fallback to hardcoded '1' if it was loaded that way
      if (!labelVolume) {
        try {
          labelVolume = cache.getVolume('1');
        } catch (err) {
          console.debug('Fallback label volume was not available', err);
        }
      }

      if (!labelVolume) {
        this.setState({
          statusMessage: 'No labelmap is available to submit.',
          statusType: 'error',
        });
        return;
      }

      // If scalarData is backed by a SharedArrayBuffer, we must copy it to a normal ArrayBuffer
      // before constructing a Blob. We can do this by creating a new typed array of the same type.
      let safeData = labelVolume.scalarData;
      if (safeData.buffer && safeData.buffer instanceof SharedArrayBuffer) {
        // Create a copy of the typed array (e.g. Uint16Array or Float32Array)
        safeData = new safeData.constructor(safeData);
      }

      const labelBlob = new Blob([safeData], {
        type: 'application/octet-stream',
      });

      const formData = new FormData();
      formData.append('session_id', sessionId);
      formData.append('series_instance_uid', seriesUid);
      if (targetId) {
        formData.append('target_id', targetId);
      }
      formData.append('dimensions', JSON.stringify(labelVolume.dimensions));
      formData.append('file', labelBlob, 'label.nii.gz');

      const response = await fetch(
        `${getAgentsApiBaseUrl()}/segmentation-review/upload-mask`,
        {
          method: 'POST',
          body: formData,
        }
      );

      if (!response.ok) {
        throw new Error(`Upload failed: ${response.status}`);
      }

      const result = await response.json();

      this.notification.show({
        title: 'Agent System',
        message: 'Modified mask saved successfully.',
        type: 'success',
        duration: 3000,
      });

      const message = {
        type: SEGMENTATION_SUBMITTED_MESSAGE,
        version: 1,
        payload: {
          sessionId,
          seriesInstanceUid: seriesUid,
          pairId,
          targetId,
          status: 'saved',
          mask: {
            byteLength: safeData.byteLength,
            dimensions: labelVolume.dimensions,
          },
          overlay: result.overlay,
        },
      };

      if (window.parent !== window) {
        window.parent.postMessage(message, window.location.origin);
      } else if (window.opener && !window.opener.closed) {
        window.opener.postMessage(message, window.location.origin);
      }
    } catch (e) {
      console.error(e);
      this.setState({
        statusMessage: 'The modified mask could not be uploaded.',
        statusType: 'error',
      });
    } finally {
      this.setState({ isSubmitting: false });
    }
  };
}
