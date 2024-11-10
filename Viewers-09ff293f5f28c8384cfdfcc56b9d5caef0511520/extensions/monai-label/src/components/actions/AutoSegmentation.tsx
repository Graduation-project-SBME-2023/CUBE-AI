import React from 'react';
import ModelSelector from '../ModelSelector';
import BaseTab from './BaseTab';
import segmentationQueue from '../SegmentationQueue'; // Import the global queue

export default class AutoSegmentation extends BaseTab {
  constructor(props) {
    super(props);

    this.modelSelector = React.createRef();
    this.state = {
      currentModel: null,
    };
  }

  // Struct for segmentation enqueue request
  createSegmentationRequest(model, image, params, labels) {
    return {
      model,
      image,
      params,
      labels,
      notification: this.notification, // Pass notification handler
      client: this.props.client(), // Client function
      updateView: this.props.updateView, // Update view function
    };
  }

  onSelectModel = (model) => {
    this.setState({ currentModel: model });
  };

  // Enqueue segmentation task into the global queue
  onSegmentationEnqueue = () => {
    const { info, viewConstants } = this.props;
    const image = viewConstants.SeriesInstanceUID;
    const model = this.modelSelector.current.currentModel();
    const config = this.props.onOptionsConfig();
    const params =
      config && config.infer && config.infer[model] ? config.infer[model] : {};

    const labels = info.models[model].labels;

    // Create a request object
    const request = this.createSegmentationRequest(
      model,
      image,
      params,
      labels
    );

    console.log('PARAMS: ', request.params);
    console.log('IMAGE (SERIESID): ', request.image);
    console.log('VIEW CONSTANTS: ', viewConstants);
    console.log('PROPS: ', this.props);
    console.log('INFO: ', info);
    console.log('CONFIG: ', config);

    // Enqueue the request in the global queue
    segmentationQueue.enqueue(request);

    ////////////////////////to do: add model name to the infro message
    const nid = this.notification.show({
      title: 'MONAI Label',
      message: `Model ${model} is added to queue`,
      type: 'info',
      duration: 20000,
    });
  };

  onSegmentation = async () => {
    const nid = this.notification.show({
      title: 'MONAI Label',
      message: 'Running Auto-Segmentation...',
      type: 'info',
      duration: 60000,
    });

    // TODO:: Fix Image ID...
    const { info, viewConstants } = this.props;
    const image = viewConstants.SeriesInstanceUID;
    const model = this.modelSelector.current.currentModel();
    const config = this.props.onOptionsConfig();
    const params =
      config && config.infer && config.infer[model] ? config.infer[model] : {};

    const labels = info.models[model].labels;
    const response = await this.props
      .client()
      .segmentation(model, image, params);

    // Bug:: Notification Service on show doesn't return id
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
        message: 'Run Segmentation - Successful',
        type: 'success',
        duration: 2000,
      });

      await this.props.updateView(response, labels);
    }
  };

  render() {
    let models = [];
    if (this.props.info && this.props.info.models) {
      for (let [name, model] of Object.entries(this.props.info.models)) {
        if (model.type === 'segmentation') {
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
          <button
            className="customButton"
            onClick={this.onSegmentationEnqueue}
            style={{
              marginTop: '10px',
              padding: '10px 20px',
              backgroundColor: '#007bff',
              color: 'white',
              border: 'none',
              borderRadius: '20px',
              cursor: 'pointer',
            }}
          >
            Add to Queue
          </button>
        </div>
      </div>
    );
  }
}
