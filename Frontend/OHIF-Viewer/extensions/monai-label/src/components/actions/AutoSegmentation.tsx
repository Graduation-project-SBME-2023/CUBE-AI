import React from 'react';
import ModelSelector from '../ModelSelector';
import BaseTab from './BaseTab';
import { getRenderingEngines, utilities as csUtils } from '@cornerstonejs/core';
import getActiveViewportEnabledElement from '../../../../cornerstone/src/utils/getActiveViewportEnabledElement';
import { getEnabledElement } from '@cornerstonejs/core';
import { getEnabledElement as OHIFgetEnabledElement } from '../../../../cornerstone/src/state';
// import { getPatientName } from '../../../../../platform/app/src/routes/WorkList/WorkList'
import { vec3 } from 'gl-matrix';
import VistaLabels from '../../components/Toolbox/VistaLabels.json';



export default class AutoSegmentation extends BaseTab {
  static instance = null; // Static reference to hold the instance
  currentSlice: any;

  //To terminate SAM2-Calc-Img-Embeddings request
  embeddingAbortController = null;

  constructor(props) {
    super(props);

    // Set the static instance reference to clear from box and point comp of sam2
    AutoSegmentation.instance = this;

    //Model Bank
    this.modelSelector = React.createRef();

    // Convert the JSON to an array of {name, value} pairs
    const vistaClasses = Object.entries(VistaLabels).map(([name, value]) => ({
      name,
      value
    }));


    this.state = {
      currentModel: 'vista', 
      selectedQueue: null,

      vistaMode: 'automatic', // 'automatic' or 'interactive'
      vistaSelectedClass: vistaClasses[0] || { name: 'liver', value: 1 }, // default to first class
      vistaClasses: vistaClasses, // store the array in state

    };

    //initialize a viewportGridService for sam2 functionalities
    const { viewportGridService } = this.props.servicesManager.services;

    this.viewportGridService = viewportGridService;

    // dictionary carrying the max slice number in each port
    this.maxPortSlicesNumber = {
      'mpr-axial': 0,
      'mpr-sagittal': 0,
      'mpr-coronal': 0,
    };

  }
  /*
   Clear the static reference when unmounted
  */
  componentWillUnmount() {
    AutoSegmentation.instance = null;
  }

  
  // Update your handleClassChange to store the selected class object
  handleClassChange = (event) => {
    const selectedName = event.target.value;
    const selectedClass = this.state.vistaClasses.find(cls => cls.name === selectedName);
    this.setState({ vistaSelectedClass: selectedClass });
    console.log("selectedName", selectedName)
    console.log("selectedClass", selectedClass)
    console.log("this.state.vistaSelectedClass", this.state.vistaSelectedClass)
  };

  // Struct for segmentation enqueue request
  createSegmentationRequest(model, image, params, labels, PatientName) {
    return {
      model,
      image,
      params,
      labels,
      PatientName,
      notification: this.notification, // Pass notification handler
      client: this.props.client(), // Client function
      updateView: this.props.updateView, // Update view function
    };
  }

  onSelectModel = (model) => {

    this.setState(
      {
        currentModel: model
      },);

  };

  // Set the selected queue to the current selected one
  handleQueueChange = (event) => {
    const selectedQueue = event.target.value;
    console.log('selectedQueue', selectedQueue);
    this.setState({ selectedQueue });
  };


  generateSessionId = () => {
    return `session-${Date.now()}-${Math.floor(Math.random() * 1000000)}`;
  };


  //parse multipart form data and extract the mask
  onSegmentation = async (samCheck = true) => {

    const { info, viewConstants } = this.props;
    const image = viewConstants.SeriesInstanceUID;
    const model = this.modelSelector.current.currentModel();
    const config = this.props.onOptionsConfig();
    const params =
      config && config.infer && config.infer[model] ? config.infer[model] : {};

    const nid = this.notification.show({
      title: 'MONAI Label',
      message: 'Running Auto-Segmentation...',
      type: 'info',
      duration: 60000,
    });

    //if vista
    if (model == 'vista') {
        //send class value
        params.Class = this.state.vistaSelectedClass.value;
        console.log("params.Class", params.Class);
      
    }

    const labels = info.models[model].labels;
    const response = await this.props
      .client()
      .segmentation(model, image, params);
    console.log("the parameters for model autoseg run", params);

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
      await this.props.updateView(response, labels, model);
      this.state.resetInteractions = false;

      this.notification.show({
        title: 'MONAI Label',
        message: 'Run Segmentation - Successful',
        type: 'success',
        duration: 2000,
      });
    }
  };

  render() {
  let models = [];

  if (this.props.info && this.props.info.models) {
    for (let [name, model] of Object.entries(this.props.info.models)) {
      if (
        model.type === 'segmentation' &&
        name !== 'sam' &&
        name !== 'nninteractive'
      ) {
        models.push(name);
      }
    }
  }

    return (
      <div className="tab">
        <input
          type="radio"
          name="rd"
          id={this.tabId}
          className="tab-switch"
          value="segmentation"
          onClick={this.onSelectActionTab}
          defaultChecked
        />
        <label htmlFor={this.tabId} className="tab-label">
          Auto-Segmentation
        </label>
        <div
          className="tab-content"
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
          }}
        >
          <ModelSelector
            ref={this.modelSelector}
            name="segmentation"
            title="Segmentation"
            models={models}
            currentModel={this.state.currentModel}
            onClick={this.onSegmentation}
            onSelectModel={this.onSelectModel}
            usage={
              <p style={{ fontSize: 'smaller' }}>
                Fully automated segmentation <b>without any user prompt</b>.
                Just select a model and click to run
              </p>
            }
          />

          {this.state.currentModel === 'vista' && (
            <div style={{
              width: '80%',
              margin: '10px 0',
              borderTop: '1px solid #eee',
              paddingTop: '15px'
            }}>
       

              {/* Conditional Rendering (keep the rest of your existing code here) */}
            {this.state.vistaMode === 'automatic' ? (
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center', // 1. Centers the Label + Input group
                width: '100%',            // 2. Ensures the container uses available space
                padding: '0 5px',         // 3. Adds a tiny safety buffer on the sides
                boxSizing: 'border-box',  // 4. Prevents padding from causing overflow
                gap: '10px'
              }}>
                <label
                  htmlFor="vista-classes"
                  style={{
                    fontSize: '14px',     // Adjusted slightly for balance
                    fontWeight: '500',
                    color: '#333',
                    whiteSpace: 'nowrap'
                  }}
                >
                  Class:
                </label>
                <select
                  id="vista-classes"
                  value={this.state.vistaSelectedClass.name}
                  onChange={this.handleClassChange}
                  style={{
                    flex: 1,                 // 5. Allows the input to fill space...
                    maxWidth: '160px',       // 6. ...but stops it before hitting the edge
                    padding: '4px 8px',      // Increased padding slightly for better look
                    borderRadius: '6px',
                    border: '1px solid #ccc',
                    backgroundColor: '#fff',
                    fontSize: '12px',        // Matched font size to look proportional
                    outline: 'none',
                    cursor: 'pointer'
                  }}
                >
                  {this.state.vistaClasses.map((cls) => (
                    <option key={cls.name} value={cls.name}>
                      {cls.name}
                    </option>
                  ))}
                </select>
                </div>
              ) : (
                
                  <div style={{
                  display: 'flex',
                  alignItems: 'center', // Vertically center items
                  justifyContent: 'center',
                  marginBottom: '15px'
                }}> 
                   
                 
                </div>
                 
              )}
            </div>
          )}

        </div>
      </div>
    );
  }
}
