import React, { useState, useEffect, useMemo } from 'react';
import classnames from 'classnames';
import PropTypes from 'prop-types';
import { Link, useNavigate } from 'react-router-dom';
import moment from 'moment';
import qs from 'query-string';
import isEqual from 'lodash.isequal';
import { useTranslation } from 'react-i18next';
import { UIModalService, UINotificationService } from '@ohif/core';

import filtersMeta from './filtersMeta.js';
import { useAppConfig } from '@state';
import { useDebounce, useSearchParams } from '@hooks';
import { utils, hotkeys, ServicesManager } from '@ohif/core';
import WFModal from './WFModal';
import Select from '../../../../ui/src/components/Select/Select';
import g_workflowManagerInstance from '../Workflow/WorkflowManager.js';

import {
  Icon,
  StudyListExpandedRow,
  LegacyButton,
  EmptyStudies,
  StudyListTable,
  StudyListPagination,
  StudyListFilter,
  WorkflowList,
  TooltipClipboard,
  Header,
  useModal,
  AboutModal,
  UserPreferences,
  LoadingIndicatorProgress,
  useSessionStorage,
} from '@ohif/ui';

import i18n from '@ohif/i18n';
import { set } from 'lodash';
import { use } from 'i18next';
import { Color } from 'platform/ui/src/storybook/color/color.js';

const { sortBySeriesDate } = utils;

const { availableLanguages, defaultLanguage, currentLanguage } = i18n;

const seriesInStudiesMap = new Map();

/**
 * TODO:
 * - debounce `setFilterValues` (150ms?)
 */
