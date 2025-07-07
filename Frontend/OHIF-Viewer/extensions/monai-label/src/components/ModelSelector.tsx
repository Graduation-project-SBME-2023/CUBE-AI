
import React, { Component } from 'react';
import PropTypes from 'prop-types';

import './ModelSelector.css';
import AutoSegmentation from '../components/actions/AutoSegmentation';


export default class ModelSelector extends Component {
  static propTypes = {
    name: PropTypes.string,
    title: PropTypes.string,
    models: PropTypes.array,
    currentModel: PropTypes.string,
    usage: PropTypes.any,
    onClick: PropTypes.func,
    onSelectModel: PropTypes.func,
    scribblesSelector: PropTypes.any,
    isDisabled: PropTypes.bool,
  };

  constructor(props) {
    super(props);

    // Select 'sam' if it's found in the models array, otherwise use the first model or default to an empty string
    const currentModel = props.models.includes('sam')
      ? 'sam'
      : props.currentModel || (props.models.length > 0 ? props.models[0] : '');

    this.state = {
      models: props.models,
      currentModel: currentModel,
      buttonDisabled: false,
    };

  }

  static getDerivedStateFromProps(props, current_state) {
    if (current_state.models !== props.models) {
      // Check for 'sam' in the updated models list
      const currentModel = props.models.includes('sam') ? 'sam' : (props.models.length > 0 ? props.models[0] : '');

      return {
        models: props.models,
        currentModel: currentModel,
      };
    }
    return null;
  }

  onChangeModel = evt => {

    if (this.props.isDisabled) return; // Disable model change if isDisabled is true

    console.log("evt.target.value....", evt.target.value);
    console.log("I am here");
    this.setState({ currentModel: evt.target.value });
    AutoSegmentation.clearSam2UIContrainers();

    if (evt.target.value == 'sam') {
      // calc image embedding(if SAM model)
      console.log("Calculating image embeddings for SAM model...");
      AutoSegmentation.calculateImageEmbeddings(false);
    }

    if (this.props.onSelectModel) this.props.onSelectModel(evt.target.value);
  };

  currentModel = () => {
    return this.props.currentModel
      ? this.props.currentModel
      : this.state.currentModel;
  };

  onClickBtn = async () => {
    if (this.state.buttonDisabled || this.props.isDisabled) {
      return;
    }

    let model = this.state.currentModel;
    if (!model) {
      console.error('This should never happen!');
      model = this.props.models.length > 0 ? this.props.models[0] : '';
    }

    this.setState({ buttonDisabled: true });
    await this.props.onClick(model);
    this.setState({ buttonDisabled: false });
  };

  render() {
    const currentModel = this.currentModel();
    const buttonText = currentModel === 'sam' ? 'Propagate' : 'Run';  // Change button text based on current model

    return (
      <div className="modelSelector">
        <table>
          <tbody>
            <tr>
              <td colSpan="3">Models:</td>
            </tr>
            <tr>
              <td width="80%">
                <select
                  className="selectBox"
                  onChange={this.onChangeModel}
                  value={currentModel}
                  disabled={this.props.isDisabled}
                >
                  {this.props.models.map(model => (
                    <option key={model} name={model} value={model}>
                      {`${model} `}
                    </option>
                  ))}
                </select>
              </td>
              <td width="2%">&nbsp;</td>
              <td width="18%">
                <button
                  className="actionButton"
                  onClick={this.onClickBtn}
                  title={'Run ' + this.props.title}
                  disabled={
                    this.state.isButtonDisabled || !this.props.models.length || this.props.isDisabled
                  }
                  style={{
                    display: this.props.onClick ? 'block' : 'none',
                    fontSize: '12px',
                    padding: '2px 15px',
                  }}
                >
                  {buttonText}
                </button>
              </td>
            </tr>
            {this.props.scribblesSelector}
          </tbody>
        </table>
        {this.props.usage}
      </div>
    );
  }
}
