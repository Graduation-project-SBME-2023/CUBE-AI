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
import { useNavigate } from 'react-router-dom';
import { useLocation } from 'react-router-dom';
import { getMonaiBaseUrl } from '../../utils/runtimeUrls';

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
  extensionManager
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

    return new MonaiLabelClient(settings ? settings.url : getMonaiBaseUrl());
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
        const url = `${getMonaiBaseUrl()}/workflow/${encodeURIComponent(
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
      const url = `${getMonaiBaseUrl()}/workflow/${encodeURIComponent(taskName)}/get-train?study_uid=${encodeURIComponent(caseId)}`;
      const response = await fetch(
        url
      );
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
      const url = `${getMonaiBaseUrl()}/workflow/${encodeURIComponent(workflowDetails.workflowName)}/update-train?${queryParams}`;
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
    let notification = new UINotificationService();

    if (selectedStudies.length === 0) {
      notification.show({
        title: 'Delete Failed',
        message: 'Please select at least one study to delete.',
        type: 'warning',
        duration: 3000,
      });
      return;
    }

    // Prepare the records to be deleted
    const recordsToDelete = selectedStudies.map(studyInstanceUid => ({
      study_uid: studyInstanceUid,
      task_name: workflowName,
    }));

    try {
      const response = await fetch(`${getMonaiBaseUrl()}/workflow/delete-multiple`, {
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
      selectedStudies.forEach(studyInstanceUid => {
        g_workflowManagerInstance.deleteStudyFromWorkflow(workflowName, studyInstanceUid);
      });
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
      const url = `${getMonaiBaseUrl()}/workflow/${encodeURIComponent(workflowName)}/train`;
      const fetchedWorkflowDataResponse = await fetch(
        url,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_name: workflowName }),
        }
      );
      let fetchedData = await fetchedWorkflowDataResponse.json();
      if (!fetchedWorkflowDataResponse.ok) {
        throw new Error(`Error: ${fetchedWorkflowDataResponse.status}`);
      }


      // try {
      //   const apiUrl = 'http://localhost:8000/NN_UNet/create_task';
      //   const body = {
      //     task_name: workflowDetails.workflowName,
      //     channel_names: modalityDict,
      //     labels: labelsDictionary,
      //     images: images,
      //   };
      //   const response = await fetch(apiUrl, {
      //     method: 'POST',
      //     headers: {
      //       'Content-Type': 'application/json',
      //     },
      //     body: JSON.stringify({
      //       task_name: body.task_name,
      //       channel_names: body.channel_names,
      //       labels: body.labels,
      //       images: body.images,
      //     }),
      //   });
      //   if (!response.ok) {
      //     throw new Error(`Error: ${response.status}`);
      //   }
      //   const result = await response.json();
      //   console.log('Process Data Response:', result);
      //   notification.show({
      //     title: 'Process Status',
      //     message: 'Data is Processed Successfully',
      //     type: 'success',
      //     duration: 5000,
      //   });
      // } catch (error) {
      //   console.error('Error processing data:', error);
      // }

      const queryParams = new URLSearchParams({
        model_name: workflowDetails.workflowName,
        labels: JSON.stringify(labelsDictionary),
        fold: numFolds,
        plan_type: modelConfig,
      }).toString();
      console.log('Query Params:', queryParams);
      try {
        // Construct query string

        const response = await fetch(`${getMonaiBaseUrl()}/create-model?${queryParams}`, {
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
          const response = await fetch(`${getMonaiBaseUrl()}/train/${workflowName}`, {
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
      pathname: '/worklist',
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
          seriesTableColumns={
            {}
          }
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
    <div className="h-screen bg-gray-900">
      <div className="bg-gray-900 shadow-md">
        <div className="bg-secondary-dark flex items-center justify-start space-x-4 px-6">
          <button
            onClick={onClickReturnButton}
            className="text-white hover:text-gray-300 focus:outline-none"
            aria-label="Go back"
          >
            <Icon
              name="chevron-left"
              className="text-primary-active w-8"
            />
          </button>
          <Typography
            variant="h4"
            className="py-3 text-lg font-light text-white"
          >
            {workflowName}
          </Typography>
        </div>
      </div>

      <div
        className="flex"
        style={{ height: 'calc(100vh - 64px)' }}
      >
        {/* Left Panel containing the "Studies", "Select All", and "Delete" buttons */}
        <div className="flex-1 overflow-y-auto bg-gray-900 p-6">
          {/* Top section with Studies label and buttons */}
          <div className="mb-6 flex items-center justify-between">
            {/* Styled "Studies" label */}
            {/* <h1 className="font-large text-lg text-white">Studies</h1> */}
            <Typography
              variant="h5"
              className="text-lg font-light text-white"
            >
              Studies
            </Typography>
            {/* Positioned Clear button inside the left panel */}
            <div className="space-x-2">
              <LegacyButton
                rounded="full"
                variant="outlined"
                color="primaryActive"
                border="primaryActive"
                onClick={handleSelectAll}
              >
                {selectAll ? 'Unselect All' : 'Select All'}
              </LegacyButton>

              {/*NEWWWW STYLE*/}
              <LegacyButton
                rounded="full"
                variant="outlined"
                color="white"
                style={{ backgroundColor: '#c4251a' }}
                // border="red"
                className="border-none  text-white hover:bg-red-700" // Custom Tailwind classes for further control
                onClick={handleDeletion}
              >
                {t('Delete')}
              </LegacyButton>
            </div>
          </div>
          <div className="max-h-full ">
            <div>
              <TableHeader tableHeader={tableHeader} />
            </div>
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
        {/* Right Panel */}
        <div
          className="flex-2 h-full overflow-y-auto"
          style={{
            backgroundColor: '#789',
            borderTopLeftRadius: '6px',
            borderTopRightRadius: '6px',
          }}
        >
          <div
            className="rounded-lg shadow-md"
            style={{ backgroundColor: '#789', padding: '0px' }}
          >
            {/* Header */}
            <Typography
              variant="h6"
              className="bg-primary-dark mb-4 p-3 text-center text-lg"
              style={{
                color: '#348cfd',
                fontSize: '1rem',
                borderTopLeftRadius: '6px', // Match the container's rounded corners
                borderTopRightRadius: '6px',
              }}
            >
              Workflow Server
            </Typography>

            {/* Content */}
            <div className="flex flex-col items-center space-y-4 p-3">
              {/* Dynamic "Connected" label */}
              <Typography
                variant="subtitle"
                style={{
                  color: isConnected ? '#1fd655' : '#484f48',
                  fontSize: '12px',
                  fontWeight: 'bold',
                  backgroundColor: '#789',
                }}
              >
                {isConnected ? 'Connected' : 'Not Connected'}
              </Typography>
            </div>
            {/* Model Section */}
            <div
              className="rounded-lg shadow-md"
              style={{ backgroundColor: '#737678', width: '100%' }}
            >
              {/* Model Info Header */}
              <div
                className="py-2 text-left text-white"
                style={{
                  fontSize: '13px',
                  paddingLeft: '10px',
                  width: '100%',
                  backgroundColor: 'black',
                  borderTopLeftRadius: '8px', // Match the container's rounded corners
                  borderTopRightRadius: '8px',
                }}
              >
                Model
              </div>
              <div style={{ margin: '10px' }}>
                {/* Labels Header with Add Button */}
                <div className="mb-3 flex w-full items-center justify-between">
                  <label
                    style={{
                      fontSize: '13px',
                      color: 'black',
                      textAlign: 'center',
                    }}
                  >
                    Labels :
                  </label>
                  <button
                    className="customButton"
                    onClick={addLabel}
                    style={{
                      width: '25px',
                      height: '25px',
                      color: 'black',
                      border: '1px solid black',
                      borderRadius: '50%',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      backgroundColor: '#add8e6',
                      transition: 'background-color 0.3s ease',
                    }}
                  >
                    +
                  </button>
                </div>

                {/* Render Dynamically Added Labels */}
                {labels.map(labelObj => (
                  <div
                    key={labelObj.id}
                    className="mb-2 flex items-center"
                  >
                    <label
                      style={{
                        fontSize: '11px',
                        color: 'black',
                        marginTop: '3px',
                        paddingLeft: '10px',
                        marginBottom: '10px',
                      }}
                    >
                      {labelObj.label}:
                    </label>
                    <input
                      id={`input-${labelObj.id}`}
                      type="text"
                      placeholder="Enter Label"
                      onBlur={e => handleLabelBlur(labelObj.id, e.target.value)} // Update dictionary only when input loses focus
                      style={{
                        fontSize: '11px',
                        padding: '2px 8px',
                        border: '1px solid #ccc',
                        borderRadius: '4px',
                        outline: 'none',
                        backgroundColor: 'white',
                        color: 'black',
                        marginBottom: '10px',
                      }}
                    />
                    {/* Delete Button */}
                    <button
                      onClick={() => handleDeleteLabel(labelObj.id)}
                      className="bg-red flex items-center justify-center rounded-full p-1 hover:bg-red-600 "
                      style={{
                        width: '20px',
                        height: '20px',
                        marginBottom: '10px',
                        marginLeft: '5px',
                      }}
                    >
                      <Icon
                        name="old-trash"
                        className="text-sm text-red-600 hover:text-white"
                      />
                    </button>
                  </div>
                ))}
                <div className="flex w-full items-center justify-between">
                  <label
                    style={{
                      fontSize: '13px',
                      color: 'black',
                      textAlign: 'center',
                    }}
                  >
                    Model Type:
                  </label>
                  <select
                    value={modelConfig}
                    onChange={e => setModelConfig(e.target.value)}
                    style={{
                      fontSize: '11px',
                      padding: '2px 6px',
                      border: '1px solid #ccc',
                      borderRadius: '4px',
                      outline: 'none',
                      backgroundColor: 'white',
                      color: 'black',
                    }}
                  >
                    <option value="2d">2D</option>
                    <option value="3d_lowres">3D</option>
                    <option value="3d_fullres">3D Full Resolution</option>
                  </select>
                </div>
              </div>
              {/* Number of Folds Section */}
              <div
                style={{
                  margin: '10px',
                  position: 'relative',
                  display: 'flex',
                  flexDirection: 'column',
                }}
              >
                <div className="mt-2 mb-3 flex items-center justify-between">
                  <label
                    style={{
                      fontSize: '13px',
                      color: 'black',
                      textAlign: 'left',
                    }}
                  >
                    Number of Folds:
                  </label>
                  <input
                    type="number"
                    min="1"
                    max="5"
                    value={numFolds}
                    onChange={e => setNumFolds(Number(e.target.value))}
                    style={{
                      fontSize: '11px',
                      padding: '2px 6px',
                      width: '60px',
                      border: '1px solid #ccc',
                      borderRadius: '4px',
                      outline: 'none',
                      backgroundColor: 'white',
                      color: 'black',
                      textAlign: 'center',
                    }}
                  />
                  {/* Info Icon */}
                  <div
                    style={{
                      position: 'relative',
                      display: 'inline-block',
                      cursor: 'pointer',
                      marginLeft: '10px',
                    }}
                    title="Folds are used for k-fold cross-validation, splitting the data into multiple subsets for training and validation. Increasing folds improves generalization but takes more computation."
                  >
                    <span>
                      <Icon
                        name="notifications-info"
                        className="text-white"
                        style={{
                          width: '20px',
                          height: '20px',
                        }}
                      />
                    </span>
                  </div>
                </div>
              </div>
            </div>
            <div
              className="rounded-lg shadow-md"
              style={{ backgroundColor: '#737678', width: '100%' }}
            >
              <div
                className="py-2 text-left text-white"
                style={{
                  fontSize: '13px',
                  paddingLeft: '10px',
                  width: '100%',
                  backgroundColor: 'black',
                  borderTopLeftRadius: '8px',
                  borderTopRightRadius: '8px',
                }}
              >
                Training Info
              </div>
              <div className="flex flex-col p-1">
                {/* Training Count */}
                <div className="flex items-center justify-between">
                  <span
                    className="text-black-500 p-2 "
                    style={{ fontSize: '13px' }}
                  >
                    Training Studies:
                  </span>
                  <span
                    className=" text-black-500 p-2 "
                    style={{ fontSize: '13px' }}
                  >
                    {trainCount}
                  </span>
                </div>

                {/* Validation Count */}
                <div className="flex items-center justify-between">
                  <span
                    className="text-black-500 p-2 "
                    style={{ fontSize: '13px' }}
                  >
                    Validation Studies:
                  </span>
                  <span
                    className="text-black-500 p-2 "
                    style={{ fontSize: '13px' }}
                  >
                    {validationCount}
                  </span>
                </div>

                {/* Training/Validation Ratio */}
                <div className="w-100 flex items-center justify-between rounded-lg bg-gray-300 px-2 py-1">
                  <span className="p-2  text-sm font-semibold text-gray-800">
                    Train/Validation Ratio:
                  </span>
                  <span className="p-2 text-sm text-sm font-semibold text-gray-800">
                    {`${trainCount} : ${validationCount}`}
                  </span>
                </div>
              </div>
              <div
                className="mt-4 flex justify-center"
                style={{ margin: '10px' }}
              >
                <LegacyButton
                  onClick={() => {
                    if (isTraining) {
                      setIsTraining(false);
                      console.log('Training stopped');
                    } else {
                      const { isValid } = validateLabels(labels);
                      if (isValid) {
                        setIsModalOpen(true);
                      }
                    }
                  }}
                  rounded="full"
                  variant="contained"
                  color="white"
                  border="black"
                  size="medium"
                  style={{ backgroundColor: '#add8e6', marginBottom: '10px' }}
                >
                  {isTraining ? 'Stop' : 'Train'}
                </LegacyButton>
                <Modal
                  isOpen={isModalOpen}
                  onRequestClose={() => setIsModalOpen(false)}
                  contentLabel="Training Summary"
                  style={{
                    overlay: {
                      backgroundColor: 'rgba(0, 0, 0, 0.5)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    },
                    content: {
                      width: '400px',
                      padding: '20px',
                      borderRadius: '10px',
                      backgroundColor: 'white',
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      textAlign: 'center',
                    },
                  }}
                >
                  <h2 className="mb-4 text-center text-xl font-bold">Training Summary</h2>
                  <p>
                    <strong>Model Type:</strong> {modelConfigDict[modelConfig]}
                  </p>
                  <p>
                    <strong>Number of Folds:</strong> {numFolds}
                  </p>
                  <p>
                    <strong>Labels:</strong>{' '}
                    {Object.keys(labelsDictionary).length > 0
                      ? Object.keys(labelsDictionary).join(', ')
                      : 'No labels defined'}
                  </p>
                  <p>
                    <strong>Total Number of Studies:</strong> {workflowStudies.length}
                  </p>
                  <div className="mt-4 flex w-full justify-between">
                    {' '}
                    <button
                      onClick={() => setIsModalOpen(false)}
                      className="rounded bg-gray-400 px-4 py-2 text-white hover:bg-gray-500"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={() => {
                        setIsModalOpen(false);
                        setIsTraining(true); // Training starts
                        train();
                        console.log('Training started');
                      }}
                      className="rounded bg-blue-500 px-4 py-2 text-white hover:bg-blue-600"
                    >
                      Proceed
                    </button>
                  </div>
                </Modal>
              </div>
            </div>
            <div
              className="rounded-lg shadow-md"
              style={{ backgroundColor: '#737678', width: '100%' }}
            >
              <div
                className="py-2 text-left text-white"
                style={{
                  fontSize: '13px',
                  paddingLeft: '10px',
                  width: '100%',
                  backgroundColor: 'black',
                  borderTopLeftRadius: '8px',
                  borderTopRightRadius: '8px',
                }}
              >
                Model Progress
              </div>

              {/* Progress Bars Container */}
              <div className="space-y-4 p-4">
                {/* Training Progress Bar */}
                <div className="flex items-center space-x-4">
                  {/* Label */}
                  <span
                    className="w-28  text-black"
                    style={{ fontSize: '13px' }}
                  >
                    Training Progress
                  </span>
                  {/* Progress Bar */}
                  <div className="relative h-3 w-full rounded-full bg-gray-300">
                    <div
                      className="absolute top-0 left-0 flex h-full items-center justify-center rounded-full text-sm text-black"
                      style={{
                        width: `${0}%`,
                        background: `linear-gradient(to right, red, yellow ${0}%, green)`,
                      }}
                    ></div>
                    <div className="absolute inset-0 flex items-center justify-center text-xs font-semibold text-black">
                      {0}%
                    </div>
                  </div>
                </div>

              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default WorkflowDetailPage;