function WorkList({
  data: studies,
  dataTotal: studiesTotal,
  isLoadingData,
  dataSource,
  hotkeysManager,
  dataPath,
  onRefresh,
  servicesManager,
}) {
  const { hotkeyDefinitions, hotkeyDefaults } = hotkeysManager;
  const { show, hide } = useModal();
  const { t } = useTranslation();
  // ~ Modes
  const [appConfig] = useAppConfig();
  // ~ Filters
  const searchParams = useSearchParams();

  const navigate = useNavigate();
  const STUDIES_LIMIT = 101;

  const queryFilterValues = _getQueryFilterValues(searchParams);

  const [sessionQueryFilterValues, updateSessionQueryFilterValues] = useSessionStorage({
    key: 'queryFilterValues',
    defaultValue: queryFilterValues,
    // ToDo: useSessionStorage currently uses an unload listener to clear the filters from session storage
    // so on systems that do not support unload events a user will NOT be able to alter any existing filter
    // in the URL, load the page and have it apply.
    clearOnUnload: true,
  });
  const [filterValues, _setFilterValues] = useState({
    ...defaultFilterValues,
    ...sessionQueryFilterValues,
  });

  const debouncedFilterValues = useDebounce(filterValues, 200);
  const { resultsPerPage, pageNumber, sortBy, sortDirection } = filterValues;

  const [workflowlist, setWorkflowlist] = useState([]); // Initialize with an empty array

  const [openModal, setOpenModal] = useState(false); // State for modal visibility
  const [newWorkflowName, setNewWorkflowName] = useState('');
  const [errorMessage, setErrorMessage] = useState(''); // State for error message
  const [selectedWorkFlowList, setSelectedWorkFlowList] = useState({});

  const updateStudySelectedWorkflow = (studyInstanceUid, workflow) => {
    setSelectedWorkFlowList(prev => ({
      ...prev,
      [studyInstanceUid]: workflow,
    }));
  };

  useEffect(() => {
    const fetchWorkflowNames = async () => {
      try {
        await g_workflowManagerInstance.refreshDatabase();
        const names = g_workflowManagerInstance.getWorkflowNames();
        setWorkflowlist(names);
      } catch (error) {
        console.error('Error fetching workflow names:', error);
      }
    };

    fetchWorkflowNames();
  }, []);

  let notification = new UINotificationService();
  const handleOpenModal = () => {
    setOpenModal(true);
  };

  // Close the modal
  const handleCloseModal = () => {
    setOpenModal(false);
    setNewWorkflowName(''); // Clear input
  };

  const handleCreateWorkflow = async () => {
    if (newWorkflowName.trim() !== '') {
      const workflowFound = g_workflowManagerInstance.workflows.find(
        workflow => workflow.workflowName === newWorkflowName
      );

      if (!workflowFound) {
        const newWorkflow = await g_workflowManagerInstance.createWorkflow(newWorkflowName);

        notification.show({
          title: 'Workflow creation',
          message: `Workflow ${newWorkflow.workflowName} is created successfully`,
          type: 'success',
          duration: 3000,
        });

        setWorkflowlist(prevList => [...prevList, newWorkflow.workflowName]);

        handleCloseModal();
      } else {
        setErrorMessage(t('Name already exists'));
      }
    } else {
      setErrorMessage('Workflow name cannot be empty');
    }
  };

  // const onClearWorkflowsClick = () => {
  //   console.log('Clearing All Workflows');
  //   setWorkflowlist([]);
  //   setSelectedWorkFlowList(null);
  //   g_workflowManagerInstance.workflows = [];
  // };

  const appendStudyInWorkflow = async studyUID => {
    let selectedWorkflow = await g_workflowManagerInstance.getWorkflowByName(selectedWorkFlowList[studyUID].label);

    selectedWorkflow.addStudy(studyUID);

    g_workflowManagerInstance.workflows = g_workflowManagerInstance.workflows.map(workflow =>
      workflow.workflowName === selectedWorkflow.workflowName ? selectedWorkflow : workflow
    );

    try {
      const response = await fetch('http://localhost:8000/workflow/add', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          case_id: studyUID,
          task_name: selectedWorkflow.workflowName,
          series_instance_uid: '',
          instance_uid: '',
          train: false,
        }),
      });

      if (!response.ok) {
        throw new Error(`Error: ${response.status}`);
      }
      notification.show({
        title: 'Study Added',
        message: `Study ${studyUID} is added to workflow${selectedWorkFlowList.label} successfully`,
        type: 'success',
        duration: 3000,
      });
      const result = await response.json();
    } catch (error) {
      console.error('Failed to add workflow record:', error);
    }
  };

  /*
   * The default sort value keep the filters synchronized with runtime conditional sorting
   * Only applied if no other sorting is specified and there are less than 101 studies
   */

  const canSort = studiesTotal < STUDIES_LIMIT;
  const shouldUseDefaultSort = sortBy === '' || !sortBy;
  const sortModifier = sortDirection === 'descending' ? 1 : -1;
  const defaultSortValues =
    shouldUseDefaultSort && canSort ? { sortBy: 'studyDate', sortDirection: 'ascending' } : {};
  const sortedStudies = studies;

  if (canSort) {
    studies.sort((s1, s2) => {
      if (shouldUseDefaultSort) {
        const ascendingSortModifier = -1;
        return _sortStringDates(s1, s2, ascendingSortModifier);
      }

      const s1Prop = s1[sortBy];
      const s2Prop = s2[sortBy];

      if (typeof s1Prop === 'string' && typeof s2Prop === 'string') {
        return s1Prop.localeCompare(s2Prop) * sortModifier;
      } else if (typeof s1Prop === 'number' && typeof s2Prop === 'number') {
        return (s1Prop > s2Prop ? 1 : -1) * sortModifier;
      } else if (!s1Prop && s2Prop) {
        return -1 * sortModifier;
      } else if (!s2Prop && s1Prop) {
        return 1 * sortModifier;
      } else if (sortBy === 'studyDate') {
        return _sortStringDates(s1, s2, sortModifier);
      }

      return 0;
    });
  }

  // ~ Rows & Studies
  const [expandedRows, setExpandedRows] = useState([]);
  const [studiesWithSeriesData, setStudiesWithSeriesData] = useState([]);
  const numOfStudies = studiesTotal;
  const querying = useMemo(() => {
    return isLoadingData || expandedRows.length > 0;
  }, [isLoadingData, expandedRows]);

  const setFilterValues = val => {
    if (filterValues.pageNumber === val.pageNumber) {
      val.pageNumber = 1;
    }
    _setFilterValues(val);
    updateSessionQueryFilterValues(val);
    setExpandedRows([]);
  };

  const onPageNumberChange = newPageNumber => {
    const oldPageNumber = filterValues.pageNumber;
    const rollingPageNumberMod = Math.floor(101 / filterValues.resultsPerPage);
    const rollingPageNumber = oldPageNumber % rollingPageNumberMod;
    const isNextPage = newPageNumber > oldPageNumber;
    const hasNextPage = Math.max(rollingPageNumber, 1) * resultsPerPage < numOfStudies;

    if (isNextPage && !hasNextPage) {
      return;
    }

    setFilterValues({ ...filterValues, pageNumber: newPageNumber });
  };

  const onResultsPerPageChange = newResultsPerPage => {
    setFilterValues({
      ...filterValues,
      pageNumber: 1,
      resultsPerPage: Number(newResultsPerPage),
    });
  };

  // Set body style
  useEffect(() => {
    document.body.classList.add('bg-black');
    return () => {
      document.body.classList.remove('bg-black');
    };
  }, []);

  // Sync URL query parameters with filters
  useEffect(() => {
    if (!debouncedFilterValues) {
      return;
    }

    const queryString = {};
    Object.keys(defaultFilterValues).forEach(key => {
      const defaultValue = defaultFilterValues[key];
      const currValue = debouncedFilterValues[key];

      // TODO: nesting/recursion?
      if (key === 'studyDate') {
        if (currValue.startDate && defaultValue.startDate !== currValue.startDate) {
          queryString.startDate = currValue.startDate;
        }
        if (currValue.endDate && defaultValue.endDate !== currValue.endDate) {
          queryString.endDate = currValue.endDate;
        }
      } else if (key === 'modalities' && currValue.length) {
        queryString.modalities = currValue.join(',');
      } else if (currValue !== defaultValue) {
        queryString[key] = currValue;
      }
    });

    const search = qs.stringify(queryString, {
      skipNull: true,
      skipEmptyString: true,
    });

    navigate({
      pathname: '/',
      search: search ? `?${search}` : undefined,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedFilterValues]);

  // Query for series information
  useEffect(() => {
    const fetchSeries = async studyInstanceUid => {
      try {
        const series = await dataSource.query.series.search(studyInstanceUid);
        seriesInStudiesMap.set(studyInstanceUid, sortBySeriesDate(series));
        setStudiesWithSeriesData([...studiesWithSeriesData, studyInstanceUid]);
      } catch (ex) {
        // TODO: UI Notification Service
        console.warn(ex);
      }
    };

    // TODO: WHY WOULD YOU USE AN INDEX OF 1?!
    // Note: expanded rows index begins at 1
    for (let z = 0; z < expandedRows.length; z++) {
      const expandedRowIndex = expandedRows[z] - 1;
      const studyInstanceUid = sortedStudies[expandedRowIndex].studyInstanceUid;

      if (studiesWithSeriesData.includes(studyInstanceUid)) {
        continue;
      }

      fetchSeries(studyInstanceUid);
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandedRows, studies]);

  const isFiltering = (filterValues, defaultFilterValues) => {
    return !isEqual(filterValues, defaultFilterValues);
  };

  const rollingPageNumberMod = Math.floor(101 / resultsPerPage);
  const rollingPageNumber = (pageNumber - 1) % rollingPageNumberMod;
  const offset = resultsPerPage * rollingPageNumber;
  const offsetAndTake = offset + resultsPerPage;
  const tableDataSource = sortedStudies.map((study, key) => {
    const rowKey = key + 1;
    const isExpanded = expandedRows.some(k => k === rowKey);
    const {
      studyInstanceUid,
      accession,
      modalities,
      instances,
      description,
      mrn,
      patientName,
      date,
      time,
    } = study;

    const studyDate =
      date &&
      moment(date, ['YYYYMMDD', 'YYYY.MM.DD'], true).isValid() &&
      moment(date, ['YYYYMMDD', 'YYYY.MM.DD']).format(t('Common:localDateFormat', 'MMM-DD-YYYY'));
    const studyTime =
      time &&
      moment(time, ['HH', 'HHmm', 'HHmmss', 'HHmmss.SSS']).isValid() &&
      moment(time, ['HH', 'HHmm', 'HHmmss', 'HHmmss.SSS']).format(
        t('Common:localTimeFormat', 'hh:mm A')
      );

    return {
      dataCY: `studyRow-${studyInstanceUid}`,
      row: [
        {
          key: 'patientName',
          content: patientName ? (
            <TooltipClipboard>{patientName}</TooltipClipboard>
          ) : (
            <span className="text-gray-700">(Empty)</span>
          ),
          gridCol: 4,
        },
        {
          key: 'mrn',
          content: <TooltipClipboard>{mrn}</TooltipClipboard>,
          gridCol: 3,
        },
        {
          key: 'studyDate',
          content: (
            <>
              {studyDate && <span className="mr-4">{studyDate}</span>}
              {studyTime && <span>{studyTime}</span>}
            </>
          ),
          title: `${studyDate || ''} ${studyTime || ''}`,
          gridCol: 5,
        },
        {
          key: 'description',
          content: <TooltipClipboard>{description}</TooltipClipboard>,
          gridCol: 4,
        },
        {
          key: 'modality',
          content: modalities,
          title: modalities,
          gridCol: 3,
        },
        {
          key: 'accession',
          content: <TooltipClipboard>{accession}</TooltipClipboard>,
          gridCol: 3,
        },
        {
          key: 'instances',
          content: (
            <>
              <Icon
                name="group-layers"
                className={classnames('mr-2 inline-flex w-4', {
                  'text-primary-active': isExpanded,
                  'text-secondary-light': !isExpanded,
                })}
              />
              {instances}
            </>
          ),
          title: (instances || 0).toString(),
          gridCol: 4,
        },
      ],
      // Todo: This is actually running for all rows, even if they are
      // not clicked on.
      expandedContent: (
        <StudyListExpandedRow
          seriesTableColumns={{
            description: t('StudyList:Description'),
            seriesNumber: t('StudyList:Series'),
            modality: t('StudyList:Modality'),
            instances: t('StudyList:Instances'),
          }}
          seriesTableDataSource={
            seriesInStudiesMap.has(studyInstanceUid)
              ? seriesInStudiesMap.get(studyInstanceUid).map(s => {
                  return {
                    description: s.description || '(empty)',
                    seriesNumber: s.seriesNumber ?? '',
                    modality: s.modality || '',
                    instances: s.numSeriesInstances || '',
                  };
                })
              : []
          }
        >
          <div className="flex flex-row items-center justify-between gap-2">
            <div className="flex flex-row gap-2">
              {appConfig.loadedModes.map((mode, i) => {
                const modalitiesToCheck = modalities.replaceAll('/', '\\');
                const isValidMode = mode.isValidMode({
                  modalities: modalitiesToCheck,
                  study,
                });
                // TODO: Modes need a default/target route? We mostly support a single one for now.
                // We should also be using the route path, but currently are not
                // mode.routeName
                // mode.routes[x].path
                // Don't specify default data source, and it should just be picked up... (this may not currently be the case)
                // How do we know which params to pass? Today, it's just StudyInstanceUIDs and configUrl if exists
                const query = new URLSearchParams();
                if (filterValues.configUrl) {
                  query.append('configUrl', filterValues.configUrl);
                }
                query.append('StudyInstanceUIDs', studyInstanceUid);

                return (
                  mode.displayName && (
                    <Link
                      className={isValidMode ? '' : 'cursor-not-allowed'}
                      key={i}
                      to={`${dataPath ? '../../' : ''}${mode.routeName}${
                        dataPath || ''
                      }?${query.toString()}`}
                      onClick={event => {
                        // In case any event bubbles up for an invalid mode, prevent the navigation.
                        // For example, the event bubbles up when the icon embedded in the disabled button is clicked.
                        if (!isValidMode) {
                          event.preventDefault();
                        }
                      }}
                    >
                      {/* TODO revisit the completely rounded style of buttons used for launching a mode from the worklist later - for now use LegacyButton*/}
                      <LegacyButton
                        rounded="full"
                        variant={isValidMode ? 'contained' : 'disabled'}
                        disabled={!isValidMode}
                        endIcon={<Icon name="launch-arrow" />} // launch-arrow | launch-info
                        onClick={() => {}}
                        data-cy={`mode-${mode.routeName}-${studyInstanceUid}`}
                      >
                        {t(`Modes:${mode.displayName}`)}
                      </LegacyButton>
                    </Link>
                  )
                );
              })}
            </div>
            {workflowlist && workflowlist.length > 0 ? (
              <div className="flex items-center gap-2">
                <Select
                  id="workflow-combobox"
                  className="w-40"
                  options={workflowlist.map(workflow => ({
                    label: workflow,
                    value: workflow,
                  }))}
                  value={selectedWorkFlowList[studyInstanceUid] || null}
                  isMulti={false}
                  isClearable={false}
                  isSearchable={false}
                  closeMenuOnSelect={true}
                  hideSelectedOptions={false}
                  onChange={e => updateStudySelectedWorkflow(studyInstanceUid, e)}
                  placeholder="Select Workflow"
                ></Select>
                <LegacyButton
                  htmlFor="workflow-combobox"
                  className="mr-2 cursor-pointer text-sm font-medium text-gray-700"
                  onClick={() => appendStudyInWorkflow(studyInstanceUid)}
                >
                  Add to Workflow
                </LegacyButton>
              </div>
            ) : (
              <span className="text-sm text-gray-500">No workflows available</span>
            )}
          </div>
        </StudyListExpandedRow>
      ),
      onClickRow: () =>
        setExpandedRows(s => (isExpanded ? s.filter(n => rowKey !== n) : [...s, rowKey])),
      isExpanded,
    };
  });

  const hasStudies = numOfStudies > 0;
  const versionNumber = process.env.VERSION_NUMBER;
  const commitHash = process.env.COMMIT_HASH;

  const menuOptions = [
    {
      title: t('Header:About'),
      icon: 'info',
      onClick: () =>
        show({
          content: AboutModal,
          title: t('AboutModal:About OHIF Viewer'),
          contentProps: { versionNumber, commitHash },
        }),
    },
    {
      title: t('Header:Preferences'),
      icon: 'settings',
      onClick: () =>
        show({
          title: t('UserPreferencesModal:User preferences'),
          content: UserPreferences,
          contentProps: {
            hotkeyDefaults: hotkeysManager.getValidHotkeyDefinitions(hotkeyDefaults),
            hotkeyDefinitions,
            onCancel: hide,
            currentLanguage: currentLanguage(),
            availableLanguages,
            defaultLanguage,
            onSubmit: state => {
              if (state.language.value !== currentLanguage().value) {
                i18n.changeLanguage(state.language.value);
              }
              hotkeysManager.setHotkeys(state.hotkeyDefinitions);
              hide();
            },
            onReset: () => hotkeysManager.restoreDefaultBindings(),
            hotkeysModule: hotkeys,
          },
        }),
    },
  ];

  if (appConfig.oidc) {
    menuOptions.push({
      icon: 'power-off',
      title: t('Header:Logout'),
      onClick: () => {
        navigate(`/logout?redirect_uri=${encodeURIComponent(window.location.href)}`);
      },
    });
  }

  const { customizationService } = servicesManager.services;
  const { component: dicomUploadComponent } =
    customizationService.get('dicomUploadComponent') ?? {};
  const uploadProps =
    dicomUploadComponent && dataSource.getConfig()?.dicomUploadEnabled
      ? {
          title: 'Upload files',
          closeButton: true,
          shouldCloseOnEsc: false,
          shouldCloseOnOverlayClick: false,
          content: dicomUploadComponent.bind(null, {
            dataSource,
            onComplete: () => {
              hide();
              onRefresh();
            },
            onStarted: () => {
              show({
                ...uploadProps,
                // when upload starts, hide the default close button as closing the dialogue must be handled by the upload dialogue itself
                closeButton: false,
              });
            },
          }),
        }
      : undefined;

  const { component: dataSourceConfigurationComponent } =
    customizationService.get('ohif.dataSourceConfigurationComponent') ?? {};

  return (
    <div className="flex h-screen flex-col bg-black ">
      <WFModal
        isOpen={openModal}
        onClose={handleCloseModal}
        onConfirm={handleCreateWorkflow}
        workFlowName={newWorkflowName}
        setWorkflowName={setNewWorkflowName}
        errorMessage={errorMessage}
      />
      <Header
        isSticky
        menuOptions={menuOptions}
        isReturnEnabled={false}
        WhiteLabeling={appConfig.whiteLabeling}
      />
      <div className="flex flex-row justify-center gap-4 ">
        <div className="ohif-scrollbar flex max-w-[75%] grow flex-col overflow-y-auto pl-4">
          <StudyListFilter
            numOfStudies={pageNumber * resultsPerPage > 100 ? 101 : numOfStudies}
            filtersMeta={filtersMeta}
            filterValues={{ ...filterValues, ...defaultSortValues }}
            onChange={setFilterValues}
            clearFilters={() => setFilterValues(defaultFilterValues)}
            isFiltering={isFiltering(filterValues, defaultFilterValues)}
            onUploadClick={uploadProps ? () => show(uploadProps) : undefined}
            getDataSourceConfigurationComponent={
              dataSourceConfigurationComponent
                ? () => dataSourceConfigurationComponent()
                : undefined
            }
          />
          {hasStudies ? (
            <div className="flex grow flex-col">
              <StudyListTable
                tableDataSource={tableDataSource.slice(offset, offsetAndTake)}
                numOfStudies={numOfStudies}
                querying={querying}
                filtersMeta={filtersMeta}
              />
              <div className="grow">
                <StudyListPagination
                  onChangePage={onPageNumberChange}
                  onChangePerPage={onResultsPerPageChange}
                  currentPage={pageNumber}
                  perPage={resultsPerPage}
                />
              </div>
            </div>
          ) : (
            <div className="w-1/4 max-w-[300px] flex-shrink-0">
              {appConfig.showLoadingIndicator && isLoadingData ? (
                <LoadingIndicatorProgress className={'h-full w-full bg-black'} />
              ) : (
                <EmptyStudies />
              )}
            </div>
          
          )}
        </div>
        <div className="w-1/4 flex-shrink-0 bg-black p-4 text-white" style={{ paddingTop: 0}}>
          {workflowlist && workflowlist.length > 0 ? (
            <WorkflowList
              workflowsMeta={workflowlist}
              workflowValues={{ sortBy: 'name', sortDirection: 'asc' }}
              numOfWorkflows={workflowlist.length}
              onChange={newValues => console.log('Changed:', newValues)}
              onWorkflowClick={workflow => {
                // Redirect to the workflow's route
                navigate(`/workflow/${workflow}`);
              }}
              // clearWorkflows={onClearWorkflowsClick}
            />
          ) : (
            <div className="text-gray-400">No workflows available</div>
          )}
          <div className="container mx-auto my-4 flex justify-center">
            <button
              className="rounded bg-blue-500 py-2 px-4 font-bold text-white hover:bg-blue-600"
              onClick={handleOpenModal}
            >
              {t('Create Workflow')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

WorkList.propTypes = {
  data: PropTypes.array.isRequired,
  dataSource: PropTypes.shape({
    query: PropTypes.object.isRequired,
    getConfig: PropTypes.func,
  }).isRequired,
  isLoadingData: PropTypes.bool.isRequired,
  servicesManager: PropTypes.instanceOf(ServicesManager),
};

const defaultFilterValues = {
  patientName: '',
  mrn: '',
  studyDate: {
    startDate: null,
    endDate: null,
  },
  description: '',
  modalities: [],
  accession: '',
  sortBy: '',
  sortDirection: 'none',
  pageNumber: 1,
  resultsPerPage: 25,
  datasources: '',
  configUrl: null,
};

function _tryParseInt(str, defaultValue) {
  let retValue = defaultValue;
  if (str && str.length > 0) {
    if (!isNaN(str)) {
      retValue = parseInt(str);
    }
  }
  return retValue;
}

function _getQueryFilterValues(params) {
  const queryFilterValues = {
    patientName: params.get('patientname'),
    mrn: params.get('mrn'),
    studyDate: {
      startDate: params.get('startdate') || null,
      endDate: params.get('enddate') || null,
    },
    description: params.get('description'),
    modalities: params.get('modalities') ? params.get('modalities').split(',') : [],
    accession: params.get('accession'),
    sortBy: params.get('sortby'),
    sortDirection: params.get('sortdirection'),
    pageNumber: _tryParseInt(params.get('pagenumber'), undefined),
    resultsPerPage: _tryParseInt(params.get('resultsperpage'), undefined),
    datasources: params.get('datasources'),
    configUrl: params.get('configurl'),
  };

  // Delete null/undefined keys
  Object.keys(queryFilterValues).forEach(
    key => queryFilterValues[key] == null && delete queryFilterValues[key]
  );

  return queryFilterValues;
}

function _sortStringDates(s1, s2, sortModifier) {
  // TODO: Delimiters are non-standard. Should we support them?
  const s1Date = moment(s1.date, ['YYYYMMDD', 'YYYY.MM.DD'], true);
  const s2Date = moment(s2.date, ['YYYYMMDD', 'YYYY.MM.DD'], true);

  if (s1Date.isValid() && s2Date.isValid()) {
    return (s1Date.toISOString() > s2Date.toISOString() ? 1 : -1) * sortModifier;
  } else if (s1Date.isValid()) {
    return sortModifier;
  } else if (s2Date.isValid()) {
    return -1 * sortModifier;
  }
}
export default WorkList;
