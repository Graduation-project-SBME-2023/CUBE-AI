import React, { Component } from 'react';
import PropTypes from 'prop-types';

import './ModelSelector.css';

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
    statusMessage: PropTypes.string,
    statusType: PropTypes.string,
  };

  constructor(props) {
    super(props);

    const currentModel =
      props.currentModel || (props.models.length > 0 ? props.models[0] : '');

    this.state = {
      models: props.models,
      currentModel: currentModel,
      buttonDisabled: false,
    };
  }

  static getDerivedStateFromProps(props, current_state) {
    const modelsChanged =
      current_state.models.length !== props.models.length ||
      current_state.models.some(
        (model, index) => model !== props.models[index]
      );
    const currentModel =
      props.currentModel ||
      (props.models.includes(current_state.currentModel)
        ? current_state.currentModel
        : props.models[0] || '');

    if (modelsChanged || currentModel !== current_state.currentModel) {
      return {
        models: props.models,
        currentModel,
      };
    }
    return null;
  }

  onChangeModel = (evt) => {
    if (this.props.isDisabled) {
      return;
    }

    this.setState({ currentModel: evt.target.value });
    // AutoSegmentation.clearSam2UIContrainers();

    // if (evt.target.value == 'sam') {
    //   // calc image embedding(if SAM model)
    //   console.log("Calculating image embeddings for SAM model...");
    //   AutoSegmentation.calculateImageEmbeddings(false);
    // }

    if (this.props.onSelectModel) {
      this.props.onSelectModel(evt.target.value);
    }
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

    let model = this.currentModel();
    if (!model) {
      console.error('This should never happen!');
      model = this.props.models.length > 0 ? this.props.models[0] : '';
    }

    this.setState({ buttonDisabled: true });
    try {
      await this.props.onClick(model);
    } finally {
      this.setState({ buttonDisabled: false });
    }
  };

  render() {
    const currentModel = this.currentModel();
    const buttonText = 'Run';

    return (
      <div className="modelSelector">
        <table className="modelSelector__table">
          <tbody>
            <tr>
              <td className="modelSelector__label" colSpan="3">
                Model
              </td>
            </tr>
            <tr>
              <td width="80%">
                <select
                  className="selectBox"
                  onChange={this.onChangeModel}
                  value={currentModel}
                  disabled={this.props.isDisabled}
                >
                  {this.props.models.map((model) => (
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
                    this.state.buttonDisabled ||
                    !this.props.models.length ||
                    this.props.isDisabled
                  }
                  aria-busy={this.state.buttonDisabled}
                  style={{ display: this.props.onClick ? 'flex' : 'none' }}
                >
                  {this.state.buttonDisabled && (
                    <span
                      className="modelSelector__spinner"
                      aria-hidden="true"
                    />
                  )}
                  {buttonText}
                </button>
              </td>
            </tr>
            {this.props.scribblesSelector}
          </tbody>
        </table>
        {this.props.usage}
        {this.props.statusMessage && (
          <div
            className={`modelSelector__status modelSelector__status--${
              this.props.statusType || 'info'
            }`}
            role="status"
          >
            {this.props.statusMessage}
          </div>
        )}
      </div>
    );
  }
}
