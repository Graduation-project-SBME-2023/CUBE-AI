import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import classnames from 'classnames';
import g_workflowManagerInstance from './WorkflowManager';
import { useAppConfig } from '@state';
import { useTranslation } from 'react-i18next';
import MonaiLabelClient from '../../../../../extensions/monai-label/src/services/MonaiLabelClient';
import { UINotificationService } from '@ohif/core';
import { utils } from '@ohif/core';
import filtersMeta from '../WorkList/filtersMeta';
import { useNavigate, useLocation } from 'react-router-dom';

import {
  Icon,
  LegacyButton,
  StudyListTable,
  TooltipClipboard,
  StudyListExpandedRow,
  EmptyStudies,
  Typography,
  TableHead,
  Modal,
} from '@ohif/ui';

const modelConfigDict = {
  '2d': '2D',
  '3d_lowres': '3D Low Resolution',
  '3d_fullres': '3D Full Resolution',
};

const tableHeader = [
  {
    key: 'checkbox',
    content: <span className="font-bold">Select</span>,
    gridCol: 2,
  },
  {
    key: 'patientName',
    content: <span className="font-bold">Patient Name</span>,
    gridCol: 3,
  },
  {
    key: 'mrn',
    content: <span className="font-bold">MRN</span>,
    gridCol: 3,
  },
  {
    key: 'description',
    content: <span className="font-bold">Description</span>,
    gridCol: 3,
  },
  {
    key: 'modality',
    content: <span className="font-bold">Modality</span>,
    gridCol: 2,
  },
  {
    key: 'accession',
    content: <span className="font-bold">Accession</span>,
    gridCol: 3,
  },
  {
    key: 'instances',
    content: <span className="font-bold">Instances</span>,
    gridCol: 2,
  },
  {
    key: 'Annotated',
    content: <span className="font-bold">Annotation</span>,
    gridCol: 3,
  },
  {
    key: 'Train/Validation',
    content: <span className="font-bold">Train</span>,
    gridCol: 3,
  },
];

