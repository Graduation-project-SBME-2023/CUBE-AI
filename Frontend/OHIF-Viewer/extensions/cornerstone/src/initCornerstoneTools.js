import {
  PanTool,
  WindowLevelTool,
  StackScrollTool,
  StackScrollMouseWheelTool,
  ZoomTool,
  VolumeRotateMouseWheelTool,
  MIPJumpToClickTool,
  LengthTool,
  RectangleROITool,
  EllipticalROITool,
  CircleROITool,
  BidirectionalTool,
  ArrowAnnotateTool,
  DragProbeTool,
  ProbeTool,
  AngleTool,
  CobbAngleTool,
  PlanarFreehandROITool,
  MagnifyTool,
  CrosshairsTool,
  SegmentationDisplayTool,
  init,
  addTool,
  annotation,
  ReferenceLinesTool,
  TrackballRotateTool,
  CircleScissorsTool,
  RectangleScissorsTool,
  SphereScissorsTool,
} from '@cornerstonejs/tools';

import CalibrationLineTool from './tools/CalibrationLineTool';
import ImageOverlayViewerTool from './tools/ImageOverlayViewerTool';

function safeAddTool(tool) {
  try {
    addTool(tool);
  } catch (error) {
    const message = error?.message || '';
    if (!message.includes('has already been added globally')) {
      throw error;
    }
  }
}

export default function initCornerstoneTools(configuration = {}) {
  CrosshairsTool.isAnnotation = false;
  ReferenceLinesTool.isAnnotation = false;

  init(configuration);
  safeAddTool(PanTool);
  safeAddTool(WindowLevelTool);
  safeAddTool(StackScrollMouseWheelTool);
  safeAddTool(StackScrollTool);
  safeAddTool(ZoomTool);
  safeAddTool(ProbeTool);
  safeAddTool(VolumeRotateMouseWheelTool);
  safeAddTool(MIPJumpToClickTool);
  safeAddTool(LengthTool);
  safeAddTool(RectangleROITool);
  safeAddTool(EllipticalROITool);
  safeAddTool(CircleROITool);
  safeAddTool(BidirectionalTool);
  safeAddTool(ArrowAnnotateTool);
  safeAddTool(DragProbeTool);
  safeAddTool(AngleTool);
  safeAddTool(CobbAngleTool);
  safeAddTool(PlanarFreehandROITool);
  safeAddTool(MagnifyTool);
  safeAddTool(CrosshairsTool);
  safeAddTool(SegmentationDisplayTool);
  safeAddTool(ReferenceLinesTool);
  safeAddTool(CalibrationLineTool);
  safeAddTool(TrackballRotateTool);
  safeAddTool(CircleScissorsTool);
  safeAddTool(RectangleScissorsTool);
  safeAddTool(SphereScissorsTool);
  safeAddTool(ImageOverlayViewerTool);

  // Modify annotation tools to use dashed lines on SR
  const annotationStyle = {
    textBoxFontSize: '15px',
    lineWidth: '1.5',
  };

  const defaultStyles = annotation.config.style.getDefaultToolStyles();
  annotation.config.style.setDefaultToolStyles({
    global: {
      ...defaultStyles.global,
      ...annotationStyle,
    },
  });
}

const toolNames = {
  Pan: PanTool.toolName,
  ArrowAnnotate: ArrowAnnotateTool.toolName,
  WindowLevel: WindowLevelTool.toolName,
  StackScroll: StackScrollTool.toolName,
  StackScrollMouseWheel: StackScrollMouseWheelTool.toolName,
  Zoom: ZoomTool.toolName,
  VolumeRotateMouseWheel: VolumeRotateMouseWheelTool.toolName,
  MipJumpToClick: MIPJumpToClickTool.toolName,
  Length: LengthTool.toolName,
  DragProbe: DragProbeTool.toolName,
  Probe: ProbeTool.toolName,
  RectangleROI: RectangleROITool.toolName,
  EllipticalROI: EllipticalROITool.toolName,
  CircleROI: CircleROITool.toolName,
  Bidirectional: BidirectionalTool.toolName,
  Angle: AngleTool.toolName,
  CobbAngle: CobbAngleTool.toolName,
  PlanarFreehandROI: PlanarFreehandROITool.toolName,
  Magnify: MagnifyTool.toolName,
  Crosshairs: CrosshairsTool.toolName,
  SegmentationDisplay: SegmentationDisplayTool.toolName,
  ReferenceLines: ReferenceLinesTool.toolName,
  CalibrationLine: CalibrationLineTool.toolName,
  TrackballRotateTool: TrackballRotateTool.toolName,
  CircleScissors: CircleScissorsTool.toolName,
  RectangleScissors: RectangleScissorsTool.toolName,
  SphereScissors: SphereScissorsTool.toolName,
  ImageOverlayViewer: ImageOverlayViewerTool.toolName,
};

export { toolNames };
