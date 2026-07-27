import React, { Component } from 'react';
import PropTypes, { object } from 'prop-types';
import { cache, triggerEvent, eventTarget } from '@cornerstonejs/core';
import { Enums, segmentation } from '@cornerstonejs/tools';
import { Icon, Typography, LegacyButton } from '@ohif/ui';
import './MonaiLabelPanel.css';
import SettingsTable from './SettingsTable';
import AutoSegmentation from './actions/AutoSegmentation';
import InteractiveSegmentation from './actions/InteractiveSegmentation';
import SmartEdit from './actions/SmartEdit';
import OptionTable from './actions/OptionTable';
import ActiveLearning from './actions/ActiveLearning';
import MonaiLabelClient from '../services/MonaiLabelClient';
import SegmentationReader from '../utils/SegmentationReader';
import MonaiSegmentation from './MonaiSegmentation';
import SegmentationToolbox from './SegmentationToolbox';
import AgentChat from './actions/AgentChat';

import { createReportAsync } from '@ohif/extension-default';
import { servicesManager } from 'platform/app/src/App';

export default class MonaiLabelPanel extends Component {
  static propTypes = {
    commandsManager: PropTypes.any,
    servicesManager: PropTypes.any,
    extensionManager: PropTypes.any,
  };

  notification: any;
  settings: any;
  state: { info: {}; action: {} };
  actions: {
    options: any;
    activelearning: any;
    segmentation: any;
    smartedit: any;
    InteractiveSegmentation: any;
  };
  props: any;
  SeriesInstanceUID: any;
  StudyInstanceUID: any;
  isMONAILabelConnected: boolean;
  Modality: any;

  constructor(props) {
    super(props);

    const { uiNotificationService, viewportGridService, displaySetService } =
      props.servicesManager.services;

    this.notification = uiNotificationService;
    this.settings = React.createRef();

    this.actions = {
      options: React.createRef(),
      activeLearning: React.createRef(),
      segmentation: React.createRef(),
      smartedit: React.createRef(),
      InteractiveSegmentation: React.createRef(),
    };

    this.state = {
      info: {},
      action: {},
      segmentations: [],
      isDataReady: false,
    };
    this.workflowName = null;
    /////////////////////////////NEW CODE/////////////////////////////////////
    const params = new URLSearchParams(window.location.search);
    const paramsObject = {};
    for (const [key, value] of params.entries()) {
      paramsObject[key] = value;
    }

    if (paramsObject.workflowName) {
      this.workflowName = paramsObject.workflowName;
    }

    // Todo: fix this hack
    setTimeout(() => {
      const { viewports, activeViewportId } = viewportGridService.getState();
      const viewport = viewports.get(activeViewportId);
      const displaySet = displaySetService.getDisplaySetByUID(
        viewport.displaySetInstanceUIDs[0]
      );

      this.SeriesInstanceUID = displaySet.SeriesInstanceUID;
      this.StudyInstanceUID = displaySet.StudyInstanceUID;
      this.Modality = displaySet.Modality;
      this.FrameOfReferenceUID = displaySet.instances[0].FrameOfReferenceUID;
      this.displaySetInstanceUID = displaySet.displaySetInstanceUID;
      this.onInfo();
    }, 3000);
  }

  async componentDidMount() {
    const { segmentationService } = this.props.servicesManager.services;
    const added = segmentationService.EVENTS.SEGMENTATION_ADDED;
    const updated = segmentationService.EVENTS.SEGMENTATION_UPDATED;
    const removed = segmentationService.EVENTS.SEGMENTATION_REMOVED;
    const subscriptions = [];

    [added, updated, removed].forEach((evt) => {
      const { unsubscribe } = segmentationService.subscribe(evt, (data) => {
        const segmentations = segmentationService.getSegmentations();

        if (!segmentations?.length) {
          return;
        }

        this.setState({ segmentations });
      });
      subscriptions.push(unsubscribe);
    });

    this.unsubscribe = () => {
      subscriptions.forEach((unsubscribe) => unsubscribe());
    };
  }

