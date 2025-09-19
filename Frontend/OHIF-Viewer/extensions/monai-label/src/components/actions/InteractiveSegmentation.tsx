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

  constructor(props) {
    super(props);

    this.modelSelector = React.createRef();
    const models = this.getModels();
    this.state = {
      currentModel: models.length > 0 ? models[0] : null, // null brings so much errors
      currentObject: null,
      clickPoints: new Map(),
      samMode: '2d', // or 3d
      // availableOrgans: models.length > 0 ? this.getModelLabels(models[0]) : {},
    };

    const { viewportGridService } = this.props.servicesManager.services;

    this.viewportGridService = viewportGridService;
  }
  // to remove active listner later
  activeListeners = [];

  
  cleanupViewportListeners = () => {
    this.activeListeners.forEach(({ element, type, handler }) => {
      element.removeEventListener(type, handler);
    });
    this.activeListeners = [];
    console.log("cleaned and removed old viewport listeners");
  };

  configureViewportInteraction = (mode: 'positive' | 'negative') => {
    if (!this.viewportGridService) return;

    this.cleanupViewportListeners(); // remove old listeners

    const viewports = this.viewportGridService.getState().viewports;
    const renderingEngines = getRenderingEngines();


    const handleClick = (evt) => {
      if (mode === 'positive') {
        console.log("Positive point clicked", evt);
      } else if (mode === 'negative') {
        console.log(" Negative point clicked", evt);    // here we can add the call for runinference for state 2d 
      }
    };

    viewports.forEach((vpConfig) => {
      const viewport = renderingEngines[0].getViewport(vpConfig.viewportId);
      if (!viewport?.element) return;

      viewport.element.addEventListener('click', handleClick);
      this.activeListeners.push({ element: viewport.element, type: 'click', handler: handleClick });
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



  onSelectModel = (model) => {
    // console.log('Selecting  (Point) Interaction Model...');
    const currentObject = null;
    const clickPoints = new Map();
    this.setState({
      currentModel: model,
      currentObject: currentObject,
      clickPoints: clickPoints,
      // availableOrgans: this.getModelLabels(model),
    });

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
  };

  correct_xy_format = (point) => {
    return [point[1], point[0], point[2]];
  }

  onRunInference = async () => {
    console.log(' run inference start');
    const { currentModel, currentObject, clickPoints } = this.state;
    const { info } = this.props;
    const { viewport, displaySet } = this.props.getActiveViewportInfo();
    
    console.log("active view port is", viewport);

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
    const framOfReferenceUID = Object.keys(clickPoints[currentObject])[0]; //might cause problems in the future
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
        negative_points[object_id].push(this.correct_xy_format(worldToIndex(pt).map(Math.round)));
      }

      positive_points[object_id] = [];
      for (const pos_annotation in positive_annotations) {
        const pt = positive_annotations[pos_annotation].data.handles.points[0];
        positive_points[object_id].push(this.correct_xy_format(worldToIndex(pt).map(Math.round)));
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

    console.log('positive points: ', positive_points);
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
        ],
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
      params['State'] = this.state.samMode;
      // 2d or 3d
      if (this.state.samMode === '3d') {
        // no more params
      } else {
        params['sam2config'] = {};
        for (const obj of object_ids) {
          const posPts = positive_points[obj] || [];
          const negPts = negative_points[obj] || [];
          const boxPts = boxes[obj] || [];

          const coords = [...posPts, ...negPts];
          const labels = [
            ...new Array(posPts.length).fill(1),
            ...new Array(negPts.length).fill(0),
          ];

          const bboxes = boxPts.map((box) => {
            const xmin = Math.min(box[0][0], box[1][0]);
            const ymin = Math.min(box[0][1], box[1][1]);
            const xmax = Math.max(box[0][0], box[1][0]);
            const ymax = Math.max(box[0][1], box[1][1]);
            const slice = box[0][2];
            return [[xmin, ymin, xmax, ymax], slice];
          });
          params['sam2config'][obj] = {
            points: [coords, labels],
            boxes: bboxes,
          };
        }
      }
    } else if (model === 'nninteractive') {
      // last point added only
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

    // hideNotification(nid, this.notification);
    if (response.status !== 200) {
      this.notification.show({
        title: 'MONAI Label - ' + model,
        message: 'Failed to Run Inference for Point Prompts',
        type: 'error',
        duration: 6000,
      });
      return;
    }

    this.notification.show({
      title: 'MONAI Label - ' + model,
      message: 'Running Inference for Point Prompts - Successful',
      type: 'success',
      duration: 4000,
    });

    this.props.updateView(response, labels, model);
  };

  initPoints = () => {
    const object = this.state.currentObject;
    if (!object) {
      console.log('Current Label is Null (No need to init)');
      return;
    }

    // const { toolGroupService, viewportGridService } =
    //   this.props.servicesManager.services;
    // const { viewports, activeViewportId } = viewportGridService.getState();
    // const viewport = viewports.get(activeViewportId);
    // const { viewportOptions } = viewport;
    // const toolGroupId = viewportOptions.toolGroupId;

    // const colorMap = this.segmentInfo();
    // const customColor = this.segColorToRgb(colorMap[label]);
    // toolGroupService.setToolConfiguration(toolGroupId, 'ProbeMONAILabel', {
    //   customColor: customColor,
    // });

    const annotations = this.state.clickPoints[object];
    if (annotations) {
      const manager = cornerstoneTools.annotation.state.getAnnotationManager();
      manager.restoreAnnotations(annotations);
    }
  };

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
    if (id === currentObject) {
      console.log('Both new and prev are same');
      return;
    }

    const prev = currentObject;
    const clickPoints = this.state.clickPoints;
    if (prev) {
      const manager = cornerstoneTools.annotation.state.getAnnotationManager();
      this.state.clickPoints[prev] = manager.saveAnnotations();
      console.log('Saving Prev annotations...', this.state.clickPoints[prev]);
      this.clearPoints();
    }
    this.state.currentObject = id;
    this.setState({ currentObject: id, clickPoints: clickPoints });
    this.initPoints();
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
    this.initPoints();
  };

  getModels() {
    const { info } = this.props;
    const allowed = ['vista', 'sam', 'nninteractive'];
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
                onBoxClick={(id) => this.enableBoxTool(id)}
                onPositiveClick={(id) => this.enablePositiveProbe(id)}
                onNegativeClick={(id) => this.enableNegativeProbe(id)}
                onResetClick={(id) => this.clearPoints(id)}
                onResetAllClick={this.clearAllPoints}
                onObjectIdChange={this.onObjectIdChange}
                onColorChange={this.onColorChange}
                onAddObjectClick={this.onAddObject}
                onRowSelect={this.onChangeObject}
              />
            }
          />
        </div>
      </div>
    );
  }
}
