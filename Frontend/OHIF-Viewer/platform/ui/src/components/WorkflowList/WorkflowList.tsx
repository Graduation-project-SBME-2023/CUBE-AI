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
      <div>
        <div className="bg-black">
          <div className="container relative mx-auto flex flex-col pt-5">
            <div className="mb-5 flex flex-row justify-between">
              <div className="flex min-w-[1px] shrink flex-row items-center gap-6">
                <Typography
                  variant="h6"
                  className="text-white"
                >
                  {t('Workflow List')}
                </Typography>
              </div>
              <div className="flex h-[34px] flex-row items-center">
                {/*<LegacyButton*/}
                {/*  rounded="full"*/}
                {/*  variant="outlined"*/}
                {/*  color="primaryActive"*/}
                {/*  border="primaryActive"*/}
                {/*  className="mx-8"*/}
                {/*  startIcon={<Icon name="cancel" />}*/}
                {/*  onClick={clearWorkflows}*/}
                {/*  disabled*/}
                {/*>*/}
                {/*  {t('Clear')}*/}
                {/*</LegacyButton>*/}

                <Typography
                  variant="h6"
                  className="mr-2"
                  data-cy={'num-workflows'}
                >
                  {numOfWorkflows > 100 ? '>100' : numOfWorkflows}
                </Typography>
                <Typography
                  variant="h6"
                  className="text-primary-light"
                >
                  {t('Workflows')}
                </Typography>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div className="sticky -top-1 z-10 mx-auto border-b-4 border-black">
        <div className="bg-primary-dark pt-3 pb-3">
          <div>
            {workflowsMeta.map(workflow => (
              <div
                className="cursor-pointer border-b border-gray-700 py-2 px-4 text-sm text-white hover:text-blue-500 sm:text-base"
                onClick={() => onWorkflowClick(workflow)}
              >
                {workflow}
              </div>
            ))}
          </div>
        </div>
        {numOfWorkflows > 100 && (
          <div className="container m-auto">
            <div className="bg-primary-main rounded-b py-1 text-center text-base">
              <p className="text-white">{t('NumOfWorkflowsHigherThan100Message')}</p>
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
