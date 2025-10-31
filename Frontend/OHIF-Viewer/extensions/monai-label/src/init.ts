import {
  addTool,
} from '@cornerstonejs/tools';
import { Types } from '@ohif/core';
import ProbeMONAILabelToolPos from './tools/ProbeMONAILabelToolPos';
import ProbeMONAILabelToolNeg from './tools/ProbeMONAILabelToolNeg';
import RecROIToolMONAILabel from './tools/RecROIToolMONAILabel';

/**
 * @param {object} configuration
 */
export default function init({
  servicesManager,
  configuration = {},
}: Types.Extensions.ExtensionParams): void {
  addTool(ProbeMONAILabelToolPos);
  addTool(ProbeMONAILabelToolNeg);
  addTool(RecROIToolMONAILabel);
}