  // componentDidUnmount? Doesn't exist this method anymore in V3?
  async componentWillUnmount() {
    this.unsubscribe();
  }

  client = () => {
    const settings =
      this.settings && this.settings.current && this.settings.current.state
        ? this.settings.current.state
        : null;
    return new MonaiLabelClient(
      settings ? settings.url : 'http://127.0.0.1:8000'
    );
  };

  onInfo = async () => {
    this.notification.show({
      title: 'Workflow Server',
      message: 'Connecting to Workflow Server',
      type: 'info',
      duration: 3000,
    });
    console.log('on info function used');
    const response = await this.client().info();

    this.isMONAILabelConnected = true;

    // remove the background
    const labels = response.data.labels.splice(1);

    const { displaySet } = this.getActiveViewportInfo();
    const segmentationId = displaySet.SeriesInstanceUID;

    const segmentations = [
      {
        id: segmentationId, // use same ID everywhere
        label: 'Segmentations',
        segments: labels.map((label, index) => ({
          segmentIndex: index + 1,
          label,
        })),
        isActive: true,
        activeSegmentIndex: 1,
        // scalarData: null,
        // FrameOfReferenceUID: displaySet.instances[0].FrameOfReferenceUID,
        // centroidsIJK: centroidsIJK,
      },
    ];

    this.props.commandsManager.runCommand('loadSegmentationsForViewport', {
      segmentations,
    });

    if (response.status !== 200) {
      this.notification.show({
        title: 'Workflow Server',
        message: 'Failed to Connect to Workflow Server',
        type: 'error',
        duration: 5000,
      });
    } else {
      this.notification.show({
        title: 'Workflow Server',
        message: 'Connected to Workflow Server - Successful',
        type: 'success',
        duration: 2000,
      });

      this.setState({ info: response.data });
      this.setState({ isDataReady: true });

      // calc image embedding(if SAM model)
      // AutoSegmentation.calculateImageEmbeddings(true);
    }
  };

  onSelectActionTab = (name) => {
    console.log('Selecting Action Tab...');
    console.log(name);
    console.log(Object.keys(this.actions));
    // Leave Event
    for (const action of Object.keys(this.actions)) {
      if (this.state.action === action) {
        if (this.actions[action].current) {
          this.actions[action].current.onLeaveActionTab();
        }
      }
    }

    // Enter Event
    for (const action of Object.keys(this.actions)) {
      if (name === action) {
        if (this.actions[action].current) {
          this.actions[action].current.onEnterActionTab();
        }
      }
    }
    this.setState({ action: name });
  };

  onOptionsConfig = () => {
    return this.actions['options'].current &&
      this.actions['options'].current.state
      ? this.actions['options'].current.state.config
      : {};
  };