function WorkflowDetailPage({
  data: studies,
  dataTotal: studiesTotal,
  isLoadingData,
  dataSource,
  hotkeysManager,
  dataPath,
  onRefresh,
  servicesManager,
  extensionManager,
}) {
  const ServerSettings = useRef();

  const [isConnected, setIsConnected] = useState(false);

  const [appConfig] = useAppConfig();
  const { t } = useTranslation('WorkflowDetailPage');

  const navigate = useNavigate();
  const location = useLocation();

  const { workflowName } = useParams();

  const [labels, setLabels] = useState([{ id: 1, label: 'Label-1' }]);
  const [labelsDictionary, setLabelsDictionary] = useState({});

  const [expandedRows, setExpandedRows] = useState([]);
  const [studiesWithSeriesData, setStudiesWithSeriesData] = useState([]);
  const [selectedStudies, setSelectedStudies] = useState([]);
  const [selectAll, setSelectAll] = useState(false);
  const [trainingProgress, setTrainingProgress] = useState(0);
  const [isTraining, setIsTraining] = useState(false);
  const [annotationStatusDict, setAnnotationStatusDict] = useState({});

  const [isModalOpen, setIsModalOpen] = useState(false);

  const [isMonaiInitialized, setIsMonaiInitialized] = useState(false);

  const [workflowStudies, setWorkflowStudies] = useState([]);
  const [previousStudies, setPreviousStudies] = useState([]);
  const [modelConfig, setModelConfig] = useState('2d');
  const [trainCount, setTrainCount] = useState(0);
  const [validationCount, setValidationCount] = useState(0);
  const [trainValStudies, setTrainValStudies] = useState([]);

  const [numFolds, setNumFolds] = useState(1);

  const [workflowDetails, setWorkflowDetails] = useState(null);

  const client = () => {
    const settings =
      ServerSettings && ServerSettings.current && ServerSettings.current.state
        ? ServerSettings.current.state
        : null;

    return new MonaiLabelClient(settings ? settings.url : 'http://127.0.0.1:8000');
  };

  const onInfo = async () => {
    let notification = new UINotificationService();
    notification.show({
      title: 'Workflow Server',
      message: 'Connecting to Workflow Server',
      type: 'info',
      duration: 3000,
    });

    try {
      const response = await client().info();
      if (response.status === 200) {
        notification.show({
          title: 'Workflow Server',
          message: 'Connected to Workflow Server - Successful',
          type: 'success',
          duration: 2000,
        });
        setIsConnected(true);
      } else {
        notification.show({
          title: 'Workflow Server',
          message: 'Failed to Connect to Workflow Server Server',
          type: 'error',
          duration: 5000,
        });
        setIsConnected(false);
      }
    } catch (error) {
      notification.show({
        title: 'Workflow Server',
        message: 'Connection error: Unable to reach the server',
        type: 'error',
        duration: 5000,
      });
      setIsConnected(false);
    }
  };

  useEffect(() => {
    if (!isMonaiInitialized) {
      onInfo();
      setIsMonaiInitialized(true);
    }
  }, [isMonaiInitialized]);

  useEffect(() => {
    const loadWorkflowStudies = async () => {
      console.log('Loading workflow studies for:', workflowName);
      const workflow = await g_workflowManagerInstance.getWorkflowByName(workflowName);
      if (workflow) {
        console.log('Workflow loaded:', workflow);
        setWorkflowDetails(workflow);
        console.log('Workflow Details in useeffectttttt:', workflow);
      } else {
        console.error('Workflow not found');
      }
    };

    loadWorkflowStudies();
  }, [workflowName]);

  useEffect(() => {
    if (workflowDetails?.Studies?.length > 0) {
      const newStudies = studies.filter(study =>
        workflowDetails.Studies.includes(study.studyInstanceUid)
      );
      if (
        JSON.stringify(newStudies) !== JSON.stringify(previousStudies) ||
        previousStudies.length === 0
      ) {
        setWorkflowStudies(newStudies);
        setPreviousStudies([...newStudies]);
      }
    }
  }, [workflowDetails, studies]);

  useEffect(() => {
    const fetchAnnotationStatus = async () => {
      try {
        const url = `http://localhost:8000/workflow/${encodeURIComponent(
          workflowName
        )}/filter-annotated`;
        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        });

        if (!response.ok) {
          throw new Error(`Error: ${response.status}`);
        }

        const fetchedAnnotationStatus = await response.json();

        setAnnotationStatusDict(fetchedAnnotationStatus);
      } catch (error) {
        console.error('Error fetching annotation status:', error);
      }
    };

    fetchAnnotationStatus();
  }, [workflowName]);

  const validateLabels = labels => {
    let notification = new UINotificationService();
    let isValid = true;
    const inputValues = labels.map(label => {
      const inputElement = document.getElementById(`input-${label.id}`);
      return inputElement ? inputElement.value.trim() : '';
    });

    const hasEmptyLabels = inputValues.some(value => value === '');
    const hasDuplicates =
      new Set(inputValues.filter(value => value !== '')).size !==
      inputValues.filter(value => value !== '').length;
    if (hasEmptyLabels) {
      isValid = false;
      notification.show({
        title: 'Empty Labels',
        message: 'Please ensure all labels are filled before creating the model.',
        type: 'error',
        duration: 3000,
      });
      return;
    }
    if (hasDuplicates) {
      isValid = false;
      notification.show({
        title: 'Duplicate Label',
        message: 'Duplicate labels are not allowed. Please ensure all labels are unique.',
        type: 'error',
        duration: 3000,
      });
      return;
    }

    return { isValid };
  };

  const addLabel = () => {
    const { isValid } = validateLabels(labels);
    let notification = new UINotificationService();

    if (labels.length >= 5) {
      notification.show({
        title: 'Labels Limit',
        message: 'Maximum of 5 labels allowed.',
        type: 'info',
        duration: 3000,
      });
      return;
    }
    if (!isValid) {
      return;
    }

    const currentLabels = labels.map(label => {
      const inputElement = document.getElementById(`input-${label.id}`);
      return inputElement ? inputElement.value.trim() : '';
    });

    // Add the new label
    const nextId = labels.length + 1; // Increment ID
    setLabels([...labels, { id: nextId, label: `Label-${nextId}` }]);

    const inputElements = document.querySelectorAll('[id^="input-"]');
    let labelId = 1; // Initialize the sequential ID counter

    // Use the dynamically assigned IDs to match values
    inputElements.forEach(input => {
      const value = input.value.trim(); //remove white spaces
      if (value !== '') {
        labelsDictionary[value] = labelId; // Only include non-empty values
        labelId++;
      }
    });
  };

  const handleDeleteLabel = labelId => {
    const inputElement = document.querySelector(`#input-${labelId}`);

    if (inputElement) {
      const value = inputElement.value.trim(); // Get the label text

      if (value !== '') {
        // Update state by removing the label from dictionary
        setLabelsDictionary(prevDictionary => {
          const newDictionary = { ...prevDictionary };
          delete newDictionary[value]; // Remove by value
          return newDictionary;
        });

        // Clear the input field (optional)
        inputElement.value = '';
      }
    } else {
      console.log(`Input field with id "input-${labelId}" not found`);
    }
  };

  useEffect(() => {
    let interval;
    if (isTraining) {
      interval = setInterval(() => {
        setTrainingProgress(prevProgress => {
          if (prevProgress >= 100) {
            clearInterval(interval);
            setIsTraining(false); // Stop training when progress reaches 100%
            return 100;
          }
          return prevProgress + 1; // Increment progress by 1% every second
        });
      }, 500);
    } else {
      setTrainingProgress(0); // Reset progress when training stops
    }

    return () => clearInterval(interval); // Cleanup function to clear interval
  }, [isTraining]);

  const handleLabelBlur = (id, value) => {
    let notification = new UINotificationService();
    if (value.trim() === '') {
      notification.show({
        title: 'Empty Label',
        message: 'Please enter a valid label.',
        type: 'error',
        duration: 3000,
      });
      return;
    }

    setLabelsDictionary(prevDictionary => ({
      ...prevDictionary,
      [value.trim()]: id, // Store only the final trimmed value
    }));
  };

  const fetchTrainFlag = async (taskName, caseId) => {
    try {
      const url = `http://localhost:8000/workflow/${encodeURIComponent(taskName)}/get-train?study_uid=${encodeURIComponent(caseId)}`;
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error('Failed to fetch train flag');
      }
      const data = await response.json();
      return data.train === true ? 'Train' : 'Validation';
    } catch (error) {
      console.error(`Error fetching train flag for ${caseId}:`, error);
      return 'Train'; // Default to 'Train' in case of error
    }
  };

  useEffect(() => {
    const initializeTrainValStudies = async () => {
      if (workflowStudies.length > 0 && trainValStudies.length === 0) {
        const updatedStudies = await Promise.all(
          workflowStudies.map(async study => {
            const trainValidation = await fetchTrainFlag(workflowName, study.studyInstanceUid);
            return { ...study, trainValidation };
          })
        );

        setTrainValStudies(updatedStudies);

        // Compute initial train/validation counts
        const newTrainCount = updatedStudies.filter(
          study => study.trainValidation === 'Train'
        ).length;
        const newValidationCount = updatedStudies.filter(
          study => study.trainValidation === 'Validation'
        ).length;

        setTrainCount(newTrainCount);
        setValidationCount(newValidationCount);
      }
    };

    initializeTrainValStudies();
  }, [workflowStudies]);

  const handleTrainValidationChange = async (event, studyInstanceUid) => {
    const newValue = event.target.value;
    const trainFlag = newValue === 'Train';

    try {
      const queryParams = new URLSearchParams({
        study_uid: studyInstanceUid,
        train: trainFlag,
      }).toString();
      const url = `http://localhost:8000/workflow/${encodeURIComponent(workflowDetails.workflowName)}/update-train?${queryParams}`;
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      });
      const result = response.json();

      setTrainValStudies(prevStudies => {
        const updatedStudies = prevStudies.map(study =>
          study.studyInstanceUid === studyInstanceUid
            ? { ...study, trainValidation: newValue }
            : study
        );

        // Recalculate counts immediately
        const newTrainCount = updatedStudies.filter(
          study => study.trainValidation === 'Train'
        ).length;
        const newValidationCount = updatedStudies.filter(
          study => study.trainValidation === 'Validation'
        ).length;

        setTrainCount(newTrainCount);
        setValidationCount(newValidationCount);
        return updatedStudies;
      });
    } catch (error) {
      console.error('Error updating train/validation:', error);
    }
  };

  const handleSelectAll = () => {
    if (selectAll) {
      setSelectedStudies([]);
    } else {
      const allStudyInstanceUids = workflowStudies.map(study => study.studyInstanceUid);
      setSelectedStudies(allStudyInstanceUids);
    }
    setSelectAll(!selectAll);
  };

  const handleCheckboxChange = (studyInstanceUid, isChecked) => {
    setSelectedStudies(prev => {
      if (isChecked) {
        return [...prev, studyInstanceUid];
      } else {
        return prev.filter(uid => uid !== studyInstanceUid);
      }
    });
  };

  const handleDeletion = async () => {
    // Prepare the records to be deleted
    const recordsToDelete = selectedStudies.map(studyInstanceUid => ({
      case_id: studyInstanceUid,
      task_name: workflowName,
    }));

    try {
      const response = await fetch('http://localhost:8000/workflow/delete-multiple', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ records: recordsToDelete }),
      });

      if (!response.ok) {
        const error = await response.json();
        console.error('Failed to delete records:', error.detail || 'Unknown error');
        return;
      }

      const result = await response.json();
      console.log('Deletion successful:', result.message);

      // Update UI state after successful deletion
      workflowDetails.Studies = workflowDetails.Studies.filter(
        study => !selectedStudies.includes(study)
      );

      setWorkflowStudies(
        studies.filter(study => workflowDetails.Studies.includes(study.studyInstanceUid))
      );
      // selectedStudies.forEach(studyInstanceUid => {
      //   g_workflowManagerInstance.deleteStudyFromWorkflow(workflowName, studyInstanceUid);
      // });
      // Clear the selection
      setSelectedStudies([]);
    } catch (error) {
      console.error('Error while deleting studies:', error);
    }
  };

  const querying = useMemo(() => {
    return isLoadingData || expandedRows.length > 0;
  }, [isLoadingData, expandedRows]);

  const seriesInStudiesMap = new Map();
  const { sortBySeriesDate } = utils;

  useEffect(() => {
    let notification = new UINotificationService();
    const fetchSeries = async studyInstanceUid => {
      try {
        const series = await dataSource.query.series.search(studyInstanceUid);
        seriesInStudiesMap.set(studyInstanceUid, sortBySeriesDate(series));
        setStudiesWithSeriesData([...studiesWithSeriesData, studyInstanceUid]);
      } catch (ex) {
        console.warn(ex);
        notification.show({
          title: 'Error',
          message: `Failed to fetch series for study: ${studyInstanceUid}`,
          type: 'error',
          duration: 3000,
        });
      }
    };

    for (let z = 0; z < expandedRows.length; z++) {
      const expandedRowIndex = expandedRows[z] - 1;
      const studyInstanceUid = workflowStudies[expandedRowIndex].studyInstanceUid;

      if (studiesWithSeriesData.includes(studyInstanceUid)) {
        continue;
      }
      fetchSeries(studyInstanceUid);
    }
  }, [expandedRows, studies, modelConfig]);

  useEffect(() => {
    let notification = new UINotificationService();

    const checkConfigConflict = async () => {
      let has2DConflict = false;
      let has3DConflict = false;

      for (const workflowStudy of workflowStudies) {
        const series = await dataSource.query.series.search(workflowStudy.studyInstanceUid);

        if (modelConfig === '2d' && series.some(s => s.numSeriesInstances > 1)) {
          has2DConflict = true;
        }
        if (
          modelConfig === '3d_lowres' ||
          (modelConfig === '3d_fullres' && series.some(s => s.numSeriesInstances < 2))
        ) {
          has3DConflict = true;
        }
      }

      if (has2DConflict) {
        notification.show({
          title: 'Configuration Conflict',
          message: 'The selected configuration is 2D, but some studies have more than 1 instance.',
          type: 'warning',
          duration: 5000,
        });
      }
      if (has3DConflict) {
        notification.show({
          title: 'Configuration Conflict',
          message: 'The selected configuration is 3D, but some studies have 1 instance.',
          type: 'warning',
          duration: 5000,
        });
      }
    };

    checkConfigConflict();
  }, [modelConfig]);

  const train = async () => {
    let notification = new UINotificationService();
    setIsTraining(true);

    try {
      const url = `http://localhost:8000/workflow/${encodeURIComponent(workflowName)}/train`;
      const fetchedWorkflowDataResponse = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_name: workflowName }),
      });
      let fetchedData = await fetchedWorkflowDataResponse.json();
      if (!fetchedWorkflowDataResponse.ok) {
        throw new Error(`Error: ${fetchedWorkflowDataResponse.status}`);
      }

      const queryParams = new URLSearchParams({
        model_name: workflowDetails.workflowName,
        labels: JSON.stringify(labelsDictionary),
        fold: numFolds,
        plan_type: modelConfig,
      }).toString();
      console.log('Query Params:', queryParams);
      try {
        // Construct query string

        const response = await fetch(`http://localhost:8000/create-model?${queryParams}`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
        });

        if (!response.ok) {
          throw new Error(`Error: ${response.status}`);
        }

        const result = await response.json();
        console.log('Model creation response:', result);

        notification.show({
          title: 'Success',
          message: 'Model Created Successfully!',
          type: 'success',
          duration: 3000,
        });
        try {
          const queryParams = new URLSearchParams({
            model_name: workflowDetails.workflowName,
          }).toString();
          const response = await fetch(`http://localhost:8000/train/${workflowName}`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
          });
          if (!response.ok) {
            throw new Error(`Error: ${response.status}`);
          }
          const result = await response.json();
          console.log('Model train response:', result);
          notification.show({
            title: 'Success',
            message: 'Model is Training!',
            type: 'success',
            duration: 5000,
          });
        } catch (error) {
          console.error('Error training model:', error);

          notification.show({
            title: 'Error',
            message: `Failed to train model: ${error.message}`,
            type: 'error',
            duration: 3000,
          });
        }
      } catch (error) {
        console.error('Error creating model:', error);

        notification.show({
          title: 'Error',
          message: `Failed to create model: ${error.message}`,
          type: 'error',
          duration: 3000,
        });
      }
    } catch (error) {
      console.error('Error Fetching Series:', error);
    }
  };

  const onClickReturnButton = () => {
    const { pathname } = location;
    const dataSourceIdx = pathname.indexOf('/', 1);
    const query = new URLSearchParams(window.location.search);
    const configUrl = query.get('configUrl');

    const dataSourceName = pathname.substring(dataSourceIdx + 1);
    const existingDataSource = extensionManager.getDataSources(dataSourceName);

    const searchQuery = new URLSearchParams();
    if (dataSourceIdx !== -1 && existingDataSource) {
      searchQuery.append('datasources', pathname.substring(dataSourceIdx + 1));
    }

    if (configUrl) {
      searchQuery.append('configUrl', configUrl);
    }

    navigate({
      pathname: '/',
      search: decodeURIComponent(searchQuery.toString()),
    });
  };

  //Study Table Data Section
  const hasStudies = workflowStudies.length > 0;

  const TableHeader = ({ tableHeader }) => {
    return (
      <TableHead
        className="  items-center"
        style={{ color: 'white', paddingRight: 0 }}
      >
        {tableHeader.map(column => (
          <div
            key={column.key}
            className={` w-${column.gridCol}/24 flex items-center justify-center border-b py-1 text-center font-semibold `}
          >
            {column.content}
          </div>
        ))}
      </TableHead>
    );
  };

  // iterate over all the studies to fill out the table data
  const tableDataSource = workflowStudies.map((study, key) => {
    const rowKey = key + 1;
    const isExpanded = expandedRows.some(k => k === rowKey);
    const { studyInstanceUid, accession, modalities, instances, description, mrn, patientName } =
      study;
    const isChecked = selectedStudies.includes(studyInstanceUid);

    return {
      dataCY: `studyRow-${studyInstanceUid}`,
      row: [
        {
          key: 'checkbox',
          content: (
            <input
              type="checkbox"
              className="form-checkbox justify-start text-left"
              data-cy={`row-checkbox-${studyInstanceUid}`}
              onClick={event => {
                event.stopPropagation(); // Prevent the click event from propagating to the row
              }}
              checked={isChecked}
              onChange={event => {
                const isChecked = event.target.checked;
                handleCheckboxChange(studyInstanceUid, isChecked);
              }}
            />
          ),
          gridCol: 2,
        },
        {
          key: 'patientName',
          content: patientName ? (
            <TooltipClipboard>{patientName}</TooltipClipboard>
          ) : (
            <span className="text-gray-700">(Empty)</span>
          ),
          gridCol: 3,
        },
        {
          key: 'mrn',
          content: <TooltipClipboard>{mrn}</TooltipClipboard>,
          gridCol: 3,
        },
        {
          key: 'description',
          content: <TooltipClipboard>{description}</TooltipClipboard>,
          gridCol: 3,
        },
        {
          key: 'modality',
          content: modalities,
          title: modalities,
          gridCol: 2,
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
          gridCol: 2,
        },
        {
          key: 'Annotated',
          content: (
            <div
              className="flex items-center justify-center rounded-2xl p-2 shadow-md"
              style={{
                backgroundColor: annotationStatusDict[studyInstanceUid] ? '#00b300' : '#737678', // Green for Annotated, Gray for Not Annotated
                minWidth: '100px',
                color: 'white',
              }}
            >
              <TooltipClipboard>
                {annotationStatusDict[studyInstanceUid] ? (
                  <span>Annotated</span>
                ) : (
                  <span>Not Annotated</span>
                )}
              </TooltipClipboard>
            </div>
          ),

          gridCol: 3,
        },
        {
          key: 'Train/Validation',
          content: (
            <select
              value={
                trainValStudies.find(s => s.studyInstanceUid === studyInstanceUid)
                  ?.trainValidation || 'Train'
              }
              onChange={event => handleTrainValidationChange(event, studyInstanceUid)}
              onClick={event => event.stopPropagation()} // Prevent row expansion
              className="bg-primary-dark cursor-pointer rounded border  px-2 py-1 text-white"
            >
              <option value="Train">Train</option>
              <option value="Validation">Validation</option>
            </select>
          ),
          title: 'Train/Validation',
          gridCol: 3,
        },
      ],
      // Todo: This is actually running for all rows, even if they are
      // not clicked on.
      expandedContent: (
        <StudyListExpandedRow
          seriesTableColumns={{}}
          seriesTableDataSource={
            seriesInStudiesMap.has(studyInstanceUid)
              ? seriesInStudiesMap.get(studyInstanceUid).map(s => { })
              : []
          }
        >
          <div className="flex flex-row items-center justify-between gap-2">
            <div className="flex flex-row gap-2">
              {appConfig.loadedModes.map((mode, i) => {
                if (mode.displayName == 'Assisted Segmentation') {
                  const modalitiesToCheck = modalities.replaceAll('/', '\\');
                  const isValidMode = mode.isValidMode({
                    modalities: modalitiesToCheck,
                    study,
                  });
                  const query = new URLSearchParams();
                  query.append('StudyInstanceUIDs', studyInstanceUid);
                  const route = `/${mode.routeName}?${query.toString()}`;

                  return (
                    mode.displayName && (
                      <Link
                        className={isValidMode ? '' : 'cursor-not-allowed'}
                        key={i}
                        to={{
                          pathname: `/${mode.routeName}`, // Path without query parameters
                          search: `?workflowName=${workflowDetails.workflowName}&StudyInstanceUIDs=${studyInstanceUid}`, // Query parameters
                          state: { from: `/workflow/${workflowDetails.workflowName}` }, // Optional: Pass previous page in state
                        }}
                        onClick={event => {
                          if (!isValidMode) {
                            event.preventDefault();
                          }
                        }}
                      >
                        <LegacyButton
                          rounded="full"
                          variant={isValidMode ? 'contained' : 'disabled'}
                          disabled={!isValidMode}
                          endIcon={<Icon name="launch-arrow" />} // launch-arrow | launch-info
                          onClick={() => { }}
                          data-cy={`mode-${mode.routeName}-${studyInstanceUid}`}
                        >
                          {t(`Modes:${mode.displayName}`)}
                        </LegacyButton>
                      </Link>
                    )
                  );
                }
              })}
            </div>
          </div>
        </StudyListExpandedRow>
      ),
      onClickRow: () =>
        setExpandedRows(s => (isExpanded ? s.filter(n => rowKey !== n) : [...s, rowKey])),
      isExpanded,
    };
  });

  return (
    <div className="h-screen bg-[#0d1526]">

      {/* Top Navbar */}
      <div className="bg-[#101c35] border-b border-[#1a2540] relative z-10">
        <div className="flex items-center justify-between px-6 h-14">
          <div className="flex items-center space-x-4">
            <button
              onClick={onClickReturnButton}
              className="group relative flex h-9 w-9 items-center justify-center rounded-lg transition-all duration-200 hover:bg-[#1a2540] active:scale-95"
              aria-label="Go back"
            >
              <Icon
                name="chevron-left"
                className="text-[#5a8ab8] group-hover:text-[#4a9eff] h-5 w-5 transition-colors duration-200"
              />
            </button>

            <div className="h-6 w-px bg-[#1a2540]" />

            <div className="flex items-center space-x-2.5">
              <Typography variant="h4" className="text-[14px] font-semibold text-[#c8d8f0] tracking-tight">
                {workflowName}
              </Typography>
              <span className="bg-[#4a9eff]/10 text-[#4a9eff] border border-[#4a9eff]/30 rounded-full px-2.5 py-0.5 text-[10px] font-semibold tracking-wider uppercase">
                Active
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content Layout */}
      <div className="flex" style={{ height: 'calc(100vh - 56px)' }}>

        {/* Left Panel - Studies Table */}
        <div className="flex-1 overflow-y-auto bg-[#0d1526] p-5">
          <div className="mb-5 flex items-center justify-between">
            <Typography variant="h5" className="text-[13px] font-semibold text-[#c8d8f0]">
              Studies
            </Typography>
            <div className="flex items-center gap-2">
              <LegacyButton
                rounded="full"
                variant="outlined"
                color="primaryActive"
                border="primaryActive"
                onClick={handleSelectAll}
                className="text-[11px] border border-[#1e3058] text-[#4a9eff] bg-transparent hover:bg-[#4a9eff]/10 transition-all duration-200 px-3 py-1 rounded-full"
              >
                {selectAll ? 'Unselect All' : 'Select All'}
              </LegacyButton>

              <LegacyButton
                rounded="full"
                variant="outlined"
                onClick={handleDeletion}
                className="text-[11px] border border-[#e05a5a]/30 text-[#e05a5a] bg-[#e05a5a]/10 hover:bg-[#e05a5a]/20 transition-all duration-200 px-3 py-1 rounded-full"
              >
                {t('Delete')}
              </LegacyButton>
            </div>
          </div>

          <div className="max-h-full">
            <TableHeader tableHeader={tableHeader} />
            {hasStudies ? (
              <StudyListTable
                tableDataSource={tableDataSource}
                numOfStudies={studiesTotal}
                querying={querying}
                filtersMeta={filtersMeta}
              />
            ) : (
              <EmptyStudies />
            )}
          </div>
        </div>

        {/* Right Panel - Configuration */}
        <div className="w-[320px] flex-shrink-0 h-full overflow-y-auto bg-[#0d1526] border-l border-[#1a2540] scrollbar-thin scrollbar-thumb-[#1e3058]">
          <div className="p-3 space-y-3">

            {/* Server Header */}
            <div className="flex items-center justify-between p-3 bg-[#071020] rounded-lg border border-[#1a2540]">
              <div className="flex flex-col gap-0.5">
                <span className="text-[9px] font-bold text-[#3d6a9e] uppercase tracking-[0.12em]">
                  Server
                </span>
                <Typography variant="h6" className="text-[13px] font-semibold text-[#c8d8f0]">
                  Workflow
                </Typography>
              </div>
              <div className={`flex items-center gap-1.5 px-2 py-1 rounded-md border ${isConnected ? 'bg-[#1a4a3a]/40 border-[#1e5a3a]' : 'bg-[#1a2540]/50 border-[#1e3058]'}`}>
                <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-[#34d399]' : 'bg-[#3d6a9e]'}`} />
                <span className={`text-[10px] font-medium ${isConnected ? 'text-[#34d399]' : 'text-[#5a8ab8]'}`}>
                  {isConnected ? 'Connected' : 'Disconnected'}
                </span>
              </div>
            </div>

            {/* Model Settings Section */}
            <div className="bg-[#071020] border border-[#1a2540] rounded-lg p-3 space-y-3">
              <h3 className="text-[9px] font-bold uppercase tracking-[0.12em] text-[#3d6a9e]">
                Model
              </h3>

              {/* Labels Section */}
              <div className="flex w-full items-center justify-between">
                <label className="text-[11px] font-medium text-[#8ab4e0]">
                  Labels<span className="text-[#1e3058] ml-0.5">:</span>
                </label>
                <button
                  type="button"
                  onClick={addLabel}
                  aria-label="Add Label"
                  className="flex h-5 w-5 cursor-pointer items-center justify-center rounded-full border border-[#1e3058] bg-transparent text-[11px] text-[#4a9eff] transition-all duration-200 hover:border-[#4a9eff]/50 hover:bg-[#4a9eff]/10 active:scale-90"
                >
                  +
                </button>
              </div>

              {/* Dynamic Labels */}
              <div className="space-y-1.5">
                {labels.map(labelObj => (
                  <div key={labelObj.id} className="group flex items-center gap-2">
                    <label className="min-w-[60px] whitespace-nowrap text-[10px] font-medium text-[#5a8ab8]">
                      {labelObj.label}<span className="ml-0.5 text-[#1e3058]">:</span>
                    </label>
                    <input
                      id={`input-${labelObj.id}`}
                      type="text"
                      placeholder="Enter value..."
                      onBlur={e => handleLabelBlur(labelObj.id, e.target.value)}
                      className="flex-1 rounded-[3px] border border-[#1e3058] bg-[#071020] px-1.5 py-0.5 text-[11px] text-[#c8d8f0] outline-none transition-all duration-200 placeholder:text-[#3d6a9e] focus:border-[#4a9eff]/50 focus:ring-1 focus:ring-[#4a9eff]/20"
                    />
                    <button
                      type="button"
                      onClick={() => handleDeleteLabel(labelObj.id)}
                      aria-label={`Delete ${labelObj.label}`}
                      className="w-4 h-4 flex-shrink-0 flex items-center justify-center rounded-full text-[#1e3058] hover:text-[#e05a5a] hover:bg-[#e05a5a]/10 opacity-0 group-hover:opacity-100 transition-all duration-200 cursor-pointer"
                    >
                      <Icon name="old-trash" size="10" className="text-current pointer-events-none" />
                    </button>
                  </div>
                ))}
              </div>

              {/* Model Type Dropdown */}
              <div className="flex w-full items-center justify-between gap-2">
                <label className="whitespace-nowrap text-[11px] font-medium text-[#8ab4e0]">
                  Model Type<span className="text-[#1e3058] ml-0.5">:</span>
                </label>
                <div className="relative max-w-[160px] flex-1">
                  <select
                    value={modelConfig}
                    onChange={e => setModelConfig(e.target.value)}
                    className="w-full cursor-pointer appearance-none rounded-[3px] border border-[#1e3058] bg-[#071020] py-1 pl-2 pr-7 text-[11px] text-[#c8d8f0] outline-none transition-all duration-200 focus:border-[#4a9eff]/50 focus:ring-1 focus:ring-[#4a9eff]/20"
                  >
                    <option value="2d" className="bg-[#071020] text-[#c8d8f0]">2D</option>
                    <option value="3d_lowres" className="bg-[#071020] text-[#c8d8f0]">3D</option>
                    <option value="3d_fullres" className="bg-[#071020] text-[#c8d8f0]">3D Full Resolution</option>
                  </select>
                  <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2">
                    <Icon name="chevron-down" size="11" className="text-[#3d6a9e]" />
                  </div>
                </div>
              </div>

              {/* Number of Folds */}
              <div className="flex items-center justify-between">
                <label className="text-[11px] font-medium text-[#8ab4e0] whitespace-nowrap">
                  Number of Folds<span className="text-[#1e3058] ml-0.5">:</span>
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min="1"
                    max="5"
                    value={numFolds}
                    onChange={e => setNumFolds(Math.min(5, Math.max(1, Number(e.target.value))))}
                    className="w-12 bg-[#071020] border border-[#1e3058] rounded-[3px] py-1 text-[11px] text-[#c8d8f0] text-center outline-none cursor-pointer focus:border-[#4a9eff]/50 focus:ring-1 focus:ring-[#4a9eff]/20 transition-all duration-200 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                  />
                  <div className="relative group">
                    <div className="flex items-center justify-center w-4 h-4 rounded-full cursor-help text-[#3d6a9e] hover:text-[#4a9eff] transition-colors duration-200">
                      <Icon name="notifications-info" size="13" className="text-current" />
                    </div>
                    <div className="pointer-events-none absolute right-0 bottom-full mb-2 w-48 p-2.5 rounded-lg bg-[#101c35] border border-[#1e3058] opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-200 z-50">
                      <p className="text-[10px] text-[#8ab4e0] leading-relaxed">
                        Used for <span className="text-[#4a9eff] font-medium">k-fold cross-validation</span>. Increasing folds improves generalization but takes more computation.
                      </p>
                      <div className="absolute right-3 -bottom-[5px] w-2 h-2 bg-[#101c35] border-b border-r border-[#1e3058] rotate-45 -z-10" />
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Training Info Section */}
            <div className="bg-[#071020] border border-[#1a2540] rounded-lg p-3 space-y-2">
              <h3 className="text-[9px] font-bold uppercase tracking-[0.12em] text-[#3d6a9e] mb-2">
                Training Info
              </h3>

              <div className="flex items-center justify-between py-0.5">
                <span className="text-[11px] text-[#8ab4e0]">Training Studies:</span>
                <span className="text-[11px] font-medium text-[#c8d8f0]">{trainCount}</span>
              </div>

              <div className="flex items-center justify-between py-0.5">
                <span className="text-[11px] text-[#8ab4e0]">Validation Studies:</span>
                <span className="text-[11px] font-medium text-[#c8d8f0]">{validationCount}</span>
              </div>

              <div className="flex items-center justify-between mt-1.5 bg-[#0d1526] border border-[#1e3058] rounded-[3px] px-2.5 py-1.5">
                <span className="text-[11px] font-semibold text-[#8ab4e0]">Train/Validation Ratio:</span>
                <span className="text-[11px] font-bold text-[#4a9eff]">{`${trainCount} : ${validationCount}`}</span>
              </div>

              {/* Train Button */}
              <button
                type="button"
                onClick={() => {
                  if (isTraining) {
                    setIsTraining(false);
                  } else {
                    const { isValid } = validateLabels(labels);
                    if (isValid) setIsModalOpen(true);
                  }
                }}
                className={`w-full mt-3 py-2 rounded-[3px] font-semibold text-[11px] transition-all duration-200 active:scale-[0.98] cursor-pointer border-none ${isTraining
                    ? 'bg-[#e05a5a]/20 text-[#e05a5a] border border-[#e05a5a]/30 hover:bg-[#e05a5a]/30'
                    : 'bg-[#1a4a8a] text-[#c8d8f0] hover:bg-[#1e5aaa]'
                  }`}
              >
                {isTraining ? 'Stop Training' : 'Train Model'}
              </button>
            </div>

            {/* Model Progress Section */}
            <div className="bg-[#071020] border border-[#1a2540] rounded-lg p-3 space-y-3">
              <h3 className="text-[9px] font-bold uppercase tracking-[0.12em] text-[#3d6a9e]">
                Model Progress
              </h3>

              {/* Training Progress Bar */}
              <div className="flex items-center gap-3">
                <span className="w-24 text-[11px] text-[#8ab4e0] flex-shrink-0">Training Progress</span>
                <div className="relative h-2 w-full rounded-full bg-[#0d1526] border border-[#1e3058] overflow-hidden">
                  <div
                    className="absolute top-0 left-0 h-full rounded-full transition-all duration-500"
                    style={{ width: `0%`, background: 'linear-gradient(to right, #f97316, #eab308)' }}
                  />
                  <div className="absolute inset-0 flex items-center justify-center text-[9px] font-bold text-[#c8d8f0]">
                    0%
                  </div>
                </div>
              </div>

              {/* Dice Metric Progress Bar */}
              <div className="flex items-center gap-3">
                <span className="w-24 text-[11px] text-[#8ab4e0] flex-shrink-0">Dice Metric</span>
                <div className="relative h-2 w-full rounded-full bg-[#0d1526] border border-[#1e3058] overflow-hidden">
                  <div
                    className="absolute top-0 left-0 h-full rounded-full transition-all duration-500"
                    style={{ width: `0%`, background: 'linear-gradient(to right, #e05a5a, #22c55e)' }}
                  />
                  <div className="absolute inset-0 flex items-center justify-center text-[9px] font-bold text-[#c8d8f0]">
                    0%
                  </div>
                </div>
              </div>
            </div>

          </div>
        </div>
      </div>

      {/* Training Summary Modal */}
      <Modal
        isOpen={isModalOpen}
        onRequestClose={() => setIsModalOpen(false)}
        contentLabel="Training Summary"
        style={{
          overlay: {
            backgroundColor: 'rgba(0, 0, 0, 0.7)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backdropFilter: 'blur(4px)',
            zIndex: 50,
          },
          content: {
            width: '380px',
            padding: '20px',
            borderRadius: '8px',
            backgroundColor: '#0d1526',
            border: '1px solid #1a2540',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            textAlign: 'center',
            boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.6)',
          },
        }}
      >
        <h2 className="mb-5 text-[14px] font-bold text-[#c8d8f0]">Training Summary</h2>
        <div className="w-full space-y-2 text-left mb-5">
          <div className="flex justify-between px-1.5 py-1 border-b border-[#1a2540]">
            <span className="text-[#5a8ab8] text-[11px]">Model Type:</span>
            <span className="text-[#c8d8f0] text-[11px] font-medium">{modelConfigDict[modelConfig]}</span>
          </div>
          <div className="flex justify-between px-1.5 py-1 border-b border-[#1a2540]">
            <span className="text-[#5a8ab8] text-[11px]">Number of Folds:</span>
            <span className="text-[#c8d8f0] text-[11px] font-medium">{numFolds}</span>
          </div>
          <div className="flex justify-between px-1.5 py-1 border-b border-[#1a2540]">
            <span className="text-[#5a8ab8] text-[11px]">Labels:</span>
            <span className="text-[#c8d8f0] text-[11px] font-medium">
              {Object.keys(labelsDictionary).length > 0 ? Object.keys(labelsDictionary).join(', ') : 'No labels defined'}
            </span>
          </div>
          <div className="flex justify-between px-1.5 py-1">
            <span className="text-[#5a8ab8] text-[11px]">Total Studies:</span>
            <span className="text-[#c8d8f0] text-[11px] font-medium">{workflowStudies.length}</span>
          </div>
        </div>

        <div className="flex w-full gap-2">
          <button
            onClick={() => setIsModalOpen(false)}
            className="flex-1 py-2 rounded-[3px] bg-transparent border border-[#1e3058] text-[#5a8ab8] text-[11px] font-medium hover:border-[#4a9eff] hover:text-[#4a9eff] transition-colors cursor-pointer"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              setIsModalOpen(false);
              setIsTraining(true);
              train();
            }}
            className="flex-1 py-2 rounded-[3px] bg-[#1a4a8a] text-[#c8d8f0] text-[11px] font-bold hover:bg-[#1e5aaa] transition-colors cursor-pointer"
          >
            Proceed
          </button>
        </div>
      </Modal>
    </div>
  );
}

export default WorkflowDetailPage;