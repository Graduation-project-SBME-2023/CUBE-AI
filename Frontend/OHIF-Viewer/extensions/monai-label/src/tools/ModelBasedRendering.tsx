import React, { Component } from 'react';
import { PiBoundingBoxDuotone } from "react-icons/pi";
import { GoXCircleFill } from "react-icons/go";
import { TbPointerPlus } from "react-icons/tb";
import { TbPointerX } from "react-icons/tb";
import { RxReset } from "react-icons/rx";
import "./ModelBasedRendering.css";
const BoxButton = ({ onClick, disabled }) => {
  if (disabled) return null; 

  return (
  <button className="box_button" onClick={onClick} title="Draw a bounding box">
    <PiBoundingBoxDuotone size={24} />
  </button>


  );
};
const DEFAULT_COLOR_LUT = [
  [200, 0, 0, 255],     // red
  [0, 200, 0, 255],     // green
  [0, 0, 200, 255],     // blue
  [200, 200, 0, 255],   // yellow
  [200, 0, 200, 255],   // magenta
  [0, 200, 200, 255],   // cyan
  [255, 127, 0, 255],   // orange
  [127, 0, 255, 255],   // purple
  [0, 127, 255, 255],   // sky blue
  [127, 127, 127, 255], // gray
];

const PositiveButton = ({ onClick, disabled }) => {
  // if (disabled) return null; 
  return(
  <button className="box_button" onClick={onClick} title="Add Postive point" disabled ={disabled} >
    <TbPointerPlus size={20} />
  </button>
);
};

const NegativeButton = ({ onClick, disabled }) => {
  // if (disabled) return null; 
  return(
  <button onClick={onClick}  className="box_button" title="Add Negative point" disabled ={disabled}>
    <GoXCircleFill size={20}/>
    
  </button>
  );
};

const ResetButton = ({ onClick, disabled }) => {
  if (disabled) return null; 
  return(
  <button onClick={onClick} className="box_button" title="pop last prompt">
   <RxReset size={20}/>
  </button>
);
};

const ResetAllButton = ({ onClick, disabled }) => {
  if (disabled) return null; 
  return(
  <button className="add_object"
    onClick={onClick}
  >
    Reset All
  </button>
);
};

const AddObjectButton = ({ onClick, disabled }) => {
  if (disabled) return null; 
  return(
  <button className="add_object"
    onClick={onClick} 
  >
    Add Another Object
  </button>
);
};

// Config per model (what to show/enable)
const MODE_CONFIG = {
  vista: {
    row1: { box: false, pos: true, neg: true, reset: true, objectId: false, color: false },
    row2: { resetAll: false },
    row3: { addObject: false },
    textPrompt: false,
  },
  nninteractive: {
    row1: { box: false, pos: true, neg: true, reset: true, objectId: false, color: false },
    row2: { resetAll: true },
    row3: { addObject: false },
    textPrompt: false,
  },
  sam: {
    row1: { box: true, pos: true, neg: true, reset: false, objectId: true, color: true },
    row2: { resetAll: false },
    row3: { addObject: true },
    textPrompt: false,
  },
  voxtell: {
    row1: { box: false, pos: false, neg: false, reset: false, objectId: false, color: false },
    row2: { resetAll: false },
    row3: { addObject: false },
    textPrompt: true,
  },
};


export default class ModelBasedRendering extends Component {
  state = {
    objects: [
      { id: 1, color: "#ff0000" } // start with one object
    ],
    selectedId: 1,
    textPrompt: '',
  };
  rgbaToHex=(r, g, b, a = 255) =>{
    return (
      "#" +
      [r, g, b, a]
        .map((x) => {
          const hex = x.toString(16);
          return hex.length === 1 ? "0" + hex : hex;
        })
        .join("")
    );
  }
  // Add another object
  handleAddObject = () => {

    const nextId = this.state.objects.length + 1;
    const [r, g, b, a] = DEFAULT_COLOR_LUT[(nextId-1) % DEFAULT_COLOR_LUT.length];
    const randomColor = this.rgbaToHex (r,g,b,a);
    // const randomColor = `#${Math.floor(Math.random() * 16777215).toString(16)}`;
    this.setState((prev) => ({
      objects: [...prev.objects, { id: nextId, color: randomColor }],
    }));
  };