  _update = async (response, labelNames) => {
    // Update instance variables
    this.updateInstanceVariables();
    const client_response = await this.client().info();
    const labels = client_response.data.labels.slice(1);
    console.log(labels);
    const onInfoLabelNames = labelNames;
    console.info('These are the predicted labels:', onInfoLabelNames);

    // Parse NRRD segmentation data
    const ret = SegmentationReader.parseNrrdData(response.data);
    if (!ret) {
      throw new Error('Failed to parse NRRD data');
    }

    const { image: buffer, header } = ret;
    const data = new Uint16Array(buffer);

    // Reformat centroids
    const centroidsIJK = new Map();
    for (const [key, value] of Object.entries(response.centroids)) {
      const segmentIndex = parseInt(value[0], 10);
      const image = value.slice(1).map((v) => parseFloat(v));
      centroidsIJK.set(segmentIndex, { image: image, world: [] });
    }

    // Get the active display set (each series = unique segmentation ID)
    const { displaySet } = this.getActiveViewportInfo();
    const segmentationId = displaySet.SeriesInstanceUID;

    // Define segmentation object with proper structure
    const segmentation_object = {
      id: segmentationId,
      label: `Segmentation_${segmentationId}`,
      segments: labels.map((label, index) => ({
        segmentIndex: index + 1,
        label,
        isActive: true,
        isVisible: true,
      })),
      isActive: true,
      activeSegmentIndex: 1,
      scalarData: data,
      FrameOfReferenceUID: this.FrameOfReferenceUID,
      centroidsIJK: centroidsIJK,
    };

    console.log(
      'BEFORE loading - segmentation_object.segments:',
      segmentation_object.segments
    );
    console.log(
      'BEFORE loading - segments length:',
      segmentation_object.segments.length
    );
    console.log(
      'BEFORE loading - first 5 segments:',
      segmentation_object.segments.slice(0, 5)
    );

    // Try to find existing volume in cache
    const volumeLoadObject = await cache.getVolumeLoadObject(segmentationId);
    console.log('_update: the volume load object', volumeLoadObject);

    if (volumeLoadObject) {
      // Volume exists - update it
      const volume = await volumeLoadObject.promise;
      console.log('Resolved volume:', volume);
      console.log(
        `_update: found existing volume for segmentationId ${segmentationId}`
      );
      const { scalarData } = volume;
      console.log('data = ', data);
      scalarData.set(data);

      triggerEvent(eventTarget, Enums.Events.SEGMENTATION_DATA_MODIFIED, {
        segmentationId,
      });

      console.debug(`Updated scalar data for segmentation ${segmentationId}`);
    } else {
      // Volume doesn't exist - create it
      console.log('Volume not found in cache, creating new segmentation');

      try {
        // Use the segmentation service to properly create the segmentation
        const { segmentationService } = this.props.servicesManager.services;

        // First, remove any existing segmentation with the same ID
        const existingSegmentations = segmentationService.getSegmentations();
        const existingSegmentation = existingSegmentations.find(
          (seg) => seg.id === segmentationId
        );
        if (existingSegmentation) {
          console.log(
            `Removing existing segmentation with ID: ${segmentationId}`
          );
          segmentationService.remove(segmentationId);

          // Wait for removal to complete
          await new Promise((resolve) => setTimeout(resolve, 100));
        }

        // Create the new segmentation using the command manager
        await this.props.commandsManager.runCommand(
          'loadSegmentationsForViewport',
          {
            segmentations: [segmentation_object],
          }
        );

        // Wait for the volume to be properly loaded and cached with retries
        let retries = 0;
        const maxRetries = 20; // Increased retries
        let newVolumeLoadObject = null;

        while (retries < maxRetries && !newVolumeLoadObject) {
          newVolumeLoadObject = await cache.getVolumeLoadObject(segmentationId);
          if (!newVolumeLoadObject) {
            console.log(
              `Waiting for volume to cache... (attempt ${retries + 1}/${maxRetries})`
            );
            await new Promise((resolve) => setTimeout(resolve, 300)); // Increased wait time
            retries++;
          }
        }

        if (!newVolumeLoadObject) {
          console.error('Failed to load volume into cache after retries');
          throw new Error('Segmentation volume failed to load into cache');
        }

        // Ensure the volume is fully loaded
        const volume = await newVolumeLoadObject.promise;
        console.log('New volume loaded:', volume);

        // Wait a bit more for segmentation service to register everything
        await new Promise((resolve) => setTimeout(resolve, 500));

        // Verify the segmentation was properly created
        const segmentation_objects = segmentationService.getSegmentations();
        console.log(
          '_update: segmentation objects from service',
          segmentation_objects
        );

        // Additional check: ensure the segmentation exists in cornerstone state
        const cornerstoneSegmentation =
          segmentation.state.getSegmentation(segmentationId);
        console.log(
          '_update: cornerstone segmentation state',
          cornerstoneSegmentation
        );

        if (!cornerstoneSegmentation) {
          console.error(
            'Failed to register segmentation with cornerstone tools'
          );

          // Try manual registration as fallback
          console.log('Attempting manual segmentation registration...');

          // Get the volume reference for manual registration
          const volumeId = segmentationId;

          try {
            // Add segmentation to cornerstone state manually
            segmentation.state.addSegmentation({
              segmentationId,
              representation: {
                type: Enums.SegmentationRepresentations.Labelmap,
                data: {
                  volumeId,
                },
              },
              config: {
                segments: Object.fromEntries(
                  labels.map((label, index) => [
                    index + 1,
                    {
                      label,
                      isActive: true,
                      isVisible: true,
                    },
                  ])
                ),
              },
            });

            console.log('Manual segmentation registration successful');
          } catch (manualError) {
            console.error('Manual registration also failed:', manualError);
            throw new Error(
              'Both automatic and manual segmentation registration failed'
            );
          }
        }
      } catch (error) {
        console.error('Error during segmentation creation:', error);
        throw error;
      }
    }

    return segmentationId;
  };

