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


//Global dictionaries containing Sam2 UI components to be refreshed whenever slice or entire study is changed
let Point_ui = {};
let Box = {};
//Global dictionary containing vista UI components to be refreshed whenever slice or entire study is changed
let VistaPoints = {};

let nnInteractivePoints = {};

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
      currentModel: 'vista', // make sam the initial choice
      selectedQueue: null,

      //sam2 dictionary with the object id as key and the points & boxe array as value to be sent to monai server
      prompts: {},

      //The ui component that is created for sam2 prompts
      pointObj: null,

      //state to hold dynamic UI components for sam2
      uiComponents: [],
      boxes: {},

      //state to hold the colors for each object in sam2
      objectColors: {},

      // "+" or "-" for point type of sam2 prompts
      selectedPointType: null,

      // Flag to indicate if the user is drawing a box for sam2
      drawingMode: 'none',

      //Tack if image embeddings are being calculated
      isCalculatingEmbeddings: true,

      //Tack if image embeddings already calculated or not
      volumeEmbeddingsCalculated: true,

      // sam2CalculateImageEmbeddingsSessionID: "",

      vistaMode: 'automatic', // 'automatic' or 'interactive'
      vistaSelectedClass: vistaClasses[0] || { name: 'liver', value: 1 }, // default to first class
      vistaClasses: vistaClasses, // store the array in state
      vistaPrompts: { points: [], labels: [] }, // To store 3D coordinates and labels
      vistaDrawingMode: false, // To track if we're in point placement mode
      vistaPointType: '+', // Current point type (+ or -)

      // nnInteractive model
      nnInteractivePointType: '+',
      nnInteractivePrompts: { points: [], labels: [], },
      nnInteractiveDrawingMode: false,
      resetInteractions:false,
      // prev mask
      prevMask2Refine:null,
      prevLabeL:[],
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

    // Remove the keydown event listener when the component is unmounted
    document.removeEventListener('keydown', this.handleKeyDown);
  }
  // Handle point type selection (+ or -) for nnInteractive
  handleNnInteractivePointSelection = (type) => {
    nnInteractivePoints = {};
    this.setState({
      nnInteractivePointType: type === 'positive' ? '+' : '-',
      nnInteractiveDrawingMode: true
    }, () => this.configureViewportInteraction('nninteractive'));

    console.log("#######################")
    console.log("in handleNnInteractivePointSelection")
    console.log("nnInteractivePoints", nnInteractivePoints)
    console.log("nnInteractivePrompts", this.state.nnInteractivePrompts)
    console.log("#######################")
  };

  //nnInteractivePointClick
  handleNnInteractivePointClick = (event) => {
    console.log("point clicked nnINteractive")
    const enabledElement = getActiveViewportEnabledElement(this.viewportGridService);
    if (!enabledElement) return;

    const { viewport } = enabledElement;
    const imageSliceData = csUtils.getImageSliceDataForVolumeViewport(viewport);
    const currentSlice = imageSliceData.imageIndex;
    const activeViewportId = viewport.id;

    // Get canvas coordinates
    const rect = viewport.element.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;


    console.log('Point coordinates:', {
      x, y,
      viewportRect: rect,
      viewportId: activeViewportId,
      slice: currentSlice
    });




    // Generate unique ID for this point
    const pointId = Date.now().toString();

    // Add point to nnInteractivePoints
    nnInteractivePoints[pointId] = {
      x, y, z: currentSlice,
      viewerID: activeViewportId,
      pointType: this.state.nnInteractivePointType,
      color: this.state.nnInteractivePointType === '+' ? '#4CAF50' : '#f44336'
    };
    // nnInteractivePoints[pointId]=volumePoint;


    // Convert to image coordinates and update prompts
    // const pointInImageCoords = this.mappPointToImageCoords(viewport.id, x, y, currentSlice);
    const renderingEngines = getRenderingEngines();
    const currentViewportObj = renderingEngines[0].getViewport(viewport.id);

    const { imageData } = currentViewportObj.getImageData();
    console.log("imageData", imageData)

    //map canvas coords x and y to world coords
    const worldCoords = currentViewportObj.canvasToWorld([x, y]);
    const ijk = vec3.fromValues(0, 0, 0);
    //mapp world coords to image
    const imageCoords = imageData.worldToIndex(worldCoords, ijk)
    // console.log("abdallah img coordss",imageCoords)

    // pointsnnInteractive=

    this.setState(prevState => ({
      nnInteractivePrompts: {
        points: [imageCoords],
        labels: [this.state.nnInteractivePointType === '+' ? 1 : 0],
        viewPort: viewport.id, // Store the viewport ID,
        // pointsMapped: [this.mapCoordinates(viewport.id, "mpr-axial", pointInImageCoords)]
      }
    }));

    // Update UI
    AutoSegmentation.updateNnInteractivePointDisplay(currentSlice, enabledElement);

    // Disable drawing mode after placing one point
    // Disable drawing mode after placing one point
    this.setState({ nnInteractiveDrawingMode: false });
    console.log("#######################")
    console.log("in handle nninteractive")
    console.log("nnInteractivePoints", nnInteractivePoints)
    console.log("nnInteractivePrompts", this.state.nnInteractivePrompts)
    console.log("#######################")
    // this.removeVistaPointEventListeners();  };
  }


  // Update nnInteractive point display in viewport
  static updateNnInteractivePointDisplay(sliceIndex, enabledElement) {

    if (!enabledElement) return;
    const { viewport } = enabledElement;

    // Clear existing points - more thorough removal
    const existingPoints = viewport.element.querySelectorAll('.nn-interactive-point');
    existingPoints.forEach(point => {
      // Ensure we're removing from the viewport element
      if (viewport.element.contains(point)) {
        viewport.element.removeChild(point);
      }
    });

    // Draw new points
    Object.entries(nnInteractivePoints).forEach(([pointId, pointData]) => {
      if (pointData.viewerID === viewport.id && pointData.z === sliceIndex) {
        const pointDiv = document.createElement('div');
        pointDiv.className = 'nn-interactive-point';
        pointDiv.style.position = 'absolute';
        pointDiv.style.left = `${pointData.x}px`;
        pointDiv.style.top = `${pointData.y}px`;

        if (pointData.pointType === '+') {
          pointDiv.style.width = '14px';
          pointDiv.style.height = '14px';
          pointDiv.style.backgroundColor = pointData.color;
          pointDiv.style.borderRadius = '50%';
          pointDiv.style.border = '2px solid white';
        } else {
          pointDiv.style.width = '10px';
          pointDiv.style.height = '10px';
          pointDiv.style.backgroundColor = pointData.color;
          pointDiv.style.transform = 'rotate(45deg)';
          pointDiv.style.border = '2px solid white';
        }

        viewport.element.appendChild(pointDiv);
      }
    });
  }
  // Handle removing the last nnInteractive point
  handleNnInteractivePopPoint = () => {
    this.setState(prevState => {
      // Get the last point ID
      const pointIds = Object.keys(nnInteractivePoints);
      if (pointIds.length === 0) return; // No points to remove

      const lastPointId = pointIds[pointIds.length - 1];
      delete nnInteractivePoints[lastPointId];

      const newNnInteractivePrompts = {
        points: prevState.nnInteractivePrompts.points.slice(0, -1)
      };

      return {
        nnInteractivePrompts: newNnInteractivePrompts
      };
    }, () => {
      // This callback runs after the state is updated
      const enabledElement = getActiveViewportEnabledElement(this.viewportGridService);
      if (enabledElement) {
        const { viewport } = enabledElement;
        const imageSliceData = csUtils.getImageSliceDataForVolumeViewport(viewport);
        const currentSlice = imageSliceData.imageIndex;
        AutoSegmentation.updateNnInteractivePointDisplay(currentSlice, enabledElement);
      }
    });
  };


  // Remove event listeners for nnInteractive points
  removeNnInteractivePointEventListeners() {
    const viewports = this.viewportGridService.getState().viewports;
    const renderingEngines = getRenderingEngines();

    viewports.forEach((viewportConfig) => {
      const viewport = renderingEngines[0].getViewport(viewportConfig.viewportId);
      viewport.element.removeEventListener('click', this.handleNnInteractivePointClick);
    });

    document.removeEventListener('keydown', this.handleEscKeyPress);
  }

  static clearNnInteractivePoints = () => {
    nnInteractivePoints = {};

    // Access the instance and clear its prompts state if needed
    if (AutoSegmentation.instance) {
      AutoSegmentation.instance.setState({
        nnInteractivePrompts: { points: [] }
      });
    }
  };
  //Handles the toggeling of modes in vista model
  handleVistaModeChange = (event) => {
    this.setState({
      vistaMode: event.target.value,
      vistaPrompts: { points: [], labels: [] },
      vistaDrawingMode: false
    });

    VistaPoints = {}; //clear UI components

    const enabledElement = getActiveViewportEnabledElement(this.viewportGridService);
    if (!enabledElement) return;

    const { viewport } = enabledElement;
    const imageSliceData = csUtils.getImageSliceDataForVolumeViewport(viewport);
    const currentSlice = imageSliceData.imageIndex;

    AutoSegmentation.updateVistaPointDisplay(currentSlice, enabledElement);
  };

  // Update your handleClassChange to store the selected class object
  handleClassChange = (event) => {
    const selectedName = event.target.value;
    const selectedClass = this.state.vistaClasses.find(cls => cls.name === selectedName);
    this.setState({ vistaSelectedClass: selectedClass });
    console.log("selectedName", selectedName)
    console.log("selectedClass", selectedClass)
    console.log("this.state.vistaSelectedClass", this.state.vistaSelectedClass)
  };

  // Handle point type selection (+ or -)
  handleVistaPointSelection = (type) => {
    this.setState({
      vistaPointType: type === 'positive' ? '+' : '-',
      vistaDrawingMode: true
    }, () => this.configureViewportInteraction('vista'));

    console.log("#######################")
    console.log("in handleVistaPointSelection")
    console.log("vistaPoints", VistaPoints)
    console.log("vistaPrompts", this.state.vistaPrompts)
    console.log("#######################")

  };


  // Handle actual point click
  handleVistaPointClick = (event) => {
    console.log("point clicked vista")
    const enabledElement = getActiveViewportEnabledElement(this.viewportGridService);
    if (!enabledElement) return;

    const { viewport } = enabledElement;
    const imageSliceData = csUtils.getImageSliceDataForVolumeViewport(viewport);
    const currentSlice = imageSliceData.imageIndex;
    const activeViewportId = viewport.id;

    // Get canvas coordinates
    const rect = viewport.element.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;

    console.log('Point coordinates:', {
      x, y,
      viewportRect: rect,
      viewportId: activeViewportId,
      slice: currentSlice
    });
    // Generate unique ID for this point
    const pointId = Date.now().toString();

    VistaPoints[pointId] = {
      x, y, z: currentSlice,
      viewerID: activeViewportId,
      pointType: this.state.vistaPointType,
      color: this.state.vistaPointType === '+' ? '#4CAF50' : '#f44336'
    };

    // Convert to image coordinates and update prompts
    const pointInImageCoords = this.mappPointToImageCoords(viewport.id, x, y, currentSlice);

    this.setState(prevState => ({
      vistaPrompts: {
        points: [...prevState.vistaPrompts.points, pointInImageCoords],
        labels: [...prevState.vistaPrompts.labels, this.state.vistaPointType === '+' ? 1 : 0]
      }
    }));

    // Update UI
    AutoSegmentation.updateVistaPointDisplay(currentSlice, enabledElement);

    // Disable drawing mode after placing one point
    this.setState({ vistaDrawingMode: false });

  };
  // Update point display in viewport
  static updateVistaPointDisplay(sliceIndex, enabledElement) {

    if (!enabledElement) return;
    const { viewport } = enabledElement;

    // Clear existing points - more thorough removal
    const existingPoints = viewport.element.querySelectorAll('.vista-point');
    existingPoints.forEach(point => {
      // Ensure we're removing from the viewport element
      if (viewport.element.contains(point)) {
        viewport.element.removeChild(point);
      }
    });

    // Draw new points
    Object.entries(VistaPoints).forEach(([pointId, pointData]) => {
      if (pointData.viewerID === viewport.id && pointData.z === sliceIndex) {
        const pointDiv = document.createElement('div');
        pointDiv.className = 'vista-point';
        pointDiv.style.position = 'absolute';
        pointDiv.style.left = `${pointData.x}px`;
        pointDiv.style.top = `${pointData.y}px`;

        if (pointData.pointType === '+') {
          pointDiv.style.width = '14px';
          pointDiv.style.height = '14px';
          pointDiv.style.backgroundColor = pointData.color;
          pointDiv.style.borderRadius = '50%';
          pointDiv.style.border = '2px solid white';
        } else {
          pointDiv.style.width = '10px';
          pointDiv.style.height = '10px';
          pointDiv.style.backgroundColor = pointData.color;
          pointDiv.style.transform = 'rotate(45deg)';
          pointDiv.style.border = '2px solid white';
        }

        viewport.element.appendChild(pointDiv);
      }
    });
  }
  // Update point display in viewport
  static updateVistaPointDisplay(sliceIndex, enabledElement) {

    if (!enabledElement) return;
    const { viewport } = enabledElement;

    // Clear existing points - more thorough removal
    const existingPoints = viewport.element.querySelectorAll('.vista-point');
    existingPoints.forEach(point => {
      // Ensure we're removing from the viewport element
      if (viewport.element.contains(point)) {
        viewport.element.removeChild(point);
      }
    });

    // Draw new points
    Object.entries(VistaPoints).forEach(([pointId, pointData]) => {
      if (pointData.viewerID === viewport.id && pointData.z === sliceIndex) {
        const pointDiv = document.createElement('div');
        pointDiv.className = 'vista-point';
        pointDiv.style.position = 'absolute';
        pointDiv.style.left = `${pointData.x}px`;
        pointDiv.style.top = `${pointData.y}px`;

        if (pointData.pointType === '+') {
          pointDiv.style.width = '14px';
          pointDiv.style.height = '14px';
          pointDiv.style.backgroundColor = pointData.color;
          pointDiv.style.borderRadius = '50%';
          pointDiv.style.border = '2px solid white';
        } else {
          pointDiv.style.width = '10px';
          pointDiv.style.height = '10px';
          pointDiv.style.backgroundColor = pointData.color;
          pointDiv.style.transform = 'rotate(45deg)';
          pointDiv.style.border = '2px solid white';
        }

        viewport.element.appendChild(pointDiv);
      }
    });
  }

  // Update point display in viewport
  static updateVistaPointDisplay(sliceIndex, enabledElement) {

    if (!enabledElement) return;
    const { viewport } = enabledElement;

    // Clear existing points - more thorough removal
    const existingPoints = viewport.element.querySelectorAll('.vista-point');
    existingPoints.forEach(point => {
      // Ensure we're removing from the viewport element
      if (viewport.element.contains(point)) {
        viewport.element.removeChild(point);
      }
    });

    // Draw new points
    Object.entries(VistaPoints).forEach(([pointId, pointData]) => {
      if (pointData.viewerID === viewport.id && pointData.z === sliceIndex) {
        const pointDiv = document.createElement('div');
        pointDiv.className = 'vista-point';
        pointDiv.style.position = 'absolute';
        pointDiv.style.left = `${pointData.x}px`;
        pointDiv.style.top = `${pointData.y}px`;

        if (pointData.pointType === '+') {
          pointDiv.style.width = '14px';
          pointDiv.style.height = '14px';
          pointDiv.style.backgroundColor = pointData.color;
          pointDiv.style.borderRadius = '50%';
          pointDiv.style.border = '2px solid white';
        } else {
          pointDiv.style.width = '10px';
          pointDiv.style.height = '10px';
          pointDiv.style.backgroundColor = pointData.color;
          pointDiv.style.transform = 'rotate(45deg)';
          pointDiv.style.border = '2px solid white';
        }

        viewport.element.appendChild(pointDiv);
      }
    });
  }
  // Remove event listeners
  removeVistaPointEventListeners() {
    const viewports = this.viewportGridService.getState().viewports;
    const renderingEngines = getRenderingEngines();

    viewports.forEach((viewportConfig) => {
      const viewport = renderingEngines[0].getViewport(viewportConfig.viewportId);
      viewport.element.removeEventListener('click', this.handleVistaPointClick);
    });

    document.removeEventListener('keydown', this.handleEscKeyPress);
  }
  // Handle removing the last VISTA point
  handleVistaPopPoint = () => {
    this.setState(prevState => {
      // Get the last point ID
      const pointIds = Object.keys(VistaPoints);
      if (pointIds.length === 0) return; // No points to remove

      const lastPointId = pointIds[pointIds.length - 1];
      delete VistaPoints[lastPointId];

      const newVistaPrompts = {
        points: prevState.vistaPrompts.points.slice(0, -1),
        labels: prevState.vistaPrompts.labels.slice(0, -1)
      };

      return {
        // vistaPoints: newVistaPoints,
        vistaPrompts: newVistaPrompts
      };
    }, () => {
      // This callback runs after the state is updated
      const enabledElement = getActiveViewportEnabledElement(this.viewportGridService);
      if (enabledElement) {
        const { viewport } = enabledElement;
        const imageSliceData = csUtils.getImageSliceDataForVolumeViewport(viewport);
        const currentSlice = imageSliceData.imageIndex;
        AutoSegmentation.updateVistaPointDisplay(currentSlice, enabledElement);
      }
    });
  };
  static clearVistaPoints = () => {
    VistaPoints = {};

    // Access the instance and clear its prompts state if needed
    if (AutoSegmentation.instance) {
      AutoSegmentation.instance.setState({
        vistaPrompts: { points: [], labels: [] }
      });
    }
  };
  // Helper function to calculate image embeddings
  // static async calculateImageEmbeddings(checkModel = false) {
  //   console.log("calculateImageEmbeddings Entered...");
  //   if (!AutoSegmentation.instance) {
  //     console.error('AutoSegmentation instance is not available.');
  //     return;
  //   }

  //   //Get class instance (as this function is global)
  //   const instance = AutoSegmentation.instance;

  //   // Check if embeddings are already being calculated
  //   if (instance.state.isCalculatingEmbeddings) {
  //     console.log("Embeddings calculation is already in progress.");
  //     return;

  //   } else {

  //     let isCalculate = true
  //     console.log("Calculating image embeddings...");


  //     if (checkModel) {

  //       //Get current selected model in model bank
  //       const model = instance.state.currentModel;
  //       console.log("model...", model)

  //       isCalculate = (model == 'sam');
  //     }

  //     // If sam then calc volume embeddings
  //     if (isCalculate) {
  //       console.log("Enter isCalculate block...");

  //       // Set the state to indicate that embeddings are being calculated
  //       instance.setState({ isCalculatingEmbeddings: true });

  //       try {
  //         await instance.sam2CalcImageEmbeddings('sam');
  //       } catch (error) {
  //         console.error('Error calculating image embeddings:', error);
  //       } finally {
  //         // Reset the state after calculation is complete
  //         instance.setState({ isCalculatingEmbeddings: false });
  //       }
  //     }
  //     else {
  //     }
  //   }
  // };

  /*
  Global function to clear dictionaries carrying sam2 ui compontets: points and boxes
  Is called when the study is changed in studylistPagination file.
  */
  static clearSam2UIContrainers = () => {
    // Clear global variables
    Point_ui = {};
    Box = {};

    // Access the instance and clear its prompts state
    if (AutoSegmentation.instance) {
      AutoSegmentation.instance.setState({ prompts: {} });
      AutoSegmentation.instance.setState({ volumeEmbeddingsCalculated: false });
    }
  };

  /*
   Helper function to generate a unique color for each object for sam2
  */
  generateUniqueColor(objectId) {
    const colors = [
      '#FF4C4C',
      '#37bb2dff',
      '#9F47E0',
      '#D5DD45',
      '#47B3A9',
      '#f5a8cfff',
      '#a6f13cff',
  
    ];
    return colors[(objectId - 1) % colors.length]; // Cycle through colors
  }

  /*
   sam2 handler function to handle add object button click to add the ui components for new object
  */
  handleAddObject = () => {
    console.log(" this.state.prompts....", this.state.prompts)

    //increment the object id by 1 from the last added object
    const objectId = Object.keys(this.state.prompts).length + 1; // Unique ID based on the number of objects

    //create key in propmts dictionary with the new id
    this.state.prompts[objectId] = {
      points: [[], []],
      pointType: '+',
      boxes: [],
    };
  //       const newPrompt = {
  //     ...this.state.prompts,
  //     [objectId]: {
  //       points: [[], []],
  //       pointType: '+',
  //       boxes: [],
  //     }
  //   };
  // this.setState({ prompts: newPrompt });

    
    // Generate a unique color
    const color = this.generateUniqueColor(objectId); // Generate a unique color

    const enabledElement = getActiveViewportEnabledElement(
      this.viewportGridService
    );

    const { viewport } = enabledElement;
    const imageSliceData = csUtils.getImageSliceDataForVolumeViewport(viewport);
    this.maxSlicesAxial = imageSliceData.numberOfSlices;

    // Dynamically update the UI components for this object
    this.setState((prevState) => ({
      prompts: { ...prevState.prompts }, // Update prompts
      objectColors: { ...prevState.objectColors, [objectId]: color }, // Assign color
      uiComponents: [
        // Add a new UI component for the object
        ...prevState.uiComponents,
        {
          objectId,
          label: `${objectId}:`,// label carrying the id

          //point and box buttons to handle prompts
          buttons: [
            { buttonText: 'Pnt' },
            { buttonText: 'Box' },
          ],
          radioBtnName: `Radio for Object ${objectId}`, //radiobtn to detect wheather +ve or -ve point
        },
      ],
    }));
    console.log(" this.state.prompts 222", this.state.prompts)

  };

  /*
   sam2 handler function to handle selected radiobtn for point type(+ve , -ve)
  */
  handleRadioChange = (event, objectId) => {
    this.setState((prevState) => ({
      selectedPointType: event.target.value,
      prompts: {
        ...prevState.prompts,
        [objectId]: {
          ...prevState.prompts[objectId],
          pointType: event.target.value,
        },
      },
    }));
  };

  /*
   Handle the "Point" button click for the specific object
  */
  handleAddPoint = (objectId) => {

    //activate drawing mode to point and enable the event listeners for mouse press
    // by calling configureViewportInteraction
    this.setState({ pointObj: objectId, drawingMode: 'point' }, () =>
      this.configureViewportInteraction()
    );
  };

  /*
  Handle the "Box" button click for the specific object
  */
  handleAddBox = (objectId) => {

    //activate drawing mode to box and enable the event listeners for mouse moves
    // by calling configureViewportBoxInteraction
    this.setState({ pointObj: objectId, drawingMode: 'box' }, () =>
      this.configureViewportBoxInteraction()
    );
  };

  /*
   Enable the event listeners for mouse moves for box drawing
  */
  configureViewportBoxInteraction() {
    if (!this.viewportGridService) {
      console.error('viewportGridService is not available.');
      return;
    }

    const viewports = this.viewportGridService.getState().viewports;
    const renderingEngines = getRenderingEngines();

    // Add keydown event listener for Esc key
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        this.handleEscKeyPress();
      }
    };

    //loop over 3 viewports
    viewports.forEach((viewportConfig) => {
      const viewport = renderingEngines[0].getViewport(
        viewportConfig.viewportId
      );
      //for version 2 box only on axial although in version 1 box in 3 orientation
      const imageSliceData =
        csUtils.getImageSliceDataForVolumeViewport(viewport);

      if (this.state.drawingMode === 'box') {
        viewport.element.addEventListener('mousedown', this.handleBoxMouseDown);
        viewport.element.addEventListener('mouseup', this.handleBoxMouseUp);
        viewport.element.addEventListener('mousemove', this.handleBoxMouseMove);
        viewport.element.removeEventListener('click', this.handleSam2Point);
        document.addEventListener('keydown', handleKeyDown);

      } else {
        viewport.element.removeEventListener('mousedown', this.handleBoxMouseDown);
        viewport.element.removeEventListener('mouseup', this.handleBoxMouseUp);
        viewport.element.removeEventListener('mousemove', this.handleBoxMouseMove);
        document.removeEventListener('keydown', handleKeyDown);

      }
      // }
    });
  }

  /*
  Handle the box drawing discard if esc key is pressed from the keyboard
  */
  handleEscKeyPress = async () => {

    const { drawingMode } = this.state;

    console.log("this.state.currentModel", this.state.currentModel)
    console.log("this.state.vistaDrawingMode", this.state.vistaDrawingMode)

    if (this.state.currentModel === 'vista') {
      // Handle VISTA point drawing discard
      this.setState({ vistaDrawingMode: false });
      this.removeVistaPointEventListeners();
      return;
    }
    if (drawingMode === 'box' && this.isDrawing) {

      // Handle box drawing discard
      const enabledElement = getActiveViewportEnabledElement(this.viewportGridService);
      if (!enabledElement) return;

      const { viewport } = enabledElement;

      // Remove the temporary box from the UI
      const tempBoxDiv = viewport.element.querySelector('.temp-box');
      if (tempBoxDiv) tempBoxDiv.remove();

      // Reset the drawing state
      this.setState({ tempBox: null, drawingMode: 'none' });
      this.isDrawing = false;

    } else if (drawingMode === 'point') {

      // Handle point drawing discard
      this.setState({ drawingMode: 'none' });

      // Remove point drawing event listeners
      this.removePointEventListeners();
    }
  };

  removePointEventListeners = () => {
    const viewports = this.viewportGridService.getState().viewports;
    const renderingEngines = getRenderingEngines();

    viewports.forEach((viewportConfig) => {
      const viewport = renderingEngines[0].getViewport(viewportConfig.viewportId);
      viewport.element.removeEventListener('click', this.handleSam2Point);
    });

    // Remove the keydown event listener for ESC key
    document.removeEventListener('keydown', this.handleEscKeyPress);
  }

  /*
  Handle the mouse down event for box
  */
  handleBoxMouseDown = (event) => {

    if (this.state.drawingMode !== 'box') return;

    const enabledElement = getActiveViewportEnabledElement(
      this.viewportGridService
    );

    if (!enabledElement) return;

    const { viewport } = enabledElement;
    const imageSliceData = csUtils.getImageSliceDataForVolumeViewport(viewport);
    this.currentSlice = imageSliceData.imageIndex;

    const existingBox = this.state.boxes[this.currentSlice];
    if (existingBox) {
      console.log('Replacing the existing box for this slice.');
    }

    const rect = viewport.element.getBoundingClientRect();
    const startX = event.clientX - rect.left;
    const startY = event.clientY - rect.top;

    this.startPoint = { x: startX, y: startY };
    this.isDrawing = true;

  };

  /*
    Handle the mouse up event for box where the box is created
  */
  handleBoxMouseUp = (event) => {

    // to prevent drawing a box when drawing a point
    if (this.state.drawingMode !== 'box' || !this.isDrawing) return;

    const enabledElement = getActiveViewportEnabledElement(
      this.viewportGridService
    );
    if (!enabledElement) return;

    //tempBox is the dashed box drawn until mouse is up
    const { tempBox } = this.state;
    if (!tempBox) return;

    const { viewport } = enabledElement;

    // Convert to relative coordinates and store the finalized box
    const rect = viewport.element.getBoundingClientRect(); // Get viewport dimensions

    const objectId = this.state.pointObj;

    const imageSliceData = csUtils.getImageSliceDataForVolumeViewport(viewport);
    this.currentSlice = imageSliceData.imageIndex;
    const SlicingMap = {
      'mpr-axial': 2,
      'mpr-sagittal': 0,
      'mpr-coronal': 1,
    };

    //mapp the 2 corers to image coords to be sent to monailabel
    const point1InImageCoords = this.mappPointToImageCoords(viewport.id, tempBox.x1, tempBox.y1, this.currentSlice)
    const point2InImageCoords = this.mappPointToImageCoords(viewport.id, tempBox.x2, tempBox.y2, this.currentSlice)
    // const mappedBox = [point1InImageCoords[0], point1InImageCoords[1], point2InImageCoords[0], point2InImageCoords[1]]
    const mappedBox = [
      Math.min(point1InImageCoords[0], point2InImageCoords[0]),
      Math.min(point1InImageCoords[1], point2InImageCoords[1]),
      Math.max(point1InImageCoords[0], point2InImageCoords[0]),
      Math.max(point1InImageCoords[1], point2InImageCoords[1]),
    ];

    // Directly use the tempBox coordinates relative to the viewport's size
    const finalizedBox = [
      [tempBox.x1, tempBox.y1, tempBox.x2, tempBox.y2], // Coordinates
      this.currentSlice, // Slice index
      viewport.id, // Viewport ID
      this.state.objectColors[this.state.pointObj] // Color from the selected object
    ];

    if (!Box[objectId]) {
      Box[objectId] = [];
    }

    // Overwrite if box exists in the same slice, else add a new one
    const existingIndex = Box[objectId].findIndex(box => box[1] === this.currentSlice);

    if (existingIndex !== -1) {
      Box[objectId][existingIndex] = finalizedBox;
    } else {
      Box[objectId].push(finalizedBox);
    }

    const PromptsBox = [
      [
        mappedBox[0],
        mappedBox[1], // Top-left corner
        mappedBox[2],
        mappedBox[3],// Bottom-right corner
      ],
      this.currentSlice,
      SlicingMap[viewport.id]
    ]
    //Add to prompts dic only of main box (not auxillary from sagittal or coronal)
    if (viewport.id == 'mpr-axial') {

      this.setState((prevState) => {
        const updatedPrompts = { ...prevState.prompts };
        if (updatedPrompts[objectId]["boxes"][0]) {
          const existingIndex = updatedPrompts[objectId]["boxes"][0].findIndex(box => box[1] === this.currentSlice);
          console.log("updatedPrompts[objectId][boxes][0]", updatedPrompts[objectId]["boxes"][0]);
          console.log("existingIndex", existingIndex)

          if (existingIndex !== -1) {
            updatedPrompts[objectId].boxes[0][existingIndex] = PromptsBox;
          } else {
            updatedPrompts[objectId].boxes[0].push(PromptsBox);
          }

        }
        else {
          updatedPrompts[objectId]["boxes"].push([PromptsBox]);

        }
        return {
          tempBox: null,
          prompts: updatedPrompts,
        };
      });
      console.log("updated Boxes", Box)
      console.log('updated prompts', this.state.prompts);
    }
    else {
      this.setState((prevState) => {
        return {
          tempBox: null,
        };
      });

    }

    // Finalize the drawn box and remove the temporary one
    const tempBoxDiv = viewport.element.querySelector('.temp-box');
    if (tempBoxDiv) tempBoxDiv.remove();

    //update the ui
    AutoSegmentation.updateBoxDisplay(this.currentSlice, enabledElement);

    //Segment 2d
    if (viewport.id == 'mpr-axial') {
      this.sam2Onsegmentation2D(this.currentSlice);
    }
    else {
      this.sam2Onsegmentation2D(this.currentSlice, PromptsBox, objectId);
    }


    //disable drawing
    this.isDrawing = false;
  };

  /*
   Handle the the mouse move for temp(dashed) box drawing
  */
  handleBoxMouseMove = (event) => {
    // Prevent drawing a box when drawing a point
    if (this.state.drawingMode !== 'box' || !this.isDrawing) return;

    const enabledElement = getActiveViewportEnabledElement(
      this.viewportGridService
    );
    if (!enabledElement) return;

    const { viewport } = enabledElement;
    const rect = viewport.element.getBoundingClientRect();

    const currentX = event.clientX - rect.left;
    const currentY = event.clientY - rect.top;

    const tempBox = {
      x1: Math.min(this.startPoint.x, currentX),
      y1: Math.min(this.startPoint.y, currentY),
      x2: Math.max(this.startPoint.x, currentX),
      y2: Math.max(this.startPoint.y, currentY),
    };

    //keep track of temp box to be used when mouse up
    this.setState({ tempBox });
    this.HandleTempBoxDisplay(viewport, tempBox, 'temp-box', 'blue', 'dashed');

  };

  /*
   Handle the dashed(temp) box drawing in the ui for sam2
  */
  HandleTempBoxDisplay(viewport, box, className, color, style) {

    // Remove existing box of the same class
    const existingBox = viewport.element.querySelector(`.${className}`);

    if (existingBox) existingBox.remove();

    // Add new box
    if (box) {
      const boxDiv = document.createElement('div');
      boxDiv.className = className;
      boxDiv.style.position = 'absolute';
      boxDiv.style.border = `2px ${style} ${color}`;
      boxDiv.style.left = `${box.x1}px`;
      boxDiv.style.top = `${box.y1}px`;
      boxDiv.style.width = `${box.x2 - box.x1}px`;
      boxDiv.style.height = `${box.y2 - box.y1}px`;
      viewport.element.appendChild(boxDiv);
    }
  }

  /*
    Handle the finalized box drawing in the UI for sam2.
    Global function to be called to refresh the UI whenever the sliceIndex changes.
  */
  static updateBoxDisplay = (sliceIndex, enabledElement) => {
    if (!enabledElement) return;

    const { viewport } = enabledElement;

    // Remove existing displayed boxes
    const existingBoxes = viewport.element.querySelectorAll('.finalized-box');
    existingBoxes.forEach((box) => box.remove());

    if (Box) {
      // Loop over the global Box variable to find matching boxes
      Object.entries(Box).forEach(([objId, boxs]) => {
        // Iterate over the array of boxes for the current objId
        boxs.forEach(([box, boxSliceIndex, boxViewportID, boxColor]) => {
          // Check if the box matches the given viewportID and sliceIndex
          if (boxViewportID === viewport.id && boxSliceIndex === sliceIndex) {
            const boxDiv = document.createElement('div');
            boxDiv.className = 'finalized-box';
            boxDiv.style.position = 'absolute';
            boxDiv.style.border = `2px solid ${boxColor}`;
            boxDiv.style.left = `${box[0]}px`;
            boxDiv.style.top = `${box[1]}px`;
            boxDiv.style.width = `${box[2] - box[0]}px`;
            boxDiv.style.height = `${box[3] - box[1]}px`;
            viewport.element.appendChild(boxDiv);
          }
        });
      });
    }
  };


  renderFinalizedBox(viewport, box) {
    if (!box) return;
    // Render the single finalized box
    const boxDiv = document.createElement('div');
    boxDiv.className = 'finalized-box';
    boxDiv.style.position = 'absolute';
    boxDiv.style.border = '2px solid red';
    boxDiv.style.left = `${box.x1}px`;
    boxDiv.style.top = `${box.y1}px`;
    boxDiv.style.width = `${box.x2 - box.x1}px`;
    boxDiv.style.height = `${box.y2 - box.y1}px`;
    viewport.element.appendChild(boxDiv);
  }

  // Auxillary Function to keep track of the missing boxes in the 3 viewports .
  checkBoxesInAllViews() {
    const objectIds = Object.keys(Box);
    const missingViews = {};

    for (const objectId of objectIds) {
      const boxes = Box[objectId] || [];
      const hasAxial = boxes.some(box => box[2] === 'mpr-axial');
      const hasSagittal = boxes.some(box => box[2] === 'mpr-sagittal');
      const hasCoronal = boxes.some(box => box[2] === 'mpr-coronal');

      const hasEnoughViews = (hasSagittal || hasCoronal);

      if (!hasEnoughViews) {
        missingViews[objectId] = {
          sagittal: !hasSagittal && !hasCoronal, // Only show if both are missing
          coronal: !hasCoronal && !hasSagittal   // Only show if both are missing
        };
      }
    }

    const missingBoxes = Object.keys(missingViews).length === 0 ? null : missingViews;


    if (missingBoxes) {
      // Create warning message
      let warningMessage = "For optimal segmentation quality, please draw boxes in axial plus at least one other view (sagittal or coronal) for the following objects:\n";

      Object.entries(missingBoxes).forEach(([objectId, views]) => {
        warningMessage += `\nObject ${objectId} is missing boxes in:`;
        if (views.axial) warningMessage += " Axial,";
        if (views.sagittal) warningMessage += " Sagittal/Coronal,";
        warningMessage = warningMessage.slice(0, -1); // Remove trailing comma
      });

      warningMessage += "\n\nClick Continue to proceed with current boxes or Cancel to add more boxes.";

      // Create a custom modal
      const modal = document.createElement('div');
      modal.style.position = 'fixed';
      modal.style.top = '0';
      modal.style.left = '0';
      modal.style.width = '100%';
      modal.style.height = '100%';
      modal.style.backgroundColor = 'rgba(0,0,0,0.5)';
      modal.style.display = 'flex';
      modal.style.justifyContent = 'center';
      modal.style.alignItems = 'center';
      modal.style.zIndex = '1000';

      const modalContent = document.createElement('div');
      modalContent.style.backgroundColor = 'gray';
      modalContent.style.padding = '20px';
      modalContent.style.borderRadius = '8px';
      modalContent.style.maxWidth = '500px';

      const message = document.createElement('p');
      message.textContent = warningMessage;
      message.style.whiteSpace = 'pre-line';
      message.style.marginBottom = '20px';

      const buttonContainer = document.createElement('div');
      buttonContainer.style.display = 'flex';
      buttonContainer.style.justifyContent = 'flex-end';
      buttonContainer.style.gap = '10px';

      const cancelButton = document.createElement('button');
      cancelButton.textContent = 'Cancel';
      cancelButton.style.padding = '8px 16px';
      cancelButton.style.backgroundColor = '#f44336';
      cancelButton.style.color = 'white';
      cancelButton.style.border = 'none';
      cancelButton.style.borderRadius = '4px';
      cancelButton.style.cursor = 'pointer';

      const continueButton = document.createElement('button');
      continueButton.textContent = 'Continue';
      continueButton.style.padding = '8px 16px';
      continueButton.style.backgroundColor = '#4CAF50';
      continueButton.style.color = 'white';
      continueButton.style.border = 'none';
      continueButton.style.borderRadius = '4px';
      continueButton.style.cursor = 'pointer';

      // Handle button clicks
      return new Promise((resolve) => {
        cancelButton.onclick = () => {
          document.body.removeChild(modal);
          resolve(false);
        };

        continueButton.onclick = () => {
          document.body.removeChild(modal);
          resolve(true);
        };

        buttonContainer.appendChild(cancelButton);
        buttonContainer.appendChild(continueButton);

        modalContent.appendChild(message);
        modalContent.appendChild(buttonContainer);

        modal.appendChild(modalContent);
        document.body.appendChild(modal);
      });
    }

    // If no missing boxes, return a resolved Promise with true
    return Promise.resolve(true);
  }

  componentDidUpdate(_, prevState) {
    if (prevState.boxes !== this.state.boxes) {
      const enabledElement = getActiveViewportEnabledElement(
        this.viewportGridService
      );
      if (enabledElement) {
        const { viewport } = enabledElement;
        const imageSliceData =
          csUtils.getImageSliceDataForVolumeViewport(viewport);
        this.currentSlice = imageSliceData.imageIndex;
        const box = this.state.boxes[this.currentSlice];
        this.renderFinalizedBox(viewport, box);
      }
    }
  }

  /*
    Handle the pop point action for sam2
  */
  handlePopPoint = (objectId) => {
    console.log('Popping point for object:', objectId);
    let poppedPoint = null;

    this.setState((prevState) => {
      console.log('point_ui :', Point_ui);
      console.log("prompts", this.state.prompts);
      if (
        !Point_ui[objectId]) {
        console.log('No points to pop');
        return null;
      }
      const prompts = { ...prevState.prompts };
      const objectPrompts = prompts[objectId];

      if (objectPrompts.points[0].length > 0) {
        // Remove the last point from the object's prompts
        objectPrompts.points[0].pop();
        objectPrompts.points[1].pop();

        // Remove the last point for the object on the current slice
        poppedPoint = Point_ui[objectId].pop();
        console.log("poppedPoint", poppedPoint)
      }
      const { element } = OHIFgetEnabledElement(poppedPoint["viewerID"]) || {};
      const enabledElement = getEnabledElement(element);

      // Remove slice entry if it becomes empty
      if (Point_ui[objectId].length === 0) {
        // Check if there are any other slices with points for this object in this viewport
        delete Point_ui[objectId];
      }
      console.log('Updated Point_ui after pop:', Point_ui);
      console.log('Updated Prompts after pop:', this.state.prompts);

      AutoSegmentation.updatePointDisplay(poppedPoint["z"], enabledElement);
      return null;
    });
  };

  /*
   Enable the event listeners for mouse click for point drawing in sam and vista models
  */
  configureViewportInteraction(model = 'sam') {

    if (!this.viewportGridService) {
      console.error('viewportGridService is not available.');
      return;
    }

    const viewports = this.viewportGridService.getState().viewports;

    const renderingEngines = getRenderingEngines();

    // Add keydown event listener for Esc key
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        this.handleEscKeyPress();
      }
    };

    viewports.forEach((viewportConfig) => {
      const viewport = renderingEngines[0].getViewport(
        viewportConfig.viewportId
      );
      const imageSliceData =
        csUtils.getImageSliceDataForVolumeViewport(viewport);

      this.maxPortSlicesNumber[viewportConfig.viewportId] =
        imageSliceData.numberOfSlices;

      if (model == 'sam') {
        if (Object.keys(this.state.prompts).length != 0) {
          viewport.element.addEventListener('click', this.handleSam2Point);
          document.addEventListener('keydown', handleKeyDown);
        } else {
          viewport.element.removeEventListener('click', this.handleSam2Point);
          document.removeEventListener('keydown', handleKeyDown);
        }
      }
      else if (model == 'vista') {
        console.log("vistaDrawingMode", this.state.vistaDrawingMode)
        if (this.state.vistaDrawingMode) {
          viewport.element.addEventListener('click', this.handleVistaPointClick);
          document.addEventListener('keydown', handleKeyDown);
        } else {
          viewport.element.removeEventListener('click', this.handleVistaPointClick);
          document.removeEventListener('keydown', handleKeyDown);
        }
      }
      else if (model == 'nninteractive') {
        console.log("nnInteractiveDrawingMode", this.state.nnInteractiveDrawingMode)
        if (this.state.nnInteractiveDrawingMode) {
          viewport.element.addEventListener('click', this.handleNnInteractivePointClick);
          document.addEventListener('keydown', handleKeyDown);
        } else {
          viewport.element.removeEventListener('click', this.handleNnInteractivePointClick);
          document.removeEventListener('keydown', handleKeyDown);
        }
      }
    });
  }

  /*
    Auxillary function that map point from any orientaion to another
  */
  mapCoordinates = (currentPlane, targetPlane, point) => {

    const Point = {
      'x': point[0],
      'y': point[1],
      'z': point[2]
    }

    if (currentPlane === targetPlane) {
      return point; // No conversion needed
    }

    // Define plane mappings
    const mappedPoints = {
      "mpr-axial": {
        "mpr-coronal": [Point['x'], Point['z'], Math.round(Point['y'])],
        "mpr-sagittal": [Point['z'], Point['y'], Math.round(Point['x'])],
      },
      "mpr-sagittal": {
        "mpr-axial": [Point['x'], Point['z'], Math.round(Point['y'])],
        "mpr-coronal": [Point['y'], Point['z'], Math.round(Point['x'])],
      },
      "mpr-coronal": {
        "mpr-axial": [Point['z'], Point['y'], Math.round(Point['x'])],
        "mpr-sagittal": [Point['z'], Point['x'], Math.round(Point['y'])],
      },
    };

    if (!mappedPoints[currentPlane] || !mappedPoints[currentPlane][targetPlane]) {
      console.log(`Invalid plane mapping from ${currentPlane} to ${targetPlane}`);
    }

    // Get the axis remapping
    const newPoint = mappedPoints[currentPlane][targetPlane];

    return newPoint;
  }

  /*
   Function to mapp the coords retrieved from mouse click event to the image coords
  */
  mappPointToImageCoords = (curretViewportId, x, y, sliceIndex) => {
    console.log("Point before conversion....", [x, y, sliceIndex]);

    const renderingEngines = getRenderingEngines();
    const currentViewportObj = renderingEngines[0].getViewport(curretViewportId);

    const { imageData } = currentViewportObj.getImageData();
    console.log("imageData", imageData)

    //map canvas coords x and y to world coords
    const worldCoords = currentViewportObj.canvasToWorld([x, y]);
    const ijk = vec3.fromValues(0, 0, 0);

    //mapp world coords to image
    const imageCoords = imageData.worldToIndex(worldCoords, ijk)
    console.log("Point after conversion to image coords", imageCoords)

    let newPoint = [0, 0, 0]

    if (curretViewportId == 'mpr-axial') {
      newPoint = [imageCoords[1], imageCoords[0], sliceIndex]
    }
    else if (curretViewportId == 'mpr-sagittal') {
      newPoint = [imageCoords[2], imageCoords[1], sliceIndex]

    }
    else if (curretViewportId == 'mpr-coronal') {
      newPoint = [imageCoords[2], imageCoords[0], sliceIndex]

    }
    console.log("newPoint", newPoint)
    return newPoint
  }


  /*
  Handle sam2 mouse click event for point prompt
  */
  handleSam2Point = (event) => {

    //Get point type (=ve , -ve)
    const { pointObj } = this.state;
    const selectedPointType = this.state.prompts[pointObj]?.pointType;

    const enabledElement = getActiveViewportEnabledElement(
      this.viewportGridService
    );
    const { viewport } = enabledElement;

    const imageSliceData = csUtils.getImageSliceDataForVolumeViewport(viewport);

    //Get the coords x and y where mouse click occurs with respect to the canvas of the viewport
    const rect = viewport.element.getBoundingClientRect();
    let x = event.clientX - rect.left;
    let y = event.clientY - rect.top;


    this.currentSlice = imageSliceData.imageIndex;
    const activeViewportId = viewport.id;

    // Get the color for this object
    const color = this.state.objectColors[pointObj];
    let viewPortFlag = true

    const hasExistingPointsOrBoxes = (Point_ui[pointObj] ? true : false) || (Box[pointObj] ? true : false);

    console.log("Point_ui[pointObj]", Point_ui[pointObj])
    console.log("hasExistingPointsOrBoxes", hasExistingPointsOrBoxes)


    // Check viewerID consistency where code is build on assumption that the main segmentation in in axial althought
    // coronal and sagittal only for refinemenet
    if (Point_ui) {

      if (activeViewportId != 'mpr-axial' && !hasExistingPointsOrBoxes) {
        const nid = this.notification.show({
          title: 'SAM',
          message: 'Coronal and Sagittal for mask enhancements only.',
          type: 'error',
          duration: 3000,
        });
        viewPortFlag = false
        return;
      }

    }

    //check on viewerId consistency
    if (viewPortFlag) {

      //Store canvas coords in global dictionary for ui
      if (!Point_ui[pointObj])
        Point_ui[pointObj] = [];

      Point_ui[pointObj].push({
        x: x,
        y: y,
        z: this.currentSlice,
        viewerID: activeViewportId,
        color,
        pointType: selectedPointType,
      });

      //update the ui
      AutoSegmentation.updatePointDisplay(this.currentSlice, enabledElement);

      // Update the state for prompts
      this.setState((prevState) => {
        const newPrompts = { ...prevState.prompts };
        if (!newPrompts[pointObj]) {
          newPrompts[pointObj] = [[], []];
        }
        const pointValue = selectedPointType === '+' ? 1 : 0;
        console.log("First point", [x, y, this.currentSlice])

        //get coords in image
        const pointInImageCoords = this.mappPointToImageCoords(viewport.id, x, y, this.currentSlice)

        console.log("pointInImageCoords2", pointInImageCoords)

        const SlicingMap = {
          'mpr-axial': 2,
          'mpr-sagittal': 0,
          'mpr-coronal': 1,
        };
        newPrompts[pointObj].points[0].push([
          pointInImageCoords[0],
          pointInImageCoords[1],
          pointInImageCoords[2],
          2
        ]);
        newPrompts[pointObj].points[1].push(pointValue);

        return { prompts: newPrompts };
      });

      //segment in 2d with mouse clicks
      this.sam2Onsegmentation2D(this.currentSlice);

    }
  };

  /*
Update the ui based on sam2 points
*/
  static updatePointDisplay(sliceIndex, enabledElement) {
    if (!enabledElement) {
      return;
    }
    const { viewport } = enabledElement;

    // Clear existing points
    const existingPoints = viewport.element.querySelectorAll('.point');
    existingPoints.forEach((point) => point.remove());

    // Draw the point if it exists for the current slice
    if (Point_ui) {
      Object.entries(Point_ui).forEach(([objectId, points]) => {
        const relevantPoints = points.filter(
          ({ viewerID, z }) => viewerID === viewport.id && z === sliceIndex
        );
        relevantPoints.forEach(({ x, y, color, pointType }) => {
          console.log("POINT DRAW in the loop");
          if (x !== undefined && y !== undefined) {
            const pointContainer = document.createElement('div');
            pointContainer.className = 'point'; // Add a class for easy selection
            pointContainer.style = `
            position: absolute;
            left: ${x}px;
            top: ${y}px;
            display: flex;
            align-items: center;
            justify-content: center;
            pointer-events: none; /* Disable interaction */
          `;

            const shapeDiv = document.createElement('div');
            shapeDiv.style = `
            position: relative;
            display: flex;
            justify-content: center;
            align-items: center;
          `;

            // Visualize the positive and negative points differently
            if (pointType === '+') {
              // Positive point: Smaller glowing orb
              shapeDiv.style = `
              width: 14px;
              height: 14px;
              background-color: ${color || 'green'};
              border-radius: 50%; /* Circle shape */
              box-shadow: 0px 0px 6px rgba(0, 255, 0, 0.7); /* Glow effect */
              border: 2px solid white;
              display: flex;
              justify-content: center;
              align-items: center;
            `;
            } else if (pointType === '-') {
              // Negative point: Smaller Rhombus (diamond) shape with a white border
              const rhombusShape = document.createElement('div');
              rhombusShape.style = `
              width: 10px; /* Smaller size for the rhombus */
              height: 10px;
              background-color: ${color || 'red'}; /* Diamond color */
              transform: rotate(45deg); /* Rotate to form a rhombus */
              box-shadow: 0px 0px 6px rgba(255, 0, 0, 0.5); /* Shadow for depth */
              border: 2px solid white; /* White border around the rhombus */
            `;
              shapeDiv.appendChild(rhombusShape);
            }

            // Create the label for the sign (+ or -), positioned directly under the shape with 1px space
            const labelDiv = document.createElement('div');
            labelDiv.textContent = pointType; // Add the sign as text
            labelDiv.style = `
            color: black;
            font-weight: ${pointType === '-' ? 'bold' : 'normal'}; /* Bold for - */
            font-size: ${pointType === '-' ? '16px' : '12px'}; /* Larger label for - */
            position: absolute;
            top: 100%; /* Position directly under the point */
            margin-top: 1px; /* 1px space between point and label */
            z-index: 1; /* Ensure the label is above the point */
          `;

            // Append the shape and label to the point container
            pointContainer.appendChild(shapeDiv);
            // pointContainer.appendChild(labelDiv);
            viewport.element.appendChild(pointContainer);
          }
        });
      });
    }
  }

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
    console.log("iam here y shahd changing the model to ", model)
    Point_ui = {};
    Box = {};
    VistaPoints = {};
    nnInteractivePoints = {}

    this.setState(
      {
        currentModel: model
      },
      // () => {
        
      //   const enabledElement = getActiveViewportEnabledElement(this.viewportGridService);
      //   if (enabledElement) {
      //     const { viewport } = enabledElement;
      //     const imageSliceData = csUtils.getImageSliceDataForVolumeViewport(viewport);
      //     const currentSlice = imageSliceData.imageIndex;

          
      //     this.updatePointDisplay(currentSlice, enabledElement);
      //     this.updateBoxDisplay(currentSlice, enabledElement);

         
      //     this.updateVistaPointDisplay(currentSlice, enabledElement);

          
      //     this.updateNnInteractivePointDisplay(currentSlice, enabledElement);

      //     // this.props.updateView([], [], model); //null response to clear masks
      //   }

      //   this.removePointEventListeners();
      //   this.removeVistaPointEventListeners();
      //   this.removeNnInteractivePointEventListeners();
      // }
    );
    // console.log("notification appears")
    // this.notification.show({
    //   title: 'Model Changed',
    //   message: `Switched to ${model} model. All previous segmentation prompts have been cleared.`,
    //   type: 'info',
    //   duration: 3000,
    // });
  };


  // Set the selected queue to the current selected one
  handleQueueChange = (event) => {
    const selectedQueue = event.target.value;
    console.log('selectedQueue', selectedQueue);
    this.setState({ selectedQueue });
  };


  handleRefineBySAM = async () => {
    console.log('Refine by SAM clicked');

    await new Promise((resolve) => {
      this.setState({
        prompts: {},
        // uiComponents: [],
      }, () => {
        console.log("cleared old prompts");
        this.onSelectModel("sam");
        const filteredLabels = this.state.prevLabeL.filter(label => label !== 0);
        console.log("filtered labels is ,", filteredLabels);
        for (let i = 0; i < filteredLabels.length; i++) {
          this.handleAddObject();
        }
        resolve();
      });
    });

    // Now that state updated, do embedding calculation
  //  await this.sam2CalcImageEmbeddings('sam');

    const nid = this.notification.show({
      title: 'MONAI Label',
      message: 'Refining Mask Input ... For further Refinement use Prompts',
      type: 'info',
      duration: 10000,
    });

    const { info, viewConstants } = this.props;
    const image = viewConstants.SeriesInstanceUID;
    const model = this.modelSelector.current.currentModel();
    const config = this.props.onOptionsConfig();
    const params =
      config && config.infer && config.infer[model] ? { ...config.infer[model] } : {};

    params.State = "Mask";
    params.Mask = this.state.prevMask2Refine;
    console.log("mask has been sent to sam",this.state.prevMask2Refine );
    params.LabelMask = this.state.prevLabel; // to get the id based on this label
    console.log("label has been sent to sam from prev mask");

    const labels = info.models[model].labels;

    try {
      const response = await this.props.client().segmentation(model, image, params);

      if (!nid) {
        window.snackbar.hideAll();
      } else {
        this.notification.hide(nid);
      }

      if (response.status !== 200) {
        this.notification.show({
          title: 'MONAI Label',
          message: 'Failed to Enhance Input Mask',
          type: 'error',
          duration: 5000,
        });
      } else {
        this.notification.show({
          title: 'MONAI Label',
          message: 'Mask is Refined Successfully',
          type: 'success',
          duration: 10000,
        });

        await this.props.updateView(response, labels, model);
      }
    } catch (error) {
      if (!nid) {
        window.snackbar.hideAll();
      } else {
        this.notification.hide(nid);
      }
      this.notification.show({
        title: 'MONAI Label',
        message: `Error during refinement: ${error.message || error}`,
        type: 'error',
        duration: 5000,
      });
    }
  };

  /*
  Segment sam in 2d
  */
  sam2Onsegmentation2D = async (slice, auxillaryBox = null, auxillaryID = null) => {
    const nid = this.notification.show({
      title: 'MONAI Label',
      message: 'Running 2d SAM Segmentation... For 3d segmentation press RUN button',
      type: 'info',
      duration: 10000,
    });

    //Extract paramters for inference api request
    const { info, viewConstants } = this.props;
    const image = viewConstants.SeriesInstanceUID;
    const model = this.modelSelector.current.currentModel();
    const config = this.props.onOptionsConfig();
    const params =
      config && config.infer && config.infer[model] ? config.infer[model] : {};

    //Sepecfic configuration for sam
    params.samc2onfig = this.state.prompts;
    params.SliceIndex = slice;

    if (auxillaryBox) {
      params.State = "Auxillary_Box";
      params.Auxillary_Box = auxillaryBox;
      params.Auxillary_Id = auxillaryID; // obj_id
    } else {
      params.State = "2d";
    }

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
        message: 'Run SAM Segmentation Successfully',
        type: 'success',
        duration: 10000,
      });

      await this.props.updateView(response, labels, model);
    }
  };

  generateSessionId = () => {
    return `session-${Date.now()}-${Math.floor(Math.random() * 1000000)}`;
  };

  /*
  Calculate image embeddings for sam
  */
  // sam2CalcImageEmbeddings = async (model) => {

  //   this.setState({
  //     isCalculatingEmbeddings: true,
  //     volumeEmbeddingsCalculated: false
  //   });

  //   window.snackbar.hideAll();
  //   const nid = this.notification.show({
  //     title: 'MONAI Label',
  //     message: 'Wait for volume processing',
  //     type: 'info',
  //     duration: 600000,
  //   });

  //   //Extract paramters for inference api request
  //   const { info, viewConstants } = this.props;
  //   const image = viewConstants.SeriesInstanceUID;
  //   const config = this.props.onOptionsConfig();
  //   const params =
  //     config && config.infer && config.infer[model] ? config.infer[model] : {};

  //   //Specific congiguration for sam
  //   params.State = "ProcessVolume"

  //   const labels = info.models[model].labels;

  //   try {
  //     const response = await this.props
  //       .client()
  //       .segmentation(model, image, params);

  //     window.snackbar.hideAll();

  //     if (response.status !== 200) {
  //       this.notification.show({
  //         title: 'MONAI Label',
  //         message: 'Failed to process the volume',
  //         type: 'error',
  //         duration: 30000,
  //       });
  //     } else {
  //       this.notification.show({
  //         title: 'MONAI Label',
  //         message: 'Volume processed successfully',
  //         type: 'success',
  //         duration: 10000,
  //       });

  //       this.setState({ volumeEmbeddingsCalculated: true });

  //     }
  //   } catch (error) {
  //     console.error('Unexpected error:', error);
  //   } finally {
  //     this.setState({ isCalculatingEmbeddings: false });
  //   }

  // };


  //parse multipart form data and extract the mask
  extractMaskFromResponse = async (response) => {
    try {
      
      const contentType = response.headers['content-type'];
      const boundaryMatch = contentType.match(/boundary=(.+)/);
      if (!boundaryMatch) {
        throw new Error('No boundary found in content-type header');
      }
      const boundary = boundaryMatch[1];
      
      //  arrayBuffer to string to parse multipart data
      const arrayBuffer = response.data;
      const uint8Array = new Uint8Array(arrayBuffer);
      const textDecoder = new TextDecoder('utf-8');
      const responseText = textDecoder.decode(uint8Array);
      const parts = responseText.split(`--${boundary}`);
      
      let maskData = null;
      let jsonData = null;
      
      for (const part of parts) {
        if (part.trim() === '' || part.trim() === '--') continue;
        
        const [headerSection, ...bodyParts] = part.split('\r\n\r\n');
        const body = bodyParts.join('\r\n\r\n');
        
        if (!headerSection || !body) continue;
        
  
        if (headerSection.includes('application/json')) {
          try {
            jsonData = JSON.parse(body.trim());
          } catch (e) {
            console.warn('Failed to parse JSON part:', e);
          }
        }
        
        if (headerSection.includes('application/octet-stream') || 
            headerSection.includes('name="pred"') ||
            headerSection.includes('filename')) {
          const binaryBody = body.trim();
          const binaryArray = new Uint8Array(binaryBody.length);
          for (let i = 0; i < binaryBody.length; i++) {
            binaryArray[i] = binaryBody.charCodeAt(i);
          }
          maskData = binaryArray;
        }
      }
      
      return {
        mask: maskData,
        metadata: jsonData,
        fullData: jsonData 
      };
      
    } catch (error) {
      console.error('Error extracting mask from response:', error);
      throw error;
    }
  };
  onSegmentation = async (samCheck = true) => {

    // TODO:: Fix Image ID...
    const { info, viewConstants } = this.props;
    const image = viewConstants.SeriesInstanceUID;
    const model = this.modelSelector.current.currentModel();
    const config = this.props.onOptionsConfig();
    const params =
      config && config.infer && config.infer[model] ? config.infer[model] : {};

    if (model == 'sam') {
      params.samc2onfig = this.state.prompts;
      params.State = "3d";

      if (samCheck) {

        //Check for the missing boxes
        const shouldContinue = await this.checkBoxesInAllViews();
        // If user clicked Cancel, return immediately
        if (!shouldContinue) {
          return;
        }
      }
    }
    const nid = this.notification.show({
      title: 'MONAI Label',
      message: 'Running Auto-Segmentation...',
      type: 'info',
      duration: 60000,
    });
    //if vista
    if (model == 'vista') {
      //get the mode
      params.Mode = this.state.vistaMode;
      console.log("params.Mode", params.Mode);
      if (params.Mode == 'automatic') {
        //send class value
        params.Class = this.state.vistaSelectedClass.value;
        console.log("params.Class", params.Class);
      }
      else {
        //send points and labels
        params.Class = 133;

        params.Points = this.state.vistaPrompts;
        params.Mask = this.state.prevMask2Refine;
        console.log("mask has been sent to vista",this.state.prevMask2Refine );
        
      }
    }
    // if nnInteractive
    if (model == 'nninteractive') {
      params.Points = this.state.nnInteractivePrompts;
      params.viewPort = this.state.activeViewportId;
      params.resetInteractions = this.state.resetInteractions;

    }
    const labels = info.models[model].labels;
    const response = await this.props
      .client()
      .segmentation(model, image, params);
    if (model != 'sam'){ 
    const extractedData = await this.extractMaskFromResponse(response);
    this.setState({
      prevMask2Refine: extractedData.mask,
      prevLabeL: Object.values(extractedData.metadata.label_names),
    });
    
    console.log('Extracted mask:', extractedData.mask);
    console.log('Metadata:', extractedData.metadata);
    console.log('labels of this mask  is :', this.state.prevLabeL); };
    console.log("length of the labels is ", this.state.prevLabeL.length);

    // Bug:: Notification Service on show doesn't return idprevLabeL
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

      await this.props.updateView(response, labels, model);
      this.state.resetInteractions = false;
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
          />{this.state.currentModel === 'nninteractive' && (
            <div style={{
              width: '80%',
              margin: '10px 0',
              borderTop: '1px solid #eee',
              paddingTop: '15px'
            }}>
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '10px',
                marginBottom: '15px'
              }}>
                {/* Pop Button */}
                <button
                  onClick={this.handleNnInteractivePopPoint}
                  style={{
                    marginRight: '10px',
                    color: 'black',
                    fontSize: '17px',
                    fontWeight: 'bold',
                    cursor: 'pointer',
                    background: 'none',
                    border: 'none',
                    padding: 0
                  }}
                  title="Remove Last Point"
                >
                  ↶
                </button>

                {/* Point Label */}
                <span style={{
                  fontSize: '14px',
                  fontWeight: '500',
                  color: '#333',
                  marginRight: '10px'
                }}>
                  Point:
                </span>

                {/* Positive Button */}
                <button
                  onClick={() => this.handleNnInteractivePointSelection('positive')}
                  style={{
                    width: '32px',
                    height: '32px',
                    padding: 0,
                    backgroundColor: '#4CAF50',
                    color: 'white',
                    border: 'none',
                    borderRadius: '50%',
                    cursor: 'pointer',
                    fontSize: '16px',
                    fontWeight: 'bold',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    transition: 'background-color 0.3s'
                  }}
                  onMouseOver={(e) => e.currentTarget.style.backgroundColor = '#3e8e41'}
                  onMouseOut={(e) => e.currentTarget.style.backgroundColor = '#4CAF50'}
                  title="Positive Point"
                >
                  +
                </button>

                {/* Negative Button */}
                <button
                  onClick={() => this.handleNnInteractivePointSelection('negative')}
                  style={{
                    width: '32px',
                    height: '32px',
                    padding: 0,
                    backgroundColor: '#f44336',
                    color: 'white',
                    border: 'none',
                    borderRadius: '50%',
                    cursor: 'pointer',
                    fontSize: '16px',
                    fontWeight: 'bold',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    transition: 'background-color 0.3s'
                  }}
                  onMouseOver={(e) => e.currentTarget.style.backgroundColor = '#d32f2f'}
                  onMouseOut={(e) => e.currentTarget.style.backgroundColor = '#f44336'}
                  title="Negative Point"
                >
                  -
                </button>
              </div>
              <div style={{ marginTop: '10px' }}>
                <button
                  className={`px-4 py-2 text-white rounded-md font-semibold shadow active:scale-95 transition-all ${this.state.resetInteractions
                    ? 'bg-gray-700 hover:bg-gray-800'
                    : 'bg-blue-600 hover:bg-blue-700'
                    }`}
                  onClick={() => {
                    this.setState(prevState => ({ resetInteractions: !prevState.resetInteractions }));
                  }}
                >
                  Reset Interactions
                </button>
              </div>
            </div>
          )}
          {this.state.currentModel === 'vista' && (
            <div style={{
              width: '80%',
              margin: '10px 0',
              borderTop: '1px solid #eee',
              paddingTop: '15px'
            }}>
              {/* Mode Selection Toggle Buttons */}
              <div style={{
                display: 'flex',
                justifyContent: 'center',
                marginBottom: '15px',
                gap: '10px'
              }}>
                <button
                  onClick={() => this.handleVistaModeChange({ target: { value: 'automatic' } })} style={{
                    padding: '8px 20px',
                    backgroundColor: this.state.vistaMode === 'automatic' ? '#007bff' : '#e9ecef',
                    color: this.state.vistaMode === 'automatic' ? 'white' : '#495057',
                    border: 'none',
                    borderRadius: '10px',
                    cursor: 'pointer',
                    fontSize: '10px',
                    fontWeight: '500',
                    transition: 'all 0.3s ease',
                    boxShadow: this.state.vistaMode === 'automatic' ? '0 2px 5px rgba(0,0,0,0.1)' : 'none'
                  }}
                >
                  Automatic Mode
                </button>

                <button
                  onClick={() => this.handleVistaModeChange({ target: { value: 'interactive' } })} style={{
                    padding: '8px 20px',
                    backgroundColor: this.state.vistaMode === 'interactive' ? '#007bff' : '#e9ecef',
                    color: this.state.vistaMode === 'interactive' ? 'white' : '#495057',
                    border: 'none',
                    borderRadius: '10px',
                    cursor: 'pointer',
                    fontSize: '10px',
                    fontWeight: '500',
                    transition: 'all 0.3s ease',
                    boxShadow: this.state.vistaMode === 'interactive' ? '0 2px 5px rgba(0,0,0,0.1)' : 'none'
                  }}
                >
                  Interactive Mode
                </button>
              </div>

              {/* Conditional Rendering (keep the rest of your existing code here) */}
              {this.state.vistaMode === 'automatic' ? (
                <div style={{
                  // margin: '0 20px 15px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px'
                }}>
                  <label
                    htmlFor="vista-classes"
                    style={{
                      fontSize: '10px',
                      fontWeight: '500',
                      color: '#333',
                      whiteSpace: 'nowrap' // Prevents label from wrapping
                    }}
                  >
                    Class:
                  </label>
                  <select
                    id="vista-classes"
                    value={this.state.vistaSelectedClass.name}
                    onChange={this.handleClassChange}
                    style={{
                      padding: '8px 12px',
                      borderRadius: '4px',
                      border: '1px solid #ddd',
                      backgroundColor: '#fff',
                      fontSize: '10px',
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
                  gap: '10px',
                  marginBottom: '15px'
                }}>
                  {/* Pop Button */}
                  <button
                    onClick={this.handleVistaPopPoint}
                    style={{
                      marginRight: '10px',
                      color: 'black',
                      fontSize: '17px',
                      fontWeight: 'bold',
                      cursor: 'pointer',
                      background: 'none',
                      border: 'none',
                      padding: 0
                    }}
                    title="Remove Last Point"
                  >
                    ↶
                  </button>

                  {/* Point Label */}
                  <span style={{
                    fontSize: '14px',
                    fontWeight: '500',
                    color: '#333',
                    marginRight: '10px'
                  }}>
                    Point:
                  </span>

                  {/* Positive Button */}
                  <button
                    onClick={() => this.handleVistaPointSelection('positive')}
                    style={{
                      width: '32px',
                      height: '32px',
                      padding: 0,
                      backgroundColor: '#4CAF50',
                      color: 'white',
                      border: 'none',
                      borderRadius: '50%', // Makes it circular
                      cursor: 'pointer',
                      fontSize: '16px',
                      fontWeight: 'bold',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      transition: 'background-color 0.3s'
                    }}
                    onMouseOver={(e) => e.currentTarget.style.backgroundColor = '#3e8e41'}
                    onMouseOut={(e) => e.currentTarget.style.backgroundColor = '#4CAF50'}
                    title="Positive Point"
                  >
                    +
                  </button>

                  {/* Negative Button */}
                  <button
                    onClick={() => this.handleVistaPointSelection('negative')}
                    style={{
                      width: '32px',
                      height: '32px',
                      padding: 0,
                      backgroundColor: '#f44336',
                      color: 'white',
                      border: 'none',
                      borderRadius: '50%', // Makes it circular
                      cursor: 'pointer',
                      fontSize: '16px',
                      fontWeight: 'bold',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      transition: 'background-color 0.3s'
                    }}
                    onMouseOver={(e) => e.currentTarget.style.backgroundColor = '#d32f2f'}
                    onMouseOut={(e) => e.currentTarget.style.backgroundColor = '#f44336'}
                    title="Negative Point"
                  >
                    -
                  </button>
                </div>
              )}
            </div>
          )}




          {this.state.currentModel === 'sam' && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '2px', marginTop: '5px', marginBottom: '5px' }}>

              {/* {add object btn} */}
              <button
                className="customButton"
                onClick={this.handleAddObject}
                style={{
                  padding: '8px 15px',
                  backgroundColor: '#007bff',
                  color: 'white',
                  border: 'none',
                  borderRadius: '20px',
                  cursor: 'pointer',
                  height: 'auto',
                  lineHeight: 'normal',
                  display: 'inline-block',
                  marginLeft: '10px',
                  marginBottom: '10px',

                }}

              >
                Add Object
              </button>
            </div>
          )}
          {this.state.currentModel === 'sam' && (
            <div style={{ width: '100%' }}>
              {/* Other content */}
              {this.state.uiComponents.map((component) => (
                <div
                  key={component.objectId}
                  style={{
                    border: '1px solid #ccc',
                    padding: '10px',
                    marginBottom: '10px',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                >
                  <button
                    onClick={() => this.handlePopPoint(component.objectId)}
                    style={{
                      marginRight: '0px',
                      marginLeft: '0px',

                      color: 'black',
                      fontSize: '17px',
                      fontWeight: 'bold',
                      cursor: 'pointer',
                    }}
                    title="Undo"

                  >
                    ↶
                  </button>
                  {/* Object ID */}
                  <span
                    style={{ fontWeight: 'bold', flex: 1, fontSize: '14px' }}
                  >
                    {component.label}
                  </span>

                  {/* Add Point Button */}
                  <button
                    onClick={() => this.handleAddBox(component.objectId)}
                    style={{
                      padding: '5px 10px', // Reduced padding for smaller button
                      backgroundColor:
                        this.state.objectColors[component.objectId], // Set background color to the color assigned to the object ID
                      color: 'white',
                      border: 'none',
                      borderRadius: '20px',
                      cursor: 'pointer',
                      height: 'auto',
                      lineHeight: 'normal',
                      display: 'inline-block',
                      marginRight: '5px',
                      fontSize: '12px',
                    }}

                  >
                    {component.buttons[1].buttonText}
                  </button>
                  <button
                    onClick={() => this.handleAddPoint(component.objectId)}
                    style={{
                      padding: '5px 10px', // Reduced padding for smaller button
                      backgroundColor:
                        this.state.objectColors[component.objectId], // Set background color to the color assigned to the object ID
                      color: 'white',
                      border: 'none',
                      borderRadius: '20px',
                      cursor: 'pointer',
                      height: 'auto',
                      lineHeight: 'normal',
                      display: 'inline-block',
                      marginRight: '5px',
                      fontSize: '12px',
                    }}

                  >
                    {component.buttons[0].buttonText}
                  </button>


                  {/* Radio Buttons */}
                  <div style={{ display: 'flex', gap: '10px' }}>
                    <label
                      style={{
                        fontSize: '14px',
                        display: 'flex',
                        alignItems: 'center',
                      }}
                    >
                      <input
                        type="radio"
                        name={`pointType_${component.objectId}`} // Unique name for each object
                        value="+"
                        checked={
                          this.state.prompts[component.objectId]?.pointType ===
                          '+'
                        }
                        onChange={(e) =>
                          this.handleRadioChange(e, component.objectId)
                        }
                        style={{ marginRight: '5px' }}
                        title="Positive Point '🔵' shape"

                      />
                      +
                    </label>
                    <label
                      style={{
                        fontSize: '16px',
                        display: 'flex',
                        alignItems: 'center',
                        fontColor: "bold black"
                      }}
                    >
                      <input
                        type="radio"
                        name={`pointType_${component.objectId}`} // Unique name for each object
                        value="-"
                        checked={
                          this.state.prompts[component.objectId]?.pointType ===
                          '-'
                        }
                        onChange={(e) =>
                          this.handleRadioChange(e, component.objectId)
                        }
                        style={{ marginRight: '5px' }}
                        title="Negative Point '🔷' shape"

                      />
                      -
                    </label>
                  </div>
                </div>
              ))}
            </div>
          )}
          {!['sam', 'vista', 'nninteractive'].includes(this.state.currentModel) && (
            <div style={{ textAlign: 'center', marginTop: '20px' }}>
              <button
                onClick={this.handleRefineBySAM} // You can define this method
                style={{
                  padding: '8px 15px',
                  backgroundColor: '#007BFF',
                  color: 'white',
                  border: 'none',
                  borderRadius: '20px',
                  cursor: 'pointer',
                  fontSize: '13px',
                  // fontWeight: 'bold',
                  marginLeft: '10px',
                  marginBottom: '10px',
                }}
              >
                Refine By SAM
              </button>
            </div>
           )}

        </div>
      </div>
    );
  }
}
