import {
  addTool,
} from '@cornerstonejs/tools';
import { Types } from '@ohif/core';
import ProbeMONAILabelToolPos from './tools/ProbeMONAILabelToolPos';
import ProbeMONAILabelToolNeg from './tools/ProbeMONAILabelToolNeg';
import RecROIToolMONAILabel from './tools/RecROIToolMONAILabel';

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

/**
 * @param {object} configuration
 */
export default function init({
  servicesManager,
  configuration = {},
}: Types.Extensions.ExtensionParams): void {
  safeAddTool(ProbeMONAILabelToolPos);
  safeAddTool(ProbeMONAILabelToolNeg);
  safeAddTool(RecROIToolMONAILabel);
}