  updateInstanceVariables = () => {
    const { viewport, displaySet } = this.getActiveViewportInfo();
    this.SeriesInstanceUID = displaySet.SeriesInstanceUID;
    this.StudyInstanceUID = displaySet.StudyInstanceUID;
    this.FrameOfReferenceUID = displaySet.instances[0].FrameOfReferenceUID;
    this.displaySetInstanceUID = displaySet.displaySetInstanceUID;
  };

  getActiveViewportInfo = () => {
    const { viewportGridService, displaySetService } =
      this.props.servicesManager.services;
    const { viewports, activeViewportId } = viewportGridService.getState();
    const viewport = viewports.get(activeViewportId);
    const displaySet = displaySetService.getDisplaySetByUID(
      viewport.displaySetInstanceUIDs[0]
    );

    // viewportId = viewport.viewportId
    // SeriesInstanceUID = displaySet.SeriesInstanceUID;
    // StudyInstanceUID = displaySet.StudyInstanceUID;
    // FrameOfReferenceUID = displaySet.instances[0].FrameOfReferenceUID;
    // displaySetInstanceUID = displaySet.displaySetInstanceUID;
    // numImageFrames = displaySet.numImageFrames;
    return { viewport, displaySet };
  };

  _debug = async () => {
    const nrrdFetch = await fetch('http://localhost:3000/pred2.nrrd');

    const info = {
      spleen: 1,
      'right kidney': 2,
      'left kidney': 3,
      liver: 6,
      stomach: 7,
      aorta: 8,
      'inferior vena cava': 9,
    };

    const nrrd = await nrrdFetch.arrayBuffer();

    this._update({ data: nrrd }, info);
  };

  parseResponse = (response) => {
    const contentType = response.headers['content-type'];
    const boundaryMatch = contentType.match(/boundary=([^;]+)/i);
    const boundary = boundaryMatch ? boundaryMatch[1] : null;

    const uint8 = new Uint8Array(response.data);
    const boundaryBytes = new TextEncoder().encode(`--${boundary}`);

    const parts = this.splitMultipart(uint8, boundaryBytes);

    let jsonData;
    let nrrdArrayBuffer;

    for (const part of parts) {
      const headerEnd = this.findDoubleCRLF(part);
      if (headerEnd === -1) continue;

      const headers = new TextDecoder().decode(part.subarray(0, headerEnd));
      const body = part.subarray(headerEnd);

      if (headers.includes('Content-Type: application/json')) {
        const jsonText = new TextDecoder().decode(body);
        jsonData = JSON.parse(jsonText);
      } else if (headers.includes('Content-Type: application/octet-stream')) {
        nrrdArrayBuffer = body.buffer.slice(
          body.byteOffset,
          body.byteOffset + body.byteLength
        );
      }
    }

    if (!jsonData || !nrrdArrayBuffer) {
      throw new Error('Failed to parse multipart response');
    }
    return { data: nrrdArrayBuffer, centroids: jsonData.centroids };
  };

