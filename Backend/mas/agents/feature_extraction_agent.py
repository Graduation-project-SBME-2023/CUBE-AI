import os
import json
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Union, List, Dict, Any, Optional
import ast

from mas.core.base_agent import BaseAgent
from mas.tools.radiomics_tools import PyRadiomicsFeatureExtractionTool  
from mas.tools.feature_importance_tools import FeatureImportanceAnalysisTool
from mas.tools.modeling_classification_tool import PyCaretClassificationTool
from mas.tools.modeling_regression_tool import PyCaretRegressionTool
from autogen_ext.models.ollama import OllamaChatCompletionClient
from autogen_agentchat.agents import AssistantAgent
from autogen_core.tools import FunctionTool  


class RadiomicFeatureExtractorAndModellingAgent(BaseAgent):
    def __init__(self, name="radiomic_extractor", model_name="qwen3-coder:480b-cloud", config=None):
        super().__init__(name, config)

        self.model_client = OllamaChatCompletionClient(
            model=model_name,
            model_info={
                "vision": False,
                "function_calling": True,
                "json_output": False,
                "structured_output": False
            }
        )
    
        # tools : 
        self.radiomics_tool = PyRadiomicsFeatureExtractionTool()
        self.feature_importance_tool = FeatureImportanceAnalysisTool()
        self.modelling_classification_tool = PyCaretClassificationTool()
        self.modelling_regression_tool = PyCaretRegressionTool()

        async def extract_radiomic_features(
            image_dir: str,
            mask_dir: str,
            output_dir: str,
            feature_classes: Union[str, List[str]] = None,     # change original to union as function wraper add constrains to it
            specific_features: Union[str, List[str]] = None,   
            image_types: Union[str, List[str]] = "Original",   
            normalize: bool = True,
            resample: bool = True,
            n_workers: int = 1,
            targets_csv: Optional[str] = None,
            id_column: Optional[str] = None,
            target_column: Optional[str] = None,
        ) -> Dict[str, Any]:
            """
            Extract radiomic features from medical images and segmentation masks.
            """
            def safe_parse(value):
                if isinstance(value, str):
                    try:
                        parsed = ast.literal_eval(value)  # if value before was '['original']', this function see if it have python container syntax like (), []
                        if isinstance(parsed, list):     # after it will be ['original']
                            return parsed
                        else:   # so here it will be only str
                            return [parsed]
                    except Exception:
                        return [value]
                return value
            
            def run_extraction():
                kwargs = dict(
                    image_dir=image_dir,
                    mask_dir=mask_dir,
                    output_dir=output_dir,
                    feature_classes=feature_classes,
                    specific_features=specific_features,
                    image_types=image_types,
                    normalize=normalize,
                    resample=resample,
                    n_workers=n_workers
                )

                # only add targets info if all three provided
                if targets_csv and target_column:
                    kwargs.update({
                        "targets_csv": targets_csv,
                        "target_column": target_column
                    })
                return self.radiomics_tool.forward(**kwargs)

            feature_classes = safe_parse(feature_classes)
            specific_features = safe_parse(specific_features)
            image_types = safe_parse(image_types)

            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(pool, run_extraction)
            return result
   
        async def analyze_feature_importance(
            input_path: str,
            output_dir: str,
            target_column: str,
            task_type: Optional[str] = None,
            method: Optional[str] = "rf",
            top_features: Union[str, list] = "10",
            encode_categorical: Optional[str] = "auto",
            max_onehot_cardinality: Optional[int] = 10,
            create_plots: Optional[bool] = False,
            top_n_plot: Optional[int] = 30
        ) -> Dict[str, Any]:
            """
            Run the FeatureImportanceAnalysisTool asynchronously.
            """
            if isinstance(top_features, (list, tuple)):
                top_features_str = ",".join(map(str, top_features))
            else:
                top_features_str = str(top_features)

            def run_analysis():
                kwargs = dict(
                    input_path=input_path,
                    output_dir=output_dir,
                    target_column=target_column,
                    task_type=task_type,
                    method=method,
                    top_features=top_features_str,
                    encode_categorical=encode_categorical,
                    max_onehot_cardinality=max_onehot_cardinality,
                    create_plots=create_plots,
                    top_n_plot=top_n_plot
                )
                return self.feature_importance_tool.forward(**kwargs)

            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(pool, run_analysis)

            return result  
        
         
        async def classification_modelling(
            input_path: str,
            output_dir: str,
            target_column: str,
            experiment_name: Optional[str] = None,
            fold: Optional[int] = 2,
            session_id: Optional[int] = None,
            use_gpu: Optional[bool] = False,
            fix_imbalance: Optional[bool] = True,
            data_split_stratify: Optional[bool] = True,
            data_split_shuffle: Optional[bool] = True,
            preprocess: Optional[bool] = True,
            ignore_features: Optional[str] = None,
            numeric_features: Optional[str] = None,
            categorical_features: Optional[str] = None,
            date_features: Optional[str] = None,
            n_select: Optional[int] = 1,
            normalize: Optional[bool] = False,
            transformation: Optional[bool] = False,
            pca: Optional[bool] = False,
            pca_components: Optional[float] = None,
            include_models: Optional[str] = None,
            exclude_models: Optional[str] = None,
            test_data_path: Optional[str] = None,
            ignore_gpu_errors: Optional[bool] = True           
        )-> Dict[str, Any]:
            """
            Run the Classification Modelling tool asynchronously.
            """            
            def run_classification_modeling():
                kwargs = dict(
                    input_path=input_path,
                    output_dir=output_dir,
                    target_column=target_column,
                    experiment_name=experiment_name,
                    fold=fold,
                    session_id=session_id,
                    use_gpu=use_gpu,
                    fix_imbalance=fix_imbalance,
                    data_split_stratify=data_split_stratify,
                    data_split_shuffle=data_split_shuffle,
                    preprocess=preprocess,
                    ignore_features=ignore_features,
                    numeric_features=numeric_features,
                    categorical_features=categorical_features,
                    date_features=date_features,
                    n_select=n_select,
                    normalize=normalize,
                    transformation=transformation,
                    pca=pca,
                    pca_components=pca_components,
                    include_models=include_models,
                    exclude_models=exclude_models,
                    test_data_path=test_data_path,
                    ignore_gpu_errors=ignore_gpu_errors
                )
                return self.modelling_classification_tool.forward(**kwargs)
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(pool, run_classification_modeling)
            return result  
        
        
        async def regression_modelling(
            input_path: str,
            output_dir: str,
            target_column: str,
            experiment_name: Optional[str] = None,
            fold: Optional[int] = 2,
            session_id: Optional[int] = None,
            use_gpu: Optional[bool] = False,
            fix_imbalance: Optional[bool] = True,
            data_split_stratify: Optional[bool] = True,
            data_split_shuffle: Optional[bool] = True,
            preprocess: Optional[bool] = True,
            ignore_features: Optional[str] = None,
            numeric_features: Optional[str] = None,
            categorical_features: Optional[str] = None,
            date_features: Optional[str] = None,
            n_select: Optional[int] = 3,
            normalize: Optional[bool] = True,
            transformation: Optional[bool] = False,
            pca: Optional[bool] = False,
            pca_components: Optional[float] = None,
            include_models: Optional[str] = None,
            exclude_models: Optional[str] = None,
            test_data_path: Optional[str] = None,
            ignore_gpu_errors: Optional[bool] = True           
        )-> Dict[str, Any]:
            """
            Run the Regression Modelling tool asynchronously.
            """            
            def run_regression_modeling():
                kwargs = dict(
                    input_path=input_path,
                    output_dir=output_dir,
                    target_column=target_column,
                    experiment_name=experiment_name,
                    fold=fold,
                    session_id=session_id,
                    use_gpu=use_gpu,
                    fix_imbalance=fix_imbalance,
                    data_split_stratify=data_split_stratify,
                    data_split_shuffle=data_split_shuffle,
                    preprocess=preprocess,
                    ignore_features=ignore_features,
                    numeric_features=numeric_features,
                    categorical_features=categorical_features,
                    date_features=date_features,
                    n_select=n_select,
                    normalize=normalize,
                    transformation=transformation,
                    pca=pca,
                    pca_components=pca_components,
                    include_models=include_models,
                    exclude_models=exclude_models,
                    test_data_path=test_data_path,
                    ignore_gpu_errors=ignore_gpu_errors
                )
                return self.modelling_regression_tool.forward(**kwargs)
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                result = await loop.run_in_executor(pool, run_regression_modeling)
            return result              
        # wraper tools to autogen
        self.radiomics_tool_wrapper = FunctionTool(
            func=extract_radiomic_features,
            name="extract_radiomic_features",
            description="Extract radiomic features from images using PyRadiomics."
        )
        
        self.feature_importance_tool_wrapper = FunctionTool(
            func=analyze_feature_importance,
            name="analyze_feature_importance",
            description=" performs feature importance analysis on tabular data for classification or regression tasks to\
            identifie the most relevant features using various methods and outputs CSV files with selected features"
        )
        
        self.modelling_classification_tool_wrapper = FunctionTool(
            func=classification_modelling,
            description="PyCaret to train and evaluate classification models based on tabular data"  ,
            name="classification_modelling"
        )
      
        self.modelling_regression_tool_wrapper = FunctionTool(
            func=regression_modelling,
            description="PyCaret to train and evaluate regression models based on tabular data"  ,
            name="regression_modelling"
        )
       
        self.assistant = AssistantAgent(
            name="RadiomicAssistant",
            description=(
                "You are a medical imaging assistant specialized in radiomics and feature analysis. "
                "You MUST complete multi-step tasks fully. Never stop after just one tool call "
                "if the instructions require multiple sequential operations."
            ),
            system_message=(
                "You are a thorough assistant that completes ALL steps in a task. "
                "When given multi-step instructions:\n"
                "1. Execute each step sequentially\n"
                "2. Wait for each tool's result before proceeding\n"
                "3. Use outputs from previous steps as inputs to next steps\n"
                "4. Only finish when ALL steps are complete\n"
                "Always confirm you've completed all required steps before summarizing."
            ),
            model_client=self.model_client,
            tools=[self.radiomics_tool_wrapper,\
                self.feature_importance_tool_wrapper,\
                    self.modelling_classification_tool_wrapper,\
                        self.modelling_regression_tool_wrapper],
            max_tool_iterations = 10
        )
        
        self.logger = logging.getLogger(self.__class__.__name__)

    async def run(self, input_data: Union[str, Dict[str, Any]]):
        try:
            if isinstance(input_data, str): 
                with open(input_data, "r") as f:
                    input_data = json.load(f)     
            instruction = input_data.get("hypothesis", "")
            image_dir = input_data.get("image_dir", "")
            mask_dir = input_data.get("mask_dir", "")
            output_dir = input_data.get("output_dir", "")
            targets_csv = input_data.get("targets_csv", "")
            target_column = input_data.get("target_column", "")

            if not instruction:
                return {
                    "status": "require_input",
                    "description": "Please provide a radiomic task instruction.",
                    "key": "instruction",
                }

            if not all([image_dir, mask_dir, output_dir]):
                return {
                    "status": "require_input",
                    "description": "Missing directories: please provide image_dir, mask_dir, and output_dir."
                }

            self.state = "running"

            task_prompt = (
                f"Instruction: {instruction}\n"
                f"Image Directory: {image_dir}\n"
                f"Mask Directory: {mask_dir}\n"
                f"Output Directory: {output_dir}\n"
            )

            if targets_csv and target_column:
                task_prompt += (
                    f"Targets CSV: {targets_csv}\n"
                    f"Target Column: {target_column}\n"
                )

            # task_prompt += (
            #     "\n CRITICAL - YOU MUST COMPLETE ALL STEPS AND\n"
            #     "Whenever the agent generates an output file (like features, predictions, or reports),\n"
            #     "it should automatically update the JSON input file which is configuration.json \n"
            #     "by adding a new key-value pair:\n"
                
            #     "where the key is the file’s logical name (like top_10_features) and the value is the file path:\n"
            #     "Step 1: Decide which radiomic feature classes are needed (shape, firstorder, glcm, etc.) in array structure\n"
            #     "Step 2: Call the feature extraction tool with correct parameters.\n"
            #    " Step 3: AFTER Step  2 completes, you MUST call analyze_feature_importance with:\n"
            #     f"  - input_path: <path to CSV file from Step 2 result> \n"
            #     " use other suitable parameters needed also AND WOTOUT CREATING PLOTS !!.\n"
            #     " DO NOT stop after Step 2. You MUST complete All steps.\n"
            #     "step 4: AFTER Step 3 Complete you MUST call modelling tools choose the classification modelling or regression modelling based on the task_type from step 3.\n"
            #     "Call sutiable modelling function with :\n"
            #     f" - input_path : <path to CSV file of top_10_features from step 3 results>\n"
            #     "use other suitable parameters needed, IMPORTANT if number of data sample in the csv is small use 2 FOLDS"
            #     " DO NOT stop after Step 3. You MUST complete All steps.\n"
            #      "Step 5 : Only provide final summary after the third tools  have been executed.\n"
            # ) 
            task_prompt += (
                "\n CRITICAL EXECUTION GUIDELINES \n"
                "You are responsible for executing a complete multi-step radiomics pipeline. "
                "You MUST complete **all steps** in sequence, and update the configuration JSON file after every step that produces an output file.\n\n"

                "===== RULES =====\n"
                "1 Execute each step one by one, strictly in the following order.\n"
                "2 NEVER stop after a single tool call — continue until all steps are finished.\n"
                "3 For every step that generates an output file (for example, extracted features CSV, top features CSV, trained model, or results summary):\n"
                "     - Immediately open the configuration JSON file (located at 'configuration.json').\n"
                "     - Add or update a key-value pair using this format:\n"
                "           { \"<logical_name>\": \"<absolute_path_to_file>\" }\n"
                "       Examples:\n"
                "           { \"radiomics_features\": \"D:/data/output/features.csv\" }\n"
                "           { \"top_10_features\": \"D:/data/output/top_10_features.csv\" }\n"
                "           { \"final_model\": \"D:/data/output/final_model.pkl\" }\n"
                "     - Save the JSON file immediately after each addition (do not overwrite unrelated keys).\n"
                "     - Confirm the update in your output summary.\n"
                "4 Use file paths that actually exist and point to generated results.\n"
                "5 Do not generate plots unless explicitly asked.\n"
                "6 Only produce a summary message AFTER all steps are successfully completed and the JSON is updated.\n\n"

                "===== PIPELINE STEPS =====\n"
                "Step 1: Decide which radiomic feature classes are required (e.g., shape, firstorder, glcm, etc.) and pass them as an array.\n"
                "Step 2: Call the feature extraction tool (`extract_radiomic_features`) with correct parameters.\n"
                "        After this step, write the extracted features file path (CSV) into 'configuration.json' under key 'radiomics_features'.\n"
                "Step 3: Call the feature importance tool (`analyze_feature_importance`) using the features file from Step 2 as input.\n"
                "        Save the resulting top features CSV file path in the JSON under key 'top_10_features'.\n"
                "Step 4: Depending on the task type, call either `classification_modelling` or `regression_modelling`.\n"
                "        Use the 'top_10_features' file as input.\n"
                "        Save the trained model path or results CSV path in the JSON under 'final_model' or 'predictions'.\n"
                "Step 5: After completing all steps, provide a concise summary confirming successful execution and all updated JSON entries.\n\n"

                "You MUST ensure that the 'configuration.json' file always reflects the latest outputs from each stage of the pipeline."
            )
           
            result = await self.assistant.run(task=task_prompt)
            self.logger.info(f"=== Assistant Result ===\n{result}")
            
            self.state = "finished"

            return {"status": "finished", "result": result}

        except Exception as e:
            self.state = "error"
            self.logger.error(f"Error in radiomic agent: {e}", exc_info=True)
            return {
                "status": "error",
                "output": str(e),
                "type": type(e).__name__
            }
