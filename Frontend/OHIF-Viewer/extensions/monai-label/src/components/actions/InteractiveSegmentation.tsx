import React, { Component } from 'react';
import ModelSelector from '../ModelSelector';
import BaseTab from './BaseTab';
import * as cornerstoneTools from '@cornerstonejs/tools';
import ModelBasedRendering from '../../tools/ModelBasedRendering';
import getActiveViewportEnabledElement from '../../../../cornerstone/src/utils/getActiveViewportEnabledElement';
import { getEnabledElement } from '@cornerstonejs/core';
import { getEnabledElement as OHIFgetEnabledElement } from '../../../../cornerstone/src/state';
import { getRenderingEngines, utilities as csUtils } from '@cornerstonejs/core';


export default class InteractiveSegmentation extends BaseTab {
  modelSelector: any;
  static instance = null;
  constructor(props) {
    super(props);
    // InteractiveSegmentation.instance = this;

    this.modelSelector = React.createRef();
    const models = this.getModels();
    this.state = {
      currentModel: models.length > 0 ? models[0] : null, // null brings so much errors
      currentObject: 1,
      clickPoints: new Map(),
      flagNninteractive: { positive: false, negative: false }

      // availableOrgans: models.length > 0 ? this.getModelLabels(models[0]) : {},
    };

    const { viewportGridService } = this.props.servicesManager.services;

    this.viewportGridService = viewportGridService;
    this.prompts = {};
    this.AuxillaryBox ={};
    this.AuxillaryID = null;
    this.sam2dstate = null;
    this.currentSlice = null;
    this.resetnnInteractiveAll = false;
    
  }
  // to remove active listner later
  activeListeners = [];