  private splitMultipart(
    buffer: Uint8Array,
    boundary: Uint8Array
  ): Uint8Array[] {
    const parts: Uint8Array[] = [];
    let start = 0;

    while (start < buffer.length) {
      const boundaryIndex = this.findSubarray(buffer, boundary, start);
      if (boundaryIndex === -1) break;

      const partStart = boundaryIndex + boundary.length + 2; // skip \r\n
      const nextBoundary = this.findSubarray(buffer, boundary, partStart);
      const partEnd = nextBoundary !== -1 ? nextBoundary : buffer.length;

      parts.push(buffer.subarray(partStart, partEnd - 2)); // strip trailing \r\n
      start = partEnd;
    }
    return parts;
  }

  private findSubarray(
    haystack: Uint8Array,
    needle: Uint8Array,
    start = 0
  ): number {
    outer: for (let i = start; i <= haystack.length - needle.length; i++) {
      for (let j = 0; j < needle.length; j++) {
        if (haystack[i + j] !== needle[j]) continue outer;
      }
      return i;
    }
    return -1;
  }

  private findDoubleCRLF(buffer: Uint8Array): number {
    for (let i = 0; i < buffer.length - 3; i++) {
      if (
        buffer[i] === 13 &&
        buffer[i + 1] === 10 && // \r\n
        buffer[i + 2] === 13 &&
        buffer[i + 3] === 10 // \r\n
      ) {
        return i + 4; // position after \r\n\r\n
      }
    }
    return -1;
  }

  saveSegmentation = async (model, segmentationId) => {
    console.log('model', model);
    const datasources = extensionManager.getActiveDataSource();
    console.log('datasources', datasources);
    console.log('segmentationId', segmentationId);
    // Ensure dataSource is available
    // Call storeSegmentation from tzhe command module
    const seg = await segmentation.state.getSegmentation(segmentationId);
    console.log(`saveSegmentation: in the saveSegmentation `, seg);
    if (!seg) {
      console.error(`No segmentation found for ID: ${segmentationId}`);
      return;
    }

    await this.props.commandsManager.runCommand('storeSegmentationModified', {
      segmentationId,
      dataSource: datasources[0],
      isModel: model,
    });
  };

  updateView = async (response, labelNames, model) => {
    console.log('update_view: inside update view ');
    console.log(`update_view: response ${response} `);
    const { data, centroids } = this.parseResponse(response);
    const segmentationId = await this._update({ data, centroids }, labelNames);
    this.saveSegmentation(model, segmentationId);
  };

  uploadLabels = async () => {
    if (!this.isMONAILabelConnected) {
      console.error('Server is not connected');
      return;
    }

    try {
      const naturalizedReport = await this.getReport();

      const servicesManager = this.props.servicesManager;
      createReportAsync({
        servicesManager,
        getReport: async () => naturalizedReport, // Reuse the already retrieved report
        reportType: 'Segmentation',
      });

      try {
        const response = await fetch(
          'http://localhost:8000/workflow/update-uids',
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              study_uid: this.StudyInstanceUID,
              task_name: this.workflowName,
              series_instance_uid: this.SeriesInstanceUID,
              instance_uid: naturalizedReport.SeriesInstanceUID,
              modality: this.Modality,
            }),
          }
        );

        if (!response.ok) {
          throw new Error(`Error: ${response.status}`);
        }