  handleRowSelect = (objId) => {
    console.log("Row selected:", objId);
    this.setState({ selectedId: objId });
    if (this.props.onRowSelect) {
      this.props.onRowSelect(objId);
    }
  }

renderRow1(config = {}) {
  const { objects } = this.state;
  const isSam = this.props.mode === "sam";
// if not sam
  if (!isSam) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          marginBottom: "10px",
          padding: "6px 8px",
          border: "2px solid #929295ff",
          borderRadius: "12px",
        }}
      >
        {/* <span style={{ fontWeight: "bold", fontSize: "14px" }}>1:</span> */}
        {config.box && <BoxButton onClick={() => this.props.onBoxClick("1")} />}
        {config.pos && <PositiveButton onClick={() => this.props.onPositiveClick("1")} disabled={this.props.positiveDisabled} />}
        {config.neg && <NegativeButton onClick={() => this.props.onNegativeClick("1")} disabled={this.props.negativeDisabled}/>}
        {config.reset && <ResetButton onClick={() => this.props.onResetClick("1")} />}
        {config.color && (
          <div
            style={{
              width: "20px",
              height: "20px",
              borderRadius: "50%",
              backgroundColor: "#ff0000", // fixed red for non-SAM
              border: "1px solid #444",
            }}
          />
        )}
      </div>
    );
  }

// if sam
  return (
    <>
      {objects.map((obj) => (
        <div
          key={obj.id}
          onClick={() => this.handleRowSelect(obj.id)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "4px",
            marginBottom: "6px",
            padding: "4px 6px",
            borderRadius: "10px",
            flexWrap: "wrap",
            cursor: "pointer",
            border: this.state.selectedId === obj.id ? "2px solid blue" : "2px solid #929295ff",
          }}
        >
          <span style={{ fontWeight: "bold", fontSize: "12px" }}>
            {obj.id}:
          </span>
          {config.box && <BoxButton onClick={() => this.props.onBoxClick(obj.id)} />}
          {config.pos && <PositiveButton onClick={() => this.props.onPositiveClick(obj.id)} disabled={this.props.positiveDisabled} />}
          {config.neg && <NegativeButton onClick={() => this.props.onNegativeClick(obj.id)} disabled={this.props.negativeDisabled} />}
          {config.reset && <ResetButton onClick={() => this.props.onResetClick(obj.id)} />}
          {config.color && (
            <div
              style={{
                width: "14px",
                height: "14px",
                borderRadius: "50%",
                backgroundColor: obj.color,
                border: "1px solid #444",
              }}
            />
          )}
        </div>
      ))}
    </>
  );
}

  renderRow2(config = {}) {
    return (
      config.resetAll && (
        <div style={{ display: "flex", justifyContent: "center", marginBottom: "10px" }}>
          <ResetAllButton onClick={this.props.onResetAllClick} />
        </div>
      )
    );
  }

  renderRow3(config = {}) {
    return (
      config.addObject && (
        <div style={{ display: "flex", justifyContent: "center", marginBottom: "10px" }}>
          <AddObjectButton onClick={this.handleAddObject} />
        </div>
      )
    );
  }

  handleTextPromptChange = (e) => {
    const textPrompt = e.target.value;
    this.setState({ textPrompt });
    if (this.props.onTextPromptChange) {
      this.props.onTextPromptChange(textPrompt);
    }
  };

  renderTextPromptInput(config = {}) {
    if (!config.textPrompt) return null;
    return (
      <div style={{ marginBottom: "10px" }}>
        <label style={{ display: "block", marginBottom: "6px", fontWeight: "bold", fontSize: "14px" }}>
          What do you want to segment?
        </label>
        <input
          type="text"
          placeholder="e.g., spleen, liver, kidney"
          value={this.state.textPrompt}
          onChange={this.handleTextPromptChange}
          style={{
            width: "100%",
            padding: "8px",
            fontSize: "14px",
            border: "2px solid #929295ff",
            borderRadius: "6px",
            boxSizing: "border-box",
          }}
        />
      </div>
    );
  }

  render() {
    const { mode } = this.props;
    const config = MODE_CONFIG[mode] || { row1: {}, row2: {}, row3: {} };
  console.log('ModelBasedRendering render - props:', {
    positiveDisabled: this.props.positiveDisabled,
    negativeDisabled: this.props.negativeDisabled
  });

    return (
      <div style={{ padding: "10px" }}>
        {/* Text Prompt Input */}
        {this.renderTextPromptInput(config)}

        {/* Row 1 */}
        {this.renderRow1(config.row1)}

        {/* Row 2 */}
        {this.renderRow2(config.row2)}

        {/* Row 3 */}
        {this.renderRow3(config.row3)}
      </div>
    );
  }

}
