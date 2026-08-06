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
  <div className="bg-black">
    <div className="w-full relative mx-auto flex flex-col pt-5 pb-5 px-5">
      <div className="flex flex-row items-center justify-between">

        <Typography variant="h6" className="text-white">
          {t('Workflow List')}
        </Typography>

        <div className="flex h-[34px] flex-row items-center">
          <Typography variant="h6" className="mr-2" data-cy={'num-workflows'}>
            {numOfWorkflows > 100 ? '>100' : numOfWorkflows}
          </Typography>
          <Typography variant="h6" className="text-primary-light">
            {t('Workflows')}
          </Typography>
        </div>

      </div>
    </div>
  </div>

  {/* Workflow List */}
  <div className="sticky -top-1 z-10 mx-auto border-b-4 border-black">
    <div className="bg-black py-2">

      <div>
        {workflowsMeta.map(workflow => (
          <div
            key={workflow}
            className="group flex items-center justify-between cursor-pointer border-b border-secondary-light py-2 px-4 transition duration-300 hover:bg-secondary-main bg-primary-dark"
            onClick={() => onWorkflowClick(workflow)}
          >
            <span className="text-base text-white">
              {workflow}
            </span>
            <Icon
              name="chevron-right"
              className="inline-flex w-4 text-white"
            />
          </div>
        ))}
      </div>

    </div>

    {numOfWorkflows > 100 && (
      <div className="w-full m-auto px-4">
        <div className="bg-primary-main rounded-b py-1 text-center text-base">
          <p className="text-white">
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