  initPromptObject = (objectId) => {
    if (!this.prompts[objectId]) {
      this.prompts[objectId] = {
        points: [[], []],  // [coords[], labels[]]
        boxes: []
      };
    }
  };
  getLastAnnotations = (worldToIndex) => {
    const manager = cornerstoneTools.annotation.state.getAnnotationManager();
    const saved = manager.saveAnnotations();
    console.log("iam inside get last annotations", saved);

    const frameOfReferenceUID = Object.keys(saved)[0];
    if (!frameOfReferenceUID) return null;

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
      console.log("the last positive point", pt);
    result.positive_points.push([
      ...this.correct_xy_format(worldToIndex(pt)).map((value, i) =>
        i === 2 ? Math.round(value) : value
      ),
      2]);
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
      2]);
    }

    // last box
    const boxKeys = Object.keys(box_annotations);
    if (boxKeys.length > 0) {
      const lastKey = boxKeys[boxKeys.length - 1];
      const pt1 = box_annotations[lastKey].data.handles.points[0];
      const pt2 = box_annotations[lastKey].data.handles.points[3];
      result.boxes.push([
        this.correct_xy_format(worldToIndex(pt1)).map((value, i) => i === 2 ? Math.round(value) : value),
        this.correct_xy_format(worldToIndex(pt2)).map((value, i) => i === 2 ? Math.round(value) : value),
      ]);
    }

    return result;
  };
  
  cleanupViewportListeners = () => {
    this.activeListeners.forEach(({ element, type, handler }) => {
      element.removeEventListener(type, handler);
    });
    this.activeListeners = [];
    console.log("cleaned and removed old viewport listeners");
  };

  handleUpdatePrompts = (mode)=>{
    //todo: instead of timeout , use event listner as the cornerstone creation take time not reliable ! 
    setTimeout(() => {
    const { cornerstoneViewportService } = this.props.servicesManager.services;
    const { viewport, displaySet } = this.props.getActiveViewportInfo();
    const viewportInfo = cornerstoneViewportService.getViewportInfo(viewport.viewportId);
    const currentViewPort = viewportInfo.viewportId
    const { worldToIndex } = viewportInfo.viewportData.data[0].volume.imageData;
    const { currentModel, currentObject, clickPoints } = this.state;
    console.log("the current object is ", currentObject);
    const result = this.getLastAnnotations(worldToIndex);
    console.log("the results getted from the function is ", result);
    this.initPromptObject(currentObject);

      if ((mode === 'positive' || mode === 'negative') && currentViewPort !== 'mpr-axial') {
      const nid = this.notification.show({
        title: 'MONAI Label',
        message: 'Positive/Negative point prompts are supported only in Axial view. Please switch to axial.',
        type: 'warning',
        duration: 8000,
      });
      return; // stop execution, don’t call sam2d
    }

    if (mode === 'positive' && result?.positive_points.length > 0) {
      const pt = result.positive_points[0];
      console.log("Positive point added", pt);

      this.prompts[currentObject].points[0].push(pt);  // coords
      this.prompts[currentObject].points[1].push(1);   // label
      this.sam2dstate = '2d';
      this.currentSlice = pt[2];
      console.log("the current slice is ", this.currentSlice);
    }

    if (mode === 'negative' && result?.negative_points.length > 0) {
      const pt = result.negative_points[0];
      console.log("Negative point added", pt);

      this.prompts[currentObject].points[0].push(pt);  // coords
      this.prompts[currentObject].points[1].push(0);   // label
      this.sam2dstate = '2d';
      this.currentSlice = pt[2];
      
    }
    if (mode === 'box' && result?.boxes.length > 0) {
      const [[x1, y1, z1], [x2, y2, z2]] = result.boxes[0];

      console.log("the z1 is", z1, "the z2 is", z2);
      console.log("the x1", x1, "the x2 is", x2, "the y1", y1, "the y2", y2);

      if (currentViewPort === 'mpr-axial') {
        const xmin = Math.min(x1, x2);
        const xmax = Math.max(x1, x2);
        const ymin = Math.min(y1, y2);
        const ymax = Math.max(y1, y2);
        const slice_index = z1;
        this.currentSlice = slice_index;

        const boxEntry = [[xmin, ymin, xmax, ymax], slice_index, 2];
        console.log("the boxentry is ", boxEntry);
        this.prompts[currentObject].boxes.push(boxEntry);
        this.sam2dstate = '2d';
      } 
      else {
        let xmin, xmax, ymin, ymax, slice_index, planeIndex;

        if (currentViewPort === 'mpr-sagittal') {
          xmin = Math.min(z1, z2);
          xmax = Math.max(z1, z2);
          ymin = Math.min(y1, y2);
          ymax = Math.max(y1, y2);
          slice_index = x1;
          planeIndex = 0;
        } else { // coronal
          xmin = Math.min(z1, z2);
          xmax = Math.max(z1, z2);
          ymin = Math.min(x1, x2);
          ymax = Math.max(x1, x2);
          slice_index = y1+1;
          planeIndex = 1;
        }

        this.currentSlice = slice_index;
        const boxEntry = [[xmin, ymin, xmax, ymax], slice_index, planeIndex];

        console.log("the box entry for Aux ", boxEntry);
        this.AuxillaryBox = boxEntry;
        this.sam2dstate = 'Auxillary_Box';
        this.AuxillaryID = currentObject;
      }
    }
    console.log("the prompts is ,", this.prompts);
    this.onsam2d(this.sam2dstate);
  }, 500); 
 
  }

  configureViewportInteraction = (mode: 'positive' | 'negative') => {
    if (!this.viewportGridService) return;

    this.cleanupViewportListeners(); // remove old listeners

    const viewports = this.viewportGridService.getState().viewports;
    const renderingEngines = getRenderingEngines();

    const handleClick = (evt) => {
      console.log("point clicked in mode", mode);

      if (this.state.currentModel === 'sam') {
        this.handleUpdatePrompts(mode);
      } else if (this.state.currentModel === 'nninteractive') {

        if (mode === 'positive' && !this.state.flagNninteractive.positive) {
          const { toolGroupService, viewportGridService } = this.props.servicesManager.services;
          const { viewports, activeViewportId } = viewportGridService.getState();
          const viewport = viewports.get(activeViewportId);
          const { viewportOptions } = viewport;
          const toolGroupId = viewportOptions.toolGroupId;

          // Use setToolPassive instead of setToolDisabled

          
          this.setState(prevState => ({
            flagNninteractive: {
              ...prevState.flagNninteractive,
              positive: true
            }
          }));
            setTimeout(() => {toolGroupService
            .getToolGroup(toolGroupId)
            .setToolPassive('ProbeMONAILabelPos');},500);
          
        } else if (mode === 'negative' && !this.state.flagNninteractive.negative) {
          const { toolGroupService, viewportGridService } = this.props.servicesManager.services;
          const { viewports, activeViewportId } = viewportGridService.getState();
          const viewport = viewports.get(activeViewportId);
          const { viewportOptions } = viewport;
          const toolGroupId = viewportOptions.toolGroupId;


          
          this.setState(prevState => ({
            flagNninteractive: {
              ...prevState.flagNninteractive,
              negative: true
            }
          }));
            setTimeout(() => {toolGroupService
            .getToolGroup(toolGroupId)
            .setToolPassive('ProbeMONAILabelNeg');},500);
        }
      }
    };


    viewports.forEach((vpConfig) => {
      const viewport = renderingEngines[0].getViewport(vpConfig.viewportId);
      if (!viewport?.element) return;

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
    if (!this.viewportGridService) return;

    this.cleanupViewportListeners(); 

    const viewports = this.viewportGridService.getState().viewports;
    const renderingEngines = getRenderingEngines();

    const holdBoxStartMap = new Map();
    const clickBoxStartMap = new Map();
    // apply threshold to prevent detection twice of the box
    const movementThreshold = 5;  // px to detect drag 
    const clickDistanceThreshold = 20; // px distance for 2-click mode 

    // option one for press hold and release
    const handleDown = (evt) => {
      holdBoxStartMap.set(evt.currentTarget, { 
        x: evt.offsetX, 
        y: evt.offsetY, 
        moved: false 
      });
    };

    const handleMove = (evt) => {
      if (holdBoxStartMap.has(evt.currentTarget)) {
        const start = holdBoxStartMap.get(evt.currentTarget);
        const dx = evt.offsetX - start.x;
        const dy = evt.offsetY - start.y;
        if (Math.abs(dx) > movementThreshold || Math.abs(dy) > movementThreshold) {
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
        if (this.state.currentModel ==='sam'){
        this.handleUpdatePrompts('box');}      
      }
        holdBoxStartMap.delete(evt.currentTarget);
      }



    };

    // option two for two clicks
    const handleClick = (evt) => {
      const element = evt.currentTarget;

      if (!clickBoxStartMap.has(element)) {
      
        clickBoxStartMap.set(element, { x: evt.offsetX, y: evt.offsetY });
        console.log("Click Box start", evt.offsetX, evt.offsetY);
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
        if (this.state.currentModel ==='sam'){
        this.handleUpdatePrompts('box');}
        } else {
          console.log("not a complete box ");
        }

        clickBoxStartMap.delete(element);
      }
    };

    viewports.forEach((vpConfig) => {
      const viewport = renderingEngines[0].getViewport(vpConfig.viewportId);
      if (!viewport?.element) return;

      viewport.element.addEventListener("mousedown", handleDown);
      viewport.element.addEventListener("mousemove", handleMove);
      viewport.element.addEventListener("mouseup", handleUp);
      viewport.element.addEventListener("click", handleClick);

      this.activeListeners.push({ element: viewport.element, type: "mousedown", handler: handleDown });
      this.activeListeners.push({ element: viewport.element, type: "mousemove", handler: handleMove });
      this.activeListeners.push({ element: viewport.element, type: "mouseup", handler: handleUp });
      this.activeListeners.push({ element: viewport.element, type: "click", handler: handleClick });
    });
  };

  resetProps = ()=>{
    this.prompts = {};
    this.AuxillaryBox ={};
    this.AuxillaryID = null;
    this.sam2dstate = null;
    this.currentSlice = null;
    this.resetnnInteractiveAll = false;
    this.voxtellTextPrompt = '';
  }

  onSelectModel = (model) => {
    // console.log('Selecting  (Point) Interaction Model...');
    const currentObject = 1;
    const clickPoints = new Map();
    this.setState({
      currentModel: model,
      currentObject: currentObject,
      clickPoints: clickPoints,
      // availableOrgans: this.getModelLabels(model),
      flagNninteractive: { positive: false, negative: false }

    });
    this.resetProps();
    this.clearAllPoints();
  };

  onEnterActionTab = () => {
    console.log(this.props.commandsManager);
    this.props.commandsManager.runCommand('setToolActive', {
      toolName: 'ProbeMONAILabelNeg',
    });
    this.props.commandsManager.runCommand('setToolActive', {
      toolName: 'RecROIToolMONAILabel',
    });
    this.props.commandsManager.runCommand('setToolActive', {
      toolName: 'ProbeMONAILabelPos',
    });
    console.info('Here we activate the probe');

   
  };

  onLeaveActionTab = () => {
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
      this.configureViewportInteraction('positive');

  };

  enableNegativeProbe = (objectId) => {
    console.log('Enable Negative Probe for Object', objectId);
    this.props.commandsManager.runCommand('setToolActive', {
      toolName: 'ProbeMONAILabelNeg',
    });
      this.configureViewportInteraction('negative');

  };

  enableBoxTool = (objectId) => {
    console.log('Enable Box Tool for Object', objectId);
    this.props.commandsManager.runCommand('setToolActive', {
      toolName: 'RecROIToolMONAILabel',
    });
      this.configureViewportBoxInteraction();

  };
  clearPoints = (objectId) => {
    console.log('Clearing points for Object', objectId);
    cornerstoneTools.annotation.state
      .getAnnotationManager()
      .removeAllAnnotations();
    this.props.servicesManager.services.cornerstoneViewportService
      .getRenderingEngine()
      .render();
      this.setState({ flagNninteractive: { positive: false, negative: false } });

  };
  onsam2d = async(state) =>{
    console.log("iam inside onsame2d function");
    const nid = this.notification.show({
      title: 'MONAI Label',
      message: 'Running 2d SAM Segmentation... For 3d segmentation press Propagate button',
      type: 'info',
      duration: 10000,
    });
    const { info} = this.props;
    const { viewport, displaySet } = this.props.getActiveViewportInfo();
    const config = this.props.onOptionsConfig();
    const params =
      config && config.infer && config.infer[model] ? config.infer[model] : {};
    params['restore_label_idx'] = false;
    params['result_compress']= false;
    params['result_dtype'] = 'uint16';
    params['result_extension'] = '.nrrd';
    params['sam2config'] = this.prompts;
    params['SliceIndex'] = this.currentSlice;
    if (state ==='2d'){
      params['State'] = "2d";
    }
    else if (state === 'Auxillary_Box'){
      params['Auxillary_Box'] = this.AuxillaryBox;
      params['Auxillary_Id'] = this.AuxillaryID;
      params['State'] = "Auxillary_Box";
    }
    console.log("the parameters are ",params);

    
    const response = await this.props
      .client()
      .infer('sam', displaySet.SeriesInstanceUID, params);
    const labels = info.models['sam'].labels;
    console.log(response);

    if (!nid) {
      window.snackbar.hideAll();
    } else {
      this.notification.hide(nid);
    }

    if (response.status !== 200) {
      this.notification.show({
        title: 'MONAI Label',
        message: 'Failed to Run Segmentation',
        type: 'error',
        duration: 5000,
      });
    } else {
      this.notification.show({
        title: 'MONAI Label',
        message: 'Run SAM Segmentation Successfully',
        type: 'success',
        duration: 10000,
      });
    }

    this.props.updateView(response, labels, 'sam');
  }

  correct_xy_format = (point) => {
    return [point[1], point[0], point[2]];
  }

  onRunInference = async () => {
    console.log(' run inference start');
    const { currentModel, currentObject, clickPoints, flagNninteractive } = this.state;
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
      this.notification.show({
        title: 'MONAI Label',
        message: 'Something went wrong: Model is not selected',
        type: 'error',
        duration: 10000,
      });
      return;
    }

    const nid = this.notification.show({
      title: 'MONAI Label - ' + model,
      message: 'Running Interactive Inference...',
      type: 'info',
      duration: 4000,
    });

    const { cornerstoneViewportService } = this.props.servicesManager.services;
    const viewportInfo = cornerstoneViewportService.getViewportInfo(
      viewport.viewportId
    );
    const { worldToIndex } = viewportInfo.viewportData.data[0].volume.imageData;

    const manager = cornerstoneTools.annotation.state.getAnnotationManager();
    clickPoints[currentObject] = manager.saveAnnotations();

    const positive_points = {};
    const negative_points = {};
    const boxes = {};
    const object_ids = [];
    const framOfReferenceUID = Object.keys(clickPoints[currentObject])[0]; 
    for (const object_id in clickPoints) {
      const negative_annotations =
        clickPoints[object_id][framOfReferenceUID]['ProbeMONAILabelNeg'];
      const positive_annotations =
        clickPoints[object_id][framOfReferenceUID]['ProbeMONAILabelPos'];
      const box_annotations =
        clickPoints[object_id][framOfReferenceUID]['RecROIToolMONAILabel'];
      negative_points[object_id] = [];
      for (const neg_annotation in negative_annotations) {
        console.log(neg_annotation);
        const pt = negative_annotations[neg_annotation].data.handles.points[0];
        negative_points[object_id].push(this.correct_xy_format(worldToIndex(pt)));
        // negative_points[object_id].push(this.correct_xy_format(worldToIndex(pt).map(Math.round)));
      }

      positive_points[object_id] = [];
      for (const pos_annotation in positive_annotations) {
        const pt = positive_annotations[pos_annotation].data.handles.points[0];
          positive_points[object_id].push(this.correct_xy_format(worldToIndex(pt)));
        // positive_points[object_id].push(this.correct_xy_format(worldToIndex(pt).map(Math.round)));
      }

      boxes[object_id] = [];
      for (const box_annotation in box_annotations) {
        const pt1 = box_annotations[box_annotation].data.handles.points[0];
        const pt2 = box_annotations[box_annotation].data.handles.points[3];
        boxes[object_id].push([
          this.correct_xy_format(worldToIndex(pt1).map(Math.round)),
          this.correct_xy_format(worldToIndex(pt2).map(Math.round)),
        ]);
      }
      object_ids.push(object_id);
    }
    // debuging 
    console.log('positive points: ', positive_points);
    const points = positive_points[1];
    const lastPoint = points?.[points.length - 1];  // safe access
    console.log("Last point for object", 0, "is", lastPoint);
    console.log('negative points: ', negative_points);
    console.log('boxes: ', boxes);

    const config = this.props.onOptionsConfig();
    const params =
      config && config.infer && config.infer[model] ? config.infer[model] : {};
    if (model === 'vista') {
      params['Points'] = {
        points: [
          ...(positive_points[currentObject] || []),
          ...(negative_points[currentObject] || []),
        ].map(([x, y, z]) => [x, y, Math.round(z)]), 
        labels: [
          ...(positive_points[currentObject]
            ? new Array(positive_points[currentObject].length).fill(1)
            : []),
          ...(negative_points[currentObject]
            ? new Array(negative_points[currentObject].length).fill(0)
            : []),
        ],
      };
      params['Mode'] = 'interactive';
      params['Class'] = 133;
    } else if (model === 'sam') {
      params['sam2config'] = this.prompts;
      params['State'] = "3d";

    } else if (model === 'nninteractive') {
      const allPoints = [
        ...(positive_points[currentObject] || []),
        ...(negative_points[currentObject] || []),
      ];
      const swappedPoints = allPoints.map(([x, y, z]) => new Float32Array([y, x, z]));
      params['viewPort'] = viewportInfo.viewportId;
      params['Points'] = {
        points:swappedPoints ,
        labels: [
          ...(positive_points[currentObject]
            ? new Array(positive_points[currentObject].length).fill(1)
            : []),
          ...(negative_points[currentObject]
            ? new Array(negative_points[currentObject].length).fill(0)
            : []),
        ],
        viewport: viewportInfo.viewportId,
      };
      params['resetInteractions'] = this.resetnnInteractiveAll;
    } else if (model === 'voxtell') {
      params['text_prompts'] = this.voxtellTextPrompt;
    } else {
      this.notification.show({
        title: 'MONAI Label - ' + model,
        message: 'Model not yet supported for Point Inference',
        type: 'error',
        duration: 6000,
      });
      // hideNotification(nid, this.notification);
      return;
    }

    const response = await this.props
      .client()
      .infer(model, displaySet.SeriesInstanceUID, params);
    const labels = info.models[model].labels;
    // console.log(response);
    console.log("the parameters for model interactive run", params);


    if (response.status !== 200) {
      this.notification.show({
        title: 'MONAI Label - ' + model,
        message: 'Failed to Run Inference for Point Prompts',
        type: 'error',
        duration: 6000,
      });
      return;
    }
    this.props.updateView(response, labels, model);

    this.notification.show({
      title: 'MONAI Label - ' + model,
      message: 'Running Inference for Interactive Segmentation - Successful',
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
  onRestAllClick =()=>{
      this.resetnnInteractiveAll = !this.resetnnInteractiveAll; 
  }
  clearAllPoints = () => {
    const clickPoints = new Map();
    this.setState({ clickPoints: clickPoints });
    this.clearPoints();
  };

  segColorToRgb(s) {
    const c = s ? s.color : [0, 0, 0];
    return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
  }

  onChangeObject = (id) => {
    const { currentObject } = this.state;
    console.log("the id is ", id, "the current object is ", currentObject);
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
    console.log("the id selected is ", id);
    this.setState({ currentObject: id});
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
    const allowed = ['vista', 'sam', 'nninteractive','voxtell'];
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
    this.cleanupViewportListeners();
    this.resetProps();
    // this.nLeaveActionTab();
    const manager = cornerstoneTools.annotation.state.getAnnotationManager();
    manager.removeAllAnnotations();
    const { cornerstoneViewportService } = this.props.servicesManager.services;
    cornerstoneViewportService.getRenderingEngine().render();
    console.log("the annotations removed");
    }



  render() {
    const models = this.getModels();
    const display = models.length > 0 ? 'block' : 'none';
    // const segInfo = this.segmentInfo();
    const labels = this.getModelLabels(this.getSelectedModel());

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
            usage={
              <ModelBasedRendering
                mode={this.state.currentModel}
                positiveDisabled={this.state.currentModel === 'nninteractive' && this.state.flagNninteractive.positive}
                negativeDisabled={this.state.currentModel === 'nninteractive' && this.state.flagNninteractive.negative}
                onBoxClick={(id) => this.enableBoxTool(id)}
                onPositiveClick={(id) => this.enablePositiveProbe(id)}
                onNegativeClick={(id) => this.enableNegativeProbe(id)}
                onResetClick={(id) => this.clearPoints(id)}
                onResetAllClick={this.onRestAllClick}
                onObjectIdChange={this.onObjectIdChange}
                onColorChange={this.onColorChange}
                onAddObjectClick={this.onAddObject}
                onRowSelect={this.onChangeObject}
                onTextPromptChange={(text) => this.voxtellTextPrompt = text}
              />
            }
          />
        </div>
      </div>
    );
  }
}