        const result = await response.json();
      } catch (error) {
        console.error('Error updating workflow:', error);
        return;
      }
    } catch (error) {
      console.error('Error generating report:', error);
    }
  };
  getReport = async () => {
    const datasources = this.props.extensionManager.getActiveDataSource();
    const { displaySet } = this.getActiveViewportInfo();
    const segmentationId = displaySet.SeriesInstanceUID;
    return await this.props.commandsManager.runCommand('storeSegmentation', {
      segmentationId,
      dataSource: datasources[0],
    });
  };

  handleReturnButtonClick = () => {
    // Navigate back to the previous page
    window.history.back();
  };

  render() {
    const { isDataReady } = this.state;
    const isConnected = this.isMONAILabelConnected;

    return (
     <div
      className="monaiLabelPanel h-full w-[320px] flex-shrink-0 bg-[#0d1526] border-l border-[#1a2540] flex flex-col"
      style={{ fontFamily: "'Segoe UI', system-ui, sans-serif", fontSize: '12px', color: '#c8d8f0' }}
    >

  {/* HEADER */}


  {/* SCROLLABLE BODY */}
  <div className="flex-1 overflow-y-auto scrollbar-thin scrollbar-thumb-[#1e3058]">

    {/* SETTINGS */}
    <div className="px-2.5 py-2 border-b border-[#1a2540] [&_input]:bg-[#071020] [&_input]:border [&_input]:border-[#1e3058] [&_input]:rounded-[3px] [&_input]:text-[#4a9eff] [&_input]:text-[11px] [&_input]:px-1.5 [&_input]:py-0.5 [&_input]:outline-none [&_select]:bg-[#071020] [&_select]:border [&_select]:border-[#1e3058] [&_select]:rounded-[3px] [&_select]:text-[#c8d8f0] [&_select]:text-[11px] [&_select]:px-1.5 [&_select]:py-0.5 [&_select]:outline-none [&_select]:appearance-none [&_button]:bg-[#101c35] [&_button]:border [&_button]:border-[#1e3058] [&_button]:rounded-[3px] [&_button]:text-[#4a9eff] [&_button]:text-[11px] [&_button]:px-1.5 [&_button]:py-0.5 [&_label]:text-[#5a8ab8] [&_label]:text-[10px]">
      <p className="text-[9px] font-bold uppercase tracking-[0.12em] text-[#3d6a9e] mb-1.5">Settings</p>
      <SettingsTable ref={this.settings} onInfo={this.onInfo} />
    </div>

    {/* MODEL INFO LABEL */}
    <div className="px-2.5 py-2 border-b border-[#1a2540]">
      <p className="text-[11px] font-semibold text-[#7a9cc8]">{this.state.info.name}</p>
      <p className="text-[9px] text-[#3d6a9e]">{this.state.info.description}</p>
    </div>

    {/* SEGMENTATION BLOCKS */}
    {isDataReady && (
      <>
        {/* AUTO SEGMENTATION */}
        <div className="px-2.5 py-2 border-b border-[#1a2540] [&_select]:bg-[#071020] [&_select]:border [&_select]:border-[#1e3058] [&_select]:rounded-[3px] [&_select]:text-[#c8d8f0] [&_select]:text-[11px] [&_select]:px-1.5 [&_select]:py-0.5 [&_select]:outline-none [&_select]:cursor-pointer [&_select]:appearance-none [&_select]:w-full [&_input]:bg-[#071020] [&_input]:border [&_input]:border-[#1e3058] [&_input]:rounded-[3px] [&_input]:text-[#c8d8f0] [&_input]:text-[11px] [&_input]:px-1.5 [&_input]:py-0.5 [&_input]:outline-none [&_input]:w-full [&_button.actionButton]:bg-[#1a4a8a] [&_button.actionButton]:border-none [&_button.actionButton]:rounded-[3px] [&_button.actionButton]:text-[#c8d8f0] [&_button.actionButton]:text-[11px] [&_button.actionButton]:font-semibold [&_button.actionButton]:px-2.5 [&_button.actionButton]:py-0.5 [&_button.actionButton]:cursor-pointer [&_button.actionButton]:whitespace-nowrap [&_label]:text-[#5a8ab8] [&_label]:text-[10px]">
          <AutoSegmentation
            ref={this.actions['segmentation']}
            tabIndex={1}
            info={this.state.info}
            viewConstants={{
              SeriesInstanceUID: this.SeriesInstanceUID,
              StudyInstanceUID: this.StudyInstanceUID,
            }}
            servicesManager={this.props.servicesManager}
            client={this.client}
            notification={this.notification}
            updateView={this.updateView}
            onSelectActionTab={this.onSelectActionTab}
            onOptionsConfig={this.onOptionsConfig}
          />
        </div>

        {/* INTERACTIVE SEGMENTATION */}
        <div className="px-2.5 py-2 border-b border-[#1a2540] [&_select]:bg-[#071020] [&_select]:border [&_select]:border-[#1e3058] [&_select]:rounded-[3px] [&_select]:text-[#c8d8f0] [&_select]:text-[11px] [&_select]:px-1.5 [&_select]:py-0.5 [&_select]:outline-none [&_select]:cursor-pointer [&_select]:appearance-none [&_select]:w-full [&_input]:bg-[#071020] [&_input]:border [&_input]:border-[#1e3058] [&_input]:rounded-[3px] [&_input]:text-[#c8d8f0] [&_input]:text-[11px] [&_input]:px-1.5 [&_input]:py-0.5 [&_input]:outline-none [&_input]:w-full [&_button.actionButton]:bg-[#1a4a8a] [&_button.actionButton]:border-none [&_button.actionButton]:rounded-[3px] [&_button.actionButton]:text-[#c8d8f0] [&_button.actionButton]:text-[11px] [&_button.actionButton]:font-semibold [&_button.actionButton]:px-2.5 [&_button.actionButton]:py-0.5 [&_button.actionButton]:cursor-pointer [&_button.actionButton]:whitespace-nowrap [&_label]:text-[#5a8ab8] [&_label]:text-[10px]">
          <InteractiveSegmentation
            ref={this.actions['InteractiveSegmentation']}
            tabIndex={2}
            info={this.state.info}
            servicesManager={this.props.servicesManager}
            client={this.client}
            notification={this.notification}
            updateView={this.updateView}
            onSelectActionTab={this.onSelectActionTab}
            onOptionsConfig={this.onOptionsConfig}
            getActiveViewportInfo={this.getActiveViewportInfo}
            commandsManager={this.props.commandsManager}
          />
        </div>
      </>
    )}

    {/* SEGMENTATION TOOLBOX */}
    {this.state.segmentations?.map((segmentation) => (
      <div
        key={segmentation.id}
        className="px-2.5 py-2 border-b border-[#1a2540] [&_select]:bg-[#071020] [&_select]:border [&_select]:border-[#1e3058] [&_select]:rounded-[3px] [&_select]:text-[#c8d8f0] [&_select]:text-[11px] [&_select]:px-1.5 [&_select]:py-0.5 [&_select]:outline-none [&_select]:appearance-none [&_input]:bg-[#071020] [&_input]:border [&_input]:border-[#1e3058] [&_input]:rounded-[3px] [&_input]:text-[#c8d8f0] [&_input]:text-[11px] [&_input]:px-1.5 [&_input]:py-0.5 [&_input]:outline-none [&_button.actionButton]:bg-[#1a4a8a] [&_button.actionButton]:border-none [&_button.actionButton]:rounded-[3px] [&_button.actionButton]:text-[#c8d8f0] [&_button.actionButton]:text-[11px] [&_button.actionButton]:font-semibold [&_button.actionButton]:px-2.5 [&_button.actionButton]:py-0.5 [&_label]:text-[#5a8ab8] [&_label]:text-[10px]"
      >
        <SegmentationToolbox servicesManager={this.props.servicesManager} />
      </div>
    ))}
{this.workflowName && (
    <button
      onClick={this.uploadLabels}
      disabled={!this.isMONAILabelConnected}
      className="w-[calc(100%-20px)] mx-2.5 my-2 py-2 bg-[#1a4a8a] border-none rounded-[4px] text-[#c8d8f0] text-[12px] font-bold tracking-wider cursor-pointer hover:bg-[#1e5aaa] transition-colors flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
    >
      Upload Labels
    </button>
  )}

    {/* RESET ALL */}
   

  </div>

  {/* UPLOAD LABELS — sticky bottom */}
  
</div>
    );
  }
}