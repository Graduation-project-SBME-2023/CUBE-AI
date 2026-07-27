import React, { useState } from 'react';
import PropTypes from 'prop-types';
import { useTranslation } from 'react-i18next';

import LegacyButton from '../LegacyButton';
import Icon from '../Icon';
import Typography from '../Typography';

const WorkflowList = ({
  workflowsMeta,
  workflowValues,
  onChange,
  clearWorkflows,
  numOfWorkflows,
  onWorkflowClick,
}) => {
  const { t } = useTranslation('WorkflowList');

  return (
   <React.Fragment>

  {/* Header */}
  <div className="bg-[#101c35] border-b border-[#1a2540]">
    <div className="container relative mx-auto flex flex-col px-5 pt-4 pb-3">
      <div className="flex flex-row items-center justify-between">

        <Typography variant="h6" className="text-[13px] font-semibold text-[#c8d8f0] tracking-tight">
          {t('Workflow List')}
        </Typography>

        <div className="flex items-center gap-1.5">
          <Typography variant="h6" className="text-[12px] font-bold text-[#4a9eff]" data-cy={'num-workflows'}>
            {numOfWorkflows > 100 ? '>100' : numOfWorkflows}
          </Typography>
          <Typography variant="h6" className="text-[12px] text-[#5a8ab8]">
            {t('Workflows')}
          </Typography>
        </div>

      </div>
    </div>
  </div>

  {/* Workflow List */}
  <div className="sticky -top-1 z-10 mx-auto border-b border-[#1a2540]">
    <div className="bg-[#0d1526] py-2">

      <div>
        {workflowsMeta.map(workflow => (
          <div
            key={workflow}
            className="group flex items-center justify-between cursor-pointer border-b border-[#1a2540] py-2 px-4 transition-colors duration-150 hover:bg-[#071020]"
            onClick={() => onWorkflowClick(workflow)}
          >
            <span className="text-[12px] text-[#8ab4e0] group-hover:text-[#4a9eff] transition-colors duration-150">
              {workflow}
            </span>
            <Icon
              name="chevron-right"
              className="w-3.5 h-3.5 text-[#1e3058] group-hover:text-[#4a9eff] transition-colors duration-150"
            />
          </div>
        ))}
      </div>

    </div>

    {numOfWorkflows > 100 && (
      <div className="container m-auto">
        <div className="bg-[#071020] border border-[#1a2540] rounded-b py-1.5 text-center">
          <p className="text-[11px] text-[#5a8ab8]">
            {t('NumOfWorkflowsHigherThan100Message')}
          </p>
        </div>
      </div>
    )}
  </div>

</React.Fragment>
  );
};

WorkflowList.propTypes = {
  workflowsMeta: PropTypes.array.isRequired,
  workflowValues: PropTypes.object.isRequired,
  numOfWorkflows: PropTypes.number.isRequired,
  onChange: PropTypes.func.isRequired,
  // clearWorkflows: PropTypes.func.isRequired,
};

export default WorkflowList;