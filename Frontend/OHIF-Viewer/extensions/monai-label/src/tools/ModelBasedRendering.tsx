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


const PositiveButton = ({ onClick, disabled }) => {
  if (disabled) return null; 
  return(
  <button className="box_button" onClick={onClick} title="Add Postive point">
    <TbPointerPlus size={20} />
  </button>
);
};

const NegativeButton = ({ onClick, disabled }) => {
  if (disabled) return null; 
  return(
  <button onClick={onClick}  className="box_button" title="Add Negative point">
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
  },
  nninteractive: {
    row1: { box: false, pos: true, neg: true, reset: true, objectId: false, color: false },
    row2: { resetAll: true },
    row3: { addObject: false },
  },
  sam: {
    row1: { box: true, pos: true, neg: true, reset: false, objectId: true, color: true },
    row2: { resetAll: false },
    row3: { addObject: true },
  },
};

export default class ModelBasedRendering extends Component {
  state = {
    objects: [
      { id: 1, color: "#ff0000" } // start with one object
    ],
  };

  // Add another object
  handleAddObject = () => {
    const nextId = this.state.objects.length + 1;
    const randomColor = `#${Math.floor(Math.random() * 16777215).toString(16)}`;
    this.setState((prev) => ({
      objects: [...prev.objects, { id: nextId, color: randomColor }],
    }));
  };

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
        {config.pos && <PositiveButton onClick={() => this.props.onPositiveClick("1")} />}
        {config.neg && <NegativeButton onClick={() => this.props.onNegativeClick("1")} />}
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
          style={{
            display: "flex",
            alignItems: "center",
            gap: "4px",
            marginBottom: "6px",
            padding: "4px 6px",
            border: "2px solid #929295ff",
            borderRadius: "10px",
            flexWrap: "wrap",
          }}
        >
          <span style={{ fontWeight: "bold", fontSize: "12px" }}>
            {obj.id}:
          </span>
          {config.box && <BoxButton onClick={() => this.props.onBoxClick(obj.id)} />}
          {config.pos && <PositiveButton onClick={() => this.props.onPositiveClick(obj.id)} />}
          {config.neg && <NegativeButton onClick={() => this.props.onNegativeClick(obj.id)} />}
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

  render() {
    const { mode } = this.props;
    const config = MODE_CONFIG[mode] || { row1: {}, row2: {}, row3: {} };

    return (
      <div style={{ padding: "10px" }}>
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
