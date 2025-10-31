import { ProbeTool, annotation, drawing } from '@cornerstonejs/tools';
import { getEnabledElement, type Types } from '@cornerstonejs/core';

const { getAnnotations } = annotation.state;

export default class ProbeMONAILabelToolNeg extends ProbeTool {
  static toolName = 'ProbeMONAILabelNeg';

  constructor(
    toolProps = {},
    defaultToolProps = {
      configuration: {
        customColor: undefined,
      },
    }
  ) {
    super(toolProps, defaultToolProps);
  }

  renderAnnotation = (enabledElement, svgDrawingHelper): boolean => {
    let renderStatus = false;
    const { viewport } = enabledElement;
    const { element } = viewport;

    let annotations = getAnnotations(this.getToolName(), element);

    if (!annotations?.length) {
      return renderStatus;
    }

    annotations = this.filterInteractableAnnotationsForElement(
      element,
      annotations
    );

    if (!annotations?.length) {
      return renderStatus;
    }

    const targetId = this.getTargetId(viewport);
    const renderingEngine = viewport.getRenderingEngine();

    const styleSpecifier: StyleSpecifier = {
      toolGroupId: this.toolGroupId,
      toolName: this.getToolName(),
      viewportId: enabledElement.viewport.id,
    };

    for (let i = 0; i < annotations.length; i++) {
      const annotation = annotations[i] as ProbeAnnotation;
      const annotationUID = annotation.annotationUID;
      const data = annotation.data;
      const point = data.handles.points[0];
      const canvasCoordinates = viewport.worldToCanvas(point);

      styleSpecifier.annotationUID = annotationUID;

      const color = 'red';

      // If rendering engine has been destroyed while rendering
      if (!viewport.getRenderingEngine()) {
        console.warn('Rendering Engine has been destroyed');
        return renderStatus;
      }

      const handleGroupUID = '0';

      const size = 6;
      const [x, y] = canvasCoordinates;
      drawing.drawLine(
        svgDrawingHelper,
        annotationUID,
        `${annotationUID}-x1`,
        [x - size, y - size],
        [x + size, y + size],
        { color, lineWidth: 2 }
      );
      drawing.drawLine(
        svgDrawingHelper,
        annotationUID,
        `${annotationUID}-x2`,
        [x - size, y + size],
        [x + size, y - size],
        { color, lineWidth: 2 }
      );

      renderStatus = true;
    }

    return renderStatus;
  };
}
