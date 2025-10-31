import os
import time
from datetime import datetime
import logging
from pathlib import Path
from typing import Dict, Optional, List, Tuple, Union, Any
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype, is_datetime64_any_dtype, is_categorical_dtype
import matplotlib.pyplot as plt
import seaborn as sns
from PIL import Image
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import SimpleITK as sitk
from sklearn.feature_selection import SelectKBest, f_classif, f_regression, mutual_info_classif, mutual_info_regression, RFE
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.preprocessing import LabelEncoder
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    confusion_matrix, roc_auc_score, roc_curve, auc,
    classification_report, mean_squared_error, mean_absolute_error,
    r2_score, explained_variance_score, mean_absolute_percentage_error
)

class PyCaretRegressionTool:
    name = "pycaret_regression"
    description = """
    This tool uses PyCaret to train and evaluate regression models.
    It compares multiple models, tunes the best ones, creates a blended model,
    and generates various visualizations and interpretations.
    Results are saved to the specified output directory.
    """
    
    inputs = {
        "input_path": {
            "type": "string",
            "description": "Path to input data file (CSV format)"
        },
        "output_dir": {
            "type": "string",
            "description": "Output directory where model and results will be saved"
        },
        "target_column": {
            "type": "string",
            "description": "Name of the target column for regression"
        },
        "experiment_name": {
            "type": "string",
            "description": "Name of the experiment",
            "required": False,
            "nullable": True
        },
        "fold": {
            "type": "integer",
            "description": "Number of cross-validation folds",
            "required": False,
            "nullable": True
        },
        "session_id": {
            "type": "integer",
            "description": "Random seed for reproducibility",
            "required": False,
            "nullable": True
        },
        "use_gpu": {
            "type": "boolean",
            "description": "Whether to use GPU for training (if available)",
            "required": False,
            "nullable": True
        },
        "data_split_stratify": {
            "type": "boolean",
            "description": "Whether to use stratified sampling for data splitting",
            "required": False,
            "nullable": True
        },
        "data_split_shuffle": {
            "type": "boolean",
            "description": "Whether to shuffle data before splitting",
            "required": False,
            "nullable": True
        },
        "preprocess": {
            "type": "boolean",
            "description": "Whether to apply preprocessing steps",
            "required": False,
            "nullable": True
        },
        "ignore_features": {
            "type": "string",
            "description": "Comma-separated list of features to ignore during training",
            "required": False,
            "nullable": True
        },
        "numeric_features": {
            "type": "string",
            "description": "Comma-separated list of numeric features",
            "required": False,
            "nullable": True
        },
        "categorical_features": {
            "type": "string",
            "description": "Comma-separated list of categorical features",
            "required": False,
            "nullable": True
        },
        "date_features": {
            "type": "string",
            "description": "Comma-separated list of date features",
            "required": False,
            "nullable": True
        },
        "n_select": {
            "type": "integer",
            "description": "Number of top models to select for blending",
            "required": False,
            "nullable": True
        },
        "normalize": {
            "type": "boolean",
            "description": "Whether to normalize numeric features",
            "required": False,
            "nullable": True
        },
        "transformation": {
            "type": "boolean",
            "description": "Whether to apply transformation to numeric features",
            "required": False,
            "nullable": True
        },
        "pca": {
            "type": "boolean",
            "description": "Whether to apply PCA for dimensionality reduction",
            "required": False,
            "nullable": True
        },
        "pca_components": {
            "type": "number",
            "description": "Number of PCA components (float between 0-1 or int > 1)",
            "required": False,
            "nullable": True
        },
        "include_models": {
            "type": "string",
            "description": "Comma-separated list of models to include in comparison",
            "required": False,
            "nullable": True
        },
        "exclude_models": {
            "type": "string",
            "description": "Comma-separated list of models to exclude from comparison",
            "required": False,
            "nullable": True
        },
        "test_data_path": {
            "type": "string",
            "description": "Path to test/holdout data for independent evaluation",
            "required": False,
            "nullable": True
        },
        "ignore_gpu_errors": {
            "type": "boolean",
            "description": "Whether to ignore GPU-related errors and fall back to CPU",
            "required": False,
            "nullable": True
        }
    }
    
    output_type = "object"

    def forward(
        self,
        input_path: str,
        output_dir: str,
        target_column: str,
        experiment_name: Optional[str] = None,
        fold: Optional[int] = 10,
        session_id: Optional[int] = None,
        use_gpu: Optional[bool] = False,
        data_split_stratify: Optional[bool] = False,  # Usually False for regression
        data_split_shuffle: Optional[bool] = True,
        preprocess: Optional[bool] = True,
        ignore_features: Optional[str] = None,
        numeric_features: Optional[str] = None,
        categorical_features: Optional[str] = None,
        date_features: Optional[str] = None,
        n_select: Optional[int] = 3,
        normalize: Optional[bool] = True,  # Usually True for regression
        transformation: Optional[bool] = True,  # Usually True for regression
        pca: Optional[bool] = False,
        pca_components: Optional[float] = None,
        include_models: Optional[str] = None,
        exclude_models: Optional[str] = None,
        test_data_path: Optional[str] = None,
        ignore_gpu_errors: Optional[bool] = True
    ):
        """
        Train and evaluate regression models using PyCaret.
        
        Args:
            input_path: Path to input CSV file with training data
            output_dir: Directory to save outputs
            target_column: Target column name for regression
            experiment_name: Name of the experiment
            fold: Number of cross-validation folds
            session_id: Random seed for reproducibility
            use_gpu: Whether to use GPU for training
            data_split_stratify: Whether to use stratified sampling
            data_split_shuffle: Whether to shuffle data before splitting
            preprocess: Whether to apply preprocessing steps
            ignore_features: Comma-separated list of features to ignore
            numeric_features: Comma-separated list of numeric features
            categorical_features: Comma-separated list of categorical features
            date_features: Comma-separated list of date features
            n_select: Number of top models to select for blending
            normalize: Whether to normalize numeric features
            transformation: Whether to apply transformation to numeric features
            pca: Whether to apply PCA for dimensionality reduction
            pca_components: Number of PCA components
            include_models: Comma-separated list of models to include
            exclude_models: Comma-separated list of models to exclude
            test_data_path: Path to test/holdout data for independent evaluation
            ignore_gpu_errors: Whether to ignore GPU errors and fallback to CPU
            
        Returns:
            Dictionary with model training results and file paths
        """
        try:
            # Setup logging
            os.makedirs(output_dir, exist_ok=True)
            log_file = os.path.join(output_dir, "pycaret_regression.log")
            
            # Get logger and clear existing handlers
            logger = logging.getLogger()
            logger.setLevel(logging.INFO)
            for handler in logger.handlers[:]:
                logger.removeHandler(handler)
            
            # Create formatter and handlers
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler = logging.FileHandler(log_file)
            file_handler.setFormatter(formatter)
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            
            # Add handlers
            logger.addHandler(file_handler)
            logger.addHandler(console_handler)
            
            # Generate experiment name if not provided
            if experiment_name is None:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                experiment_name = f"regression_exp_{timestamp}"
            
            # Generate session_id if not provided
            if session_id is None:
                import random
                session_id = random.randint(1, 10000)
            
            logging.info(f"Starting regression experiment: {experiment_name}")
            logging.info(f"Input data: {input_path}")
            logging.info(f"Output directory: {output_dir}")
            logging.info(f"Target column: {target_column}")
            
            # Import PyCaret's regression module
            try:
                from pycaret.regression import (
                    setup, compare_models, tune_model, blend_models, 
                    pull, predict_model, save_model, load_model,
                    plot_model, interpret_model
                )
                
                # Check PyCaret version for compatibility adjustments
                import pycaret
                pycaret_version = getattr(pycaret, "__version__", "unknown")
                logging.info(f"PyCaret version: {pycaret_version}")
                
                logging.info("PyCaret imported successfully")
            except ImportError as e:
                logging.error(f"Error importing PyCaret: {str(e)}")
                logging.error("Please install PyCaret: pip install pycaret")
                raise ImportError("PyCaret is required for this tool. Please install it with: pip install pycaret")
            
            # Load data
            logging.info(f"Loading data from {input_path}")
            data = pd.read_csv(input_path)
            logging.info(f"Data loaded successfully: {data.shape[0]} rows, {data.shape[1]} columns")
            
            # Verify target column exists
            if target_column not in data.columns:
                raise ValueError(f"Target column '{target_column}' not found in the dataset")
            
            # Prepare parameters for setup
            setup_params = {
                'data': data,
                'target': target_column,
                'session_id': session_id,
                'experiment_name': experiment_name,
                'fold': fold,
                'use_gpu': use_gpu,
                'preprocess': preprocess,
                'data_split_shuffle': data_split_shuffle,
                'log_experiment': False  # Set to False to avoid MLflow errors
            }
            
            # Add stratify only if it's supported in regression context
            if data_split_stratify is not None:
                setup_params['data_split_stratify'] = data_split_stratify
            
            # Add optional parameters if provided
            if ignore_features:
                setup_params['ignore_features'] = [f.strip() for f in ignore_features.split(',')]
            
            if numeric_features:
                setup_params['numeric_features'] = [f.strip() for f in numeric_features.split(',')]
            
            if categorical_features:
                setup_params['categorical_features'] = [f.strip() for f in categorical_features.split(',')]
            
            if date_features:
                setup_params['date_features'] = [f.strip() for f in date_features.split(',')]
            
            if normalize is not None:
                setup_params['normalize'] = normalize
                
            if transformation is not None:
                setup_params['transformation'] = transformation
                
            if pca is not None:
                setup_params['pca'] = pca
                
            if pca_components is not None:
                setup_params['pca_components'] = pca_components
            
            # Check GPU availability and cuml installation if use_gpu is requested
            if use_gpu:
                try:
                    import cuml
                    logging.info("RAPIDS cuML is available for GPU acceleration")
                except ImportError:
                    logging.warning("'cuml' is not installed but use_gpu=True was specified.")
                    if ignore_gpu_errors:
                        logging.warning("Running on CPU instead. To use GPU, install cuml: pip install cuml")
                        use_gpu = False
                        setup_params['use_gpu'] = False
                    else:
                        raise ImportError("GPU acceleration requested but cuml is not installed. Install with: pip install cuml")
            
            # Set up the experiment with error handling for parameter compatibility
            logging.info("Setting up PyCaret experiment")
            logging.info(f"Setup parameters: {setup_params}")
            
            # Try to create setup with default parameters first
            try:
                s = setup(**setup_params)
                logging.info("PyCaret setup completed successfully")
            except TypeError as e:
                logging.warning(f"Setup error: {str(e)}")
                if "unexpected keyword argument" in str(e):
                    # Handle incompatible parameters by removing them and retrying
                    error_param = str(e).split("argument ")[-1].split("'")[1] if "'" in str(e) else str(e).split("argument ")[-1].strip()
                    logging.warning(f"Incompatible parameter detected: {error_param}")
                    logging.warning(f"Removing parameter and retrying setup")
                    
                    if error_param in setup_params:
                        del setup_params[error_param]
                        try:
                            s = setup(**setup_params)
                            logging.info("PyCaret setup completed successfully after parameter adjustment")
                        except Exception as inner_e:
                            logging.error(f"Setup failed even after removing parameter: {str(inner_e)}")
                            # Try a minimal setup as last resort
                            minimal_params = {
                                'data': data,
                                'target': target_column,
                                'session_id': session_id
                            }
                            logging.warning(f"Attempting minimal setup with just: {minimal_params.keys()}")
                            s = setup(**minimal_params)
                            logging.info("PyCaret setup completed with minimal parameters")
                    else:
                        raise
                else:
                    raise
            except Exception as e:
                logging.error(f"Unexpected setup error: {str(e)}")
                # Try a minimal setup as last resort
                minimal_params = {
                    'data': data,
                    'target': target_column,
                    'session_id': session_id
                }
                logging.warning(f"Attempting minimal setup with just: {minimal_params.keys()}")
                s = setup(**minimal_params)
                logging.info("PyCaret setup completed with minimal parameters")
            
            # Prepare parameters for compare_models
            compare_params = {'n_select': n_select}
            
            if include_models:
                compare_params['include'] = [m.strip() for m in include_models.split(',')]
                
            if exclude_models:
                compare_params['exclude'] = [m.strip() for m in exclude_models.split(',')]
            
            # Compare baseline models with compatibility handling
            logging.info(f"Comparing models (selecting top {n_select} models)")
            try:
                best = compare_models(**compare_params)
            except TypeError as e:
                if "unexpected keyword argument" in str(e):
                    # Handle incompatible parameters
                    error_param = str(e).split("argument ")[-1].split("'")[1] if "'" in str(e) else str(e).split("argument ")[-1].strip()
                    logging.warning(f"Incompatible parameter detected in compare_models: {error_param}")
                    logging.warning(f"Removing parameter and retrying")
                    
                    if error_param in compare_params:
                        del compare_params[error_param]
                        best = compare_models(**compare_params)
                    else:
                        raise
                else:
                    raise
            except Exception as e:
                logging.error(f"Error in compare_models: {str(e)}")
                # Try with minimal parameters
                logging.warning("Attempting compare_models with minimal parameters")
                best = compare_models()
            
            # If only one model is returned, wrap it in a list
            if not isinstance(best, list):
                best = [best]
                logging.info("Only one model was selected")
            
            logging.info(f"Top {len(best)} models selected")
            
            # Initialize list to track created files
            created_files = []
            
            # Compare models results
            compare_results = pull()
            compare_results_path = os.path.join(output_dir, 'model_comparison_results.csv')
            compare_results.to_csv(compare_results_path, index=True)
            created_files.append(('Model Comparison Results', compare_results_path))
            logging.info(f"Saved model comparison results to {compare_results_path}")
            
            # Define a function to create plots for a model
            def create_plots_for_model(model, plots_dir, created_files):
                # Store the original working directory
                original_dir = os.getcwd()
                
                try:
                    # Change to the plots directory before creating plots
                    os.chdir(plots_dir)
                    logging.info(f"Changed working directory to {plots_dir}")
                    
                    # Regression-specific plot types
                    plot_types = [
                        'residuals', 'error', 'cooks', 'learning',
                        'vc', 'feature', 'feature_all', 'parameter']
                    
                    model_plots = []
                    
                    # Try to create each plot type
                    for plot_type in plot_types:
                        try:
                            logging.info(f"Attempting to create {plot_type} plot in {plots_dir}")
                            
                            # Try to create the plot with save=True
                            plot_model(model, plot=plot_type, save=True)
                            
                            # Check if file was created
                            expected_filename = f"{plot_type}.png"
                            if os.path.exists(expected_filename):
                                full_path = os.path.join(plots_dir, expected_filename)
                                model_plots.append((f'Plot: {plot_type}', full_path))
                                logging.info(f"Successfully created {plot_type} plot at {full_path}")
                            else:
                                logging.warning(f"Plot {plot_type} was not found after creation")
                            
                        except Exception as e:
                            logging.warning(f"Failed to create {plot_type} plot: {str(e)}")
                    
                    return model_plots
                
                finally:
                    # Always restore the original working directory
                    os.chdir(original_dir)
                    logging.info(f"Restored working directory to {original_dir}")
            
            # Function to generate model interpretations
            def create_interpretations_for_model(model, plots_dir, created_files):
                # Store the original working directory
                original_dir = os.getcwd()
                
                try:
                    # Change to the plots directory before creating interpretations
                    os.chdir(plots_dir)
                    logging.info(f"Changed working directory to {plots_dir}")
                    
                    model_interpretations = []
                    
                    # Try to create summary interpretation
                    try:
                        logging.info("Attempting to create model interpretation summary")
                        interpret_model(model, plot='summary', save=True)
                        
                        # Check if file was created
                        expected_filename = "SHAP Summary.png"
                        if os.path.exists(expected_filename):
                            full_path = os.path.join(plots_dir, expected_filename)
                            model_interpretations.append(('Interpretation: summary', full_path))
                            logging.info(f"Successfully created interpretation summary at {full_path}")
                        else:
                            logging.warning("Interpretation summary file was not found after creation")
                            
                    except Exception as e:
                        logging.warning(f"Failed to create summary interpretation: {str(e)}")
                    
                    # Try to create correlation interpretation
                    try:
                        logging.info("Attempting to create model interpretation correlation")
                        interpret_model(model, plot='correlation', save=True)
                        
                        # Check if file was created
                        expected_filename = "SHAP Correlation.png"
                        if os.path.exists(expected_filename):
                            full_path = os.path.join(plots_dir, expected_filename)
                            model_interpretations.append(('Interpretation: correlation', full_path))
                            logging.info(f"Successfully created interpretation correlation at {full_path}")
                        else:
                            logging.warning("Interpretation correlation file was not found after creation")
                            
                    except Exception as e:
                        logging.warning(f"Failed to create correlation interpretation: {str(e)}")
                    
                    return model_interpretations
                
                finally:
                    # Always restore the original working directory
                    os.chdir(original_dir)
                    logging.info(f"Restored working directory to {original_dir}")
            
            # Function to evaluate model on test data
            def evaluate_model_on_test_data(model, output_path, model_name):
                try:
                    logging.info(f"Evaluating {model_name} on test data using PyCaret's inherent workflow")
                    
                    # Use PyCaret's inherent workflow for holdout predictions
                    # This uses the test split that was created during setup()
                    holdout_pred = predict_model(model)
                    
                    # Save the predictions
                    holdout_pred.to_csv(output_path, index=False)
                    logging.info(f"Saved {model_name} test predictions to {output_path}")
                    return output_path
                except Exception as e:
                    logging.error(f"Error evaluating {model_name} on test data: {str(e)}")
                    return None
            
            # Create a top-level models directory
            models_dir = os.path.join(output_dir, "models")
            os.makedirs(models_dir, exist_ok=True)
            
            # Dictionary to track all individual model paths
            individual_model_paths = {}
            model_test_predictions = {}
            
            # Tune and save each model individually
            tuned_models = []
            for i, model in enumerate(best):
                model_index = i + 1
                model_name = f"tuned_model_{model_index}"
                logging.info(f"Tuning model {model_index}/{len(best)}")
                
                try:
                    # Create a dedicated directory for this model
                    model_dir = os.path.join(models_dir, model_name)
                    os.makedirs(model_dir, exist_ok=True)
                    
                    # Tune the model
                    tuned_model = tune_model(model)
                    tuned_models.append(tuned_model)
                    
                    # Save tuned model results
                    tuned_results = pull()
                    tuned_results_path = os.path.join(model_dir, f'{model_name}_results.csv')
                    tuned_results.to_csv(tuned_results_path, index=True)
                    created_files.append((f'{model_name} Results', tuned_results_path))
                    logging.info(f"Saved {model_name} results to {tuned_results_path}")
                    
                    # Save the model itself
                    model_path = os.path.join(model_dir, f'{model_name}')
                    try:
                        save_model(tuned_model, model_path)
                        created_files.append((f'{model_name} Saved Model', model_path))
                        individual_model_paths[model_name] = model_path
                        logging.info(f"Saved {model_name} to {model_path}")
                    except Exception as e:
                        logging.error(f"Error saving {model_name}: {str(e)}")
                    
                    # Create plots directory for this model
                    plots_dir = os.path.join(model_dir, "plots")
                    os.makedirs(plots_dir, exist_ok=True)
                    
                    # Generate plots for this model
                    logging.info(f"Generating plots for {model_name}")
                    model_plots = create_plots_for_model(tuned_model, plots_dir, created_files)
                    created_files.extend(model_plots)
                    
                    # Generate interpretations for this model
                    logging.info(f"Generating interpretations for {model_name}")
                    model_interpretations = create_interpretations_for_model(tuned_model, plots_dir, created_files)
                    created_files.extend(model_interpretations)
                    
                    # Evaluate on holdout set using PyCaret's inherent workflow
                    test_pred_path = os.path.join(model_dir, f'independent_eval_results_{model_name}.csv')
                    test_result = evaluate_model_on_test_data(tuned_model, test_pred_path, model_name)
                    if test_result:
                        created_files.append((f'{model_name} Test Predictions', test_result))
                        model_test_predictions[model_name] = test_result
                    
                except Exception as e:
                    logging.error(f"Error processing {model_name}: {str(e)}")
            
            # Create a blended model if we have multiple tuned models
            blended_model = None
            
            if len(tuned_models) > 1:
                logging.info("Creating blended model from tuned models")
                try:
                    # Create dedicated directory for blended model
                    blended_dir = os.path.join(models_dir, "blended_model")
                    os.makedirs(blended_dir, exist_ok=True)
                    
                    # Create the blended model
                    blended_model = blend_models(tuned_models)
                    
                    # Save blended model results
                    blended_results = pull()
                    blended_results_path = os.path.join(blended_dir, 'blended_model_results.csv')
                    blended_results.to_csv(blended_results_path, index=True)
                    created_files.append(('Blended Model Results', blended_results_path))
                    logging.info(f"Saved blended model results to {blended_results_path}")
                    
                    # Save the blended model
                    blended_model_path = os.path.join(blended_dir, 'blended_model')
                    try:
                        save_model(blended_model, blended_model_path)
                        created_files.append(('Blended Model Saved Model', blended_model_path))
                        individual_model_paths["blended_model"] = blended_model_path
                        logging.info(f"Saved blended model to {blended_model_path}")
                    except Exception as e:
                        logging.error(f"Error saving blended model: {str(e)}")
                    
                    # Create plots directory for blended model
                    plots_dir = os.path.join(blended_dir, "plots")
                    os.makedirs(plots_dir, exist_ok=True)
                    
                    # Generate plots for blended model
                    logging.info("Generating plots for blended model")
                    blended_plots = create_plots_for_model(blended_model, plots_dir, created_files)
                    created_files.extend(blended_plots)
                    
                    # Generate interpretations for blended model
                    logging.info("Generating interpretations for blended model")
                    blended_interpretations = create_interpretations_for_model(blended_model, plots_dir, created_files)
                    created_files.extend(blended_interpretations)
                    
                    # Evaluate on holdout set using PyCaret's inherent workflow
                    blended_test_pred_path = os.path.join(blended_dir, 'independent_eval_results_blended_model.csv')
                    test_result = evaluate_model_on_test_data(blended_model, blended_test_pred_path, "blended_model")
                    if test_result:
                        created_files.append(('Blended Model Test Predictions', test_result))
                        model_test_predictions["blended_model"] = test_result
                    
                    final_model = blended_model
                except Exception as e:
                    logging.error(f"Error creating blended model: {str(e)}")
                    logging.info("Using the best tuned model as the final model")
                    final_model = tuned_models[0]
            else:
                logging.info("Only one model available, using it as the final model")
                final_model = tuned_models[0] if tuned_models else best[0]
            
            # Initialize final model path
            final_model_path = None
            
            # Save the final model to the main output directory as well
            final_model_path = os.path.join(output_dir, f'{experiment_name}_final_model')
            try:
                save_model(final_model, final_model_path)
                created_files.append(('Final Model', final_model_path))
                logging.info(f"Saved final model to {final_model_path}")
            except Exception as e:
                logging.error(f"Error saving final model: {str(e)}")
                final_model_path = None
            
            # Initialize summary path
            summary_path = None
            
            # Create summary report with links to all models and evaluations
            try:
                summary_path = os.path.join(output_dir, "model_summary.csv")
                summary_data = []
                
                # Add individual models to summary
                for i, model in enumerate(tuned_models):
                    model_name = f"tuned_model_{i+1}"
                    model_path = individual_model_paths.get(model_name, "Not saved")
                    test_pred_path = model_test_predictions.get(model_name, "Not available")
                    
                    summary_data.append({
                        "Model": model_name,
                        "Type": "Tuned Individual Model",
                        "Model Path": model_path,
                        "Test Predictions": test_pred_path
                    })
                
                # Add blended model to summary if it exists
                if blended_model is not None:
                    model_path = individual_model_paths.get("blended_model", "Not saved")
                    test_pred_path = model_test_predictions.get("blended_model", "Not available")
                    
                    summary_data.append({
                        "Model": "blended_model",
                        "Type": "Blended Model",
                        "Model Path": model_path,
                        "Test Predictions": test_pred_path
                    })
                
                # Write summary to CSV
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_csv(summary_path, index=False)
                created_files.append(('Model Summary', summary_path))
                logging.info(f"Created model summary at {summary_path}")
            except Exception as e:
                logging.error(f"Error creating model summary: {str(e)}")
                summary_path = None

            logging.info("Regression modeling completed successfully")

            return {
                "status": "success",
                "experiment_name": experiment_name,
                "input_path": input_path,
                "output_dir": output_dir,
                "final_model_path": final_model_path,
                "individual_model_paths": individual_model_paths,
                "model_test_predictions": model_test_predictions,
                "model_summary": summary_path,
                "log_file": log_file,
                "created_files": created_files
            }

        except Exception as e:
            logging.error(f"Error in PyCaret regression: {str(e)}", exc_info=True)
            return {
                "status": "error",
                "error_message": str(e),
                "input_path": input_path,
                "output_dir": output_dir
            }

