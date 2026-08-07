import React from 'react';
import ModelSelector from '../ModelSelector';
import BaseTab from './BaseTab';
// import { getPatientName } from '../../../../../platform/app/src/routes/WorkList/WorkList'
import VistaLabels from '../../components/Toolbox/VistaLabels.json';

export default class AutoSegmentation extends BaseTab {
  static instance = null; // Static reference to hold the instance
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
      value,
    }));

    this.state = {
      currentModel: 'vista',
      selectedQueue: null,

      vistaMode: 'automatic', // 'automatic' or 'interactive'
      vistaSelectedClass: null, // force user to select a class
      vistaClasses: vistaClasses, // store the array in state
      vistaClassSearch: '',
      vistaClassSearchOpen: false,
      statusMessage: '',
      statusType: 'info',
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

  selectVistaClass = (selectedName) => {
    const selectedClass = this.state.vistaClasses.find(
      (cls) => cls.name === selectedName
    );
    if (!selectedClass) {
      return;
    }

    this.setState({
      vistaSelectedClass: selectedClass,
      vistaClassSearch: selectedClass.name,
      vistaClassSearchOpen: false,
    });
  };

  handleClassSearchChange = (event) => {
    const searchText = event.target.value;
    const exactMatch = this.state.vistaClasses.find(
      (cls) => cls.name.toLowerCase() === searchText.trim().toLowerCase()
    );
    this.setState({
      vistaClassSearch: searchText,
      vistaClassSearchOpen: true,
      ...(exactMatch ? { vistaSelectedClass: exactMatch } : {}),
    });
  };

  handleClassSearchKeyDown = (event) => {
    if (event.key === 'Escape') {
      this.setState({ vistaClassSearchOpen: false });
      return;
    }

    if (event.key === 'Enter') {
      const firstMatch = this.getMatchingVistaClasses(
        this.state.vistaClassSearch
      )[0];
      if (firstMatch) {
        event.preventDefault();
        this.selectVistaClass(firstMatch.name);
      }
    }
  };

  getMatchingVistaClasses = (searchText) => {
    const normalizedSearch = searchText.trim().toLowerCase();
    return this.state.vistaClasses
      .filter((cls) => cls.name.toLowerCase().includes(normalizedSearch))
      .sort((a, b) => {
        const aStartsWith = a.name.toLowerCase().startsWith(normalizedSearch);
        const bStartsWith = b.name.toLowerCase().startsWith(normalizedSearch);
        return Number(bStartsWith) - Number(aStartsWith);
      });
  };

  checkHasPrompts = () => {
    try {
      // Import cornerstoneTools to access annotation manager
      const cornerstoneTools = require('@cornerstonejs/tools');
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
    this.setState({
      currentModel: model,
    });
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
    this.setState({ statusMessage: '', statusType: 'info' });
    const { info, viewConstants } = this.props;
    const image = viewConstants.SeriesInstanceUID;
    const model = this.modelSelector.current.currentModel();
    const config = this.props.onOptionsConfig();
    const params = {
      ...(config && config.infer && config.infer[model]
        ? config.infer[model]
        : {}),
    };

    //if vista
    if (model === 'vista') {
      //send class value
      params.Class = this.state.vistaSelectedClass.value;
      console.log('params.Class', params.Class);
    }

    const labels = info.models[model].labels;
    let response;
    try {
      response = await this.props.client().segmentation(model, image, params);
    } catch (error) {
      console.error(`Failed to run ${model} auto-segmentation`, error);
      this.setState({
        statusMessage: 'Segmentation could not be completed. Please try again.',
        statusType: 'error',
      });
      return;
    }
    console.log('the parameters for model autoseg run', params);

    if (response.status !== 200) {
      this.setState({
        statusMessage:
          'Segmentation failed. Please check the model and try again.',
        statusType: 'error',
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
    const models = [];

    if (this.props.info && this.props.info.models) {
      for (const [name, model] of Object.entries(this.props.info.models)) {
        if (
          model.type === 'segmentation' &&
          name !== 'sam' &&
          name !== 'nninteractive' &&
          name !== 'voxtell'
        ) {
          models.push(name);
        }
      }
    }

    const filteredVistaClasses = this.getMatchingVistaClasses(
      this.state.vistaClassSearch
    );
    const visibleVistaClasses = filteredVistaClasses.slice(0, 8);

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
            statusMessage={this.state.statusMessage}
            statusType={this.state.statusType}
            isRunDisabled={this.state.currentModel === 'vista' && !this.state.vistaSelectedClass}
            usage={
              <p style={{ fontSize: 'smaller' }}>
                Fully automated segmentation <b>without any user prompt</b>.
                Just select a model and click to run
              </p>
            }
          />

          {this.state.currentModel === 'vista' && (
            <div
              style={{
                width: '80%',
                margin: '10px 0',
                borderTop: '1px solid #eee',
                paddingTop: '15px',
              }}
            >
              {/* Conditional Rendering (keep the rest of your existing code here) */}
              {this.state.vistaMode === 'automatic' ? (
                <div
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'stretch',
                    width: '100%', // 2. Ensures the container uses available space
                    padding: '0 5px', // 3. Adds a tiny safety buffer on the sides
                    boxSizing: 'border-box', // 4. Prevents padding from causing overflow
                    gap: '8px',
                  }}
                >
                  <label
                    htmlFor="vista-class-search"
                    style={{
                      fontSize: '14px', // Adjusted slightly for balance
                      fontWeight: '500',
                      color: '#333',
                    }}
                  >
                    Search label
                  </label>
                  <input
                    id="vista-class-search"
                    type="text"
                    value={this.state.vistaClassSearch}
                    onChange={this.handleClassSearchChange}
                    onFocus={() =>
                      this.setState({ vistaClassSearchOpen: true })
                    }
                    onBlur={() =>
                      this.setState({ vistaClassSearchOpen: false })
                    }
                    onKeyDown={this.handleClassSearchKeyDown}
                    placeholder="Type label name..."
                    autoComplete="off"
                    role="combobox"
                    aria-autocomplete="list"
                    aria-expanded={this.state.vistaClassSearchOpen}
                    aria-controls="vista-class-matches"
                  />
                  {this.state.vistaClassSearchOpen && (
                    <div
                      id="vista-class-matches"
                      role="listbox"
                      aria-label="Matching Vista labels"
                      style={{
                        maxHeight: '190px',
                        overflowY: 'auto',
                        border: '1px solid #1e3058',
                        borderRadius: '6px',
                        background: '#071020',
                      }}
                    >
                      {!visibleVistaClasses.length ? (
                        <div style={{ padding: '8px', color: '#7a9cc8' }}>
                          No matching labels
                        </div>
                      ) : (
                        visibleVistaClasses.map((cls) => (
                          <button
                            key={cls.name}
                            type="button"
                            role="option"
                            aria-selected={
                              cls.name === this.state.vistaSelectedClass?.name
                            }
                            onMouseDown={(event) => {
                              event.preventDefault();
                              this.selectVistaClass(cls.name);
                            }}
                            style={{
                              display: 'block',
                              width: '100%',
                              padding: '8px 10px',
                              border: 'none',
                              borderBottom: '1px solid #1a2540',
                              background:
                                cls.name === this.state.vistaSelectedClass?.name
                                  ? '#17345d'
                                  : 'transparent',
                              color: '#c8d8f0',
                              textAlign: 'left',
                              cursor: 'pointer',
                            }}
                          >
                            {cls.name}
                          </button>
                        ))
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center', // Vertically center items
                    justifyContent: 'center',
                    marginBottom: '15px',
                  }}
                ></div>
              )}
            </div>
          )}
        </div>
      </div>
    );
  }
}
