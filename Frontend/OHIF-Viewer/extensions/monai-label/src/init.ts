import {
  addTool,
} from '@cornerstonejs/tools';
import { Types } from '@ohif/core';
import ProbeMONAILabelToolPos from './tools/ProbeMONAILabelToolPos';
import ProbeMONAILabelToolNeg from './tools/ProbeMONAILabelToolNeg';

/**
 * @param {object} configuration
 */
export default function init({
  servicesManager,
  configuration = {},
}: Types.Extensions.ExtensionParams): void {
  addTool(ProbeMONAILabelToolPos);
  addTool(ProbeMONAILabelToolNeg);
}
