import React, { useState, useEffect } from 'react';
import PropTypes from 'prop-types';
import { useTranslation } from 'react-i18next';

import LegacyButton from '../LegacyButton';
import LegacyButtonGroup from '../LegacyButtonGroup';
import Typography from '../Typography';
import Select from '../Select';

import SegmentationQueue from '../../../../../extensions/monai-label/src/components/SegmentationQueue';
import QueueOptionsManager from '../QueueOptionsManager';

const StudyListPagination = ({ onChangePage, currentPage, perPage, onChangePerPage }) => {
  const { t } = useTranslation('StudyList');

  const navigateToPage = page => {
    const toPage = page < 1 ? 1 : page;
    onChangePage(toPage);
  };

  const ranges = [
    { value: '25', label: '25' },
    { value: '50', label: '50' },
    { value: '100', label: '100' },
  ];
  const [selectedRange, setSelectedRange] = useState(ranges.find(r => r.value === perPage));
  const [queueOptions, setQueueOptions] = useState(QueueOptionsManager.getQueueOptions());
  const [selectedOptions, setSelectedOptions] = useState([]);

  useEffect(() => {
    const updateOptions = newOptions => {
      setQueueOptions([...newOptions]);
    };

    QueueOptionsManager.onChange(updateOptions);

    return () => {
      QueueOptionsManager.onChangeCallbacks = QueueOptionsManager.onChangeCallbacks.filter(
        callback => callback !== updateOptions
      );
    };
  }, []);

  const removeQueueOption = () => {
    if (selectedOptions) {
      QueueOptionsManager.removeQueueOption(selectedOptions.value, selectedOptions.label);
      // setQueueOptions(QueueOptionsManager.getQueueOptions());
      SegmentationQueue.removeRequest(selectedOptions.value, selectedOptions.label);

      setSelectedOptions([]); // Clear selected options
    }
    console.log(QueueOptionsManager.getQueueOptions());
  };

  const onSelectedRange = selectedRange => {
    setSelectedRange(selectedRange);
    onChangePerPage(selectedRange.value);
  };

  const handleSelectChange = selected => {
    setSelectedOptions(selected);
    console.log('Selected:', selected);
  };

  return (
    <div className="bg-black py-10">
      <div className="container relative m-auto px-8">
        <div className="mb-4 ml-auto flex items-center justify-end space-x-4">
          <Typography className="text-base text-white">{t('Queue')}</Typography>

          {/* Combobox */}
          <Select
            id="queue-options"
            className="w-40"
            options={queueOptions}
            value={selectedOptions}
            isMulti={false}
            isClearable={false}
            isSearchable={false}
            closeMenuOnSelect={true}
            hideSelectedOptions={false}
            placeholder={t('Select Option')}
            onChange={handleSelectChange}
          />

          {/* Delete Button */}
          <LegacyButton
            size="medium"
            className="bg-gray-600 py-2 px-4 text-base font-bold text-white hover:bg-gray-700"
            onClick={removeQueueOption}
          >
            {t('Delete Element')}
          </LegacyButton>

          {/* "Run Queue" button */}
          <LegacyButton
            size="medium"
            className="rounded-md bg-blue-600 py-2 px-6 text-base font-bold text-white shadow-lg hover:bg-blue-700"
            style={{
              textAlign: 'center',
              border: '2px solid #1e40af',
            }}
            onClick={() => SegmentationQueue.processQueue()}
          >
            {t('Run Queue')}
          </LegacyButton>
        </div>
        {/* Combined container for the two sections */}
        <div className="flex items-center justify-between">
          <div className="mr-auto flex items-center justify-end">
            <Select
              id="rows-per-page"
              className="border-primary-main relative mr-3 w-24"
              options={ranges}
              value={selectedRange}
              isMulti={false}
              isClearable={false}
              isSearchable={false}
              closeMenuOnSelect={false}
              hideSelectedOptions={true}
              onChange={onSelectedRange}
            />
            <Typography className="text-base opacity-60">{t('Results per page')}</Typography>
          </div>

          {/* Second container for page info and buttons */}
          <div className="ml-auto flex items-center justify-end">
            <Typography className="mr-4 text-base opacity-60">
              {t('Page')} {currentPage}
            </Typography>
            <LegacyButtonGroup>
              <LegacyButton
                size="initial"
                className="px-4 py-2 text-base"
                color="translucent"
                border="primary"
                variant="outlined"
                onClick={() => navigateToPage(1)}
              >
                {`<<`}
              </LegacyButton>
              <LegacyButton
                size="initial"
                className="py-2 px-2 text-base"
                color="translucent"
                border="primary"
                variant="outlined"
                onClick={() => navigateToPage(currentPage - 1)}
              >
                {t('Previous')}
              </LegacyButton>
              <LegacyButton
                size="initial"
                className="py-2 px-4 text-base"
                color="translucent"
                border="primary"
                variant="outlined"
                onClick={() => navigateToPage(currentPage + 1)}
              >
                {t('Next')}
              </LegacyButton>
            </LegacyButtonGroup>
          </div>
        </div>
      </div>
    </div>
  );
};

StudyListPagination.propTypes = {
  onChangePage: PropTypes.func.isRequired,
  currentPage: PropTypes.number.isRequired,
  perPage: PropTypes.number.isRequired,
  onChangePerPage: PropTypes.func.isRequired,
};

export default StudyListPagination;
