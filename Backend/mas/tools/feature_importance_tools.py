import os
import re
import json
from datetime import datetime
import logging
from pathlib import Path
from typing import Dict, Optional, List, Tuple, Union, Any
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype, is_datetime64_any_dtype, is_categorical_dtype
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')
import seaborn as sns
from PIL import Image
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import SimpleITK as sitk
from radiomics import featureextractor
from sklearn.feature_selection import SelectKBest, f_classif, f_regression, mutual_info_classif, mutual_info_regression, RFE
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.preprocessing import LabelEncoder
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE
class FeatureImportanceAnalysisTool:
    name = "feature_importance_analysis"
    description = """
    This tool performs feature importance analysis on tabular data for classification or regression tasks.
    It identifies the most relevant features using various methods and outputs CSV files with selected features.
    Optionally generates visualization plots for feature importance and data distribution.
    """
    
    inputs = {
        "input_path": {
            "type": "string",
            "description": "Path to input data file (CSV format)"
        },
        "output_dir": {
            "type": "string",
            "description": "Output directory where results will be saved"
        },
        "target_column": {
            "type": "string",
            "description": "Name of the target column for prediction"
        },
        "task_type": {
            "type": "string",
            "description": "Type of machine learning task ('classification' or 'regression')",
            "required": False,
            "nullable": True
        },
        "method": {
            "type": "string",
            "description": "Feature selection method ('rf', 'f_test', 'mutual_info', 'rfe')",
            "required": False,
            "nullable": True
        },
        "top_features": {
            "type": "string",
            "description": "Comma-separated list of top features counts to select (e.g., '10,50,100')",
            "required": False,
            "nullable": True
        },
        "encode_categorical": {
            "type": "string",
            "description": "Method to encode categorical features ('auto', 'onehot', 'label', 'target', 'none')",
            "required": False,
            "nullable": True
        },
        "max_onehot_cardinality": {
            "type": "integer",
            "description": "Maximum unique values for one-hot encoding if encode_categorical='auto'",
            "required": False,
            "nullable": True
        },
        "create_plots": {
            "type": "boolean",
            "description": "Whether to generate visualization plots",
            "required": False,
            "nullable": True
        },
        "top_n_plot": {
            "type": "integer",
            "description": "Number of top features to show in importance plots",
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
        task_type: Optional[str] = None,
        method: Optional[str] = "rf",
        top_features: Optional[str] = "10",
        encode_categorical: Optional[str] = "auto",
        max_onehot_cardinality: Optional[int] = 10,
        create_plots: Optional[bool] = True,
        top_n_plot: Optional[int] = 30
    ):
        """
        Analyze feature importance for tabular data and output results.
        
        Args:
            input_path: Path to input CSV file
            output_dir: Directory to save results
            target_column: Target column name for prediction
            task_type: 'classification' or 'regression' (auto-detected if None)
            method: Feature selection method ('rf', 'f_test', 'mutual_info', 'rfe')
            top_features: Comma-separated list of top features to select
            encode_categorical: Method for encoding categorical features
            max_onehot_cardinality: Max unique values for one-hot encoding if auto
            create_plots: Whether to generate visualization plots
            top_n_plot: Number of top features to show in importance plots
            
        Returns:
            Dictionary with analysis results and file paths
        """
        # Validate input parameters
        self._validate_parameters(input_path, output_dir, target_column, task_type, method, 
                               encode_categorical, top_features)
        
        # Create output directory first
        os.makedirs(output_dir, exist_ok=True)
        
        # Set up logger and log file
        log_file = os.path.join(output_dir, "feature_importance_analysis.log")
        logger = self._setup_logging(log_file)
        
        try:
            logger.info(f"Starting feature importance analysis for {input_path}")
            logger.info(f"Target column: {target_column}")
            
            # Load data and prepare it for analysis
            X, y, original_df, X_original, detected_task_type = self._load_and_prepare_data(
                input_path, 
                target_column, 
                task_type,
                encode_categorical, 
                max_onehot_cardinality,
                logger
            )
            
            if task_type is None:
                task_type = detected_task_type
                logger.info(f"Auto-detected task type: {task_type}")
            
            # Parse the list of top features counts
            top_features_list = [int(n) for n in top_features.split(',')]
            top_features_list.sort()  # Ensure ascending order
            
            # Apply feature selection based on the method
            result_files = self._perform_feature_selection(
                X, y, original_df, X_original,
                output_dir, target_column, task_type,
                method, top_features_list, create_plots, top_n_plot,
                logger
            )
            
            logger.info("Feature importance analysis completed successfully")
            
            return {
                "status": "success",
                "input_path": input_path,
                "output_dir": output_dir,
                "target_column": target_column,
                "task_type": task_type,
                "method": method,
                "result_files": result_files,
                "feature_count": X.shape[1],
                "log_file": log_file
            }
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error in feature importance analysis: {error_msg}", exc_info=True)
            return {
                "status": "error",
                "error_message": error_msg,
                "input_path": input_path,
                "output_dir": output_dir,
                "log_file": log_file
            }
    
    def _validate_parameters(self, input_path, output_dir, target_column, task_type, method, 
                         encode_categorical, top_features):
        """
        Validate input parameters to catch errors early.
        
        Args:
            input_path: Path to input CSV file
            output_dir: Directory to save results
            target_column: Target column name
            task_type: Task type
            method: Feature selection method
            encode_categorical: Method to encode categorical features
            top_features: Comma-separated list of top features to select
        """
        # Check if input file exists and is a CSV
        if not os.path.exists(input_path):
            raise ValueError(f"Input file does not exist: {input_path}")
        
        if not input_path.lower().endswith('.csv'):
            raise ValueError(f"Input file must be a CSV file: {input_path}")
        
        # Validate task_type if provided
        if task_type is not None and task_type not in ['classification', 'regression']:
            raise ValueError(f"Invalid task_type: {task_type}. Must be 'classification' or 'regression'")
        
        # Validate method
        valid_methods = ['rf', 'f_test', 'mutual_info', 'rfe']
        if method not in valid_methods:
            raise ValueError(f"Invalid method: {method}. Must be one of {valid_methods}")
        
        # Validate encoding method
        valid_encodings = ['auto', 'onehot', 'label', 'target', 'none']
        if encode_categorical not in valid_encodings:
            raise ValueError(f"Invalid encode_categorical: {encode_categorical}. Must be one of {valid_encodings}")
        
        # Validate top_features format
        if not re.match(r'^(\d+,)*\d+$', top_features):
            raise ValueError(f"Invalid top_features format: {top_features}. Must be comma-separated integers (e.g., '10,50,100')")
    
    def _setup_logging(self, log_file):
        """
        Set up a logger specific to this instance to avoid modifying the root logger.
        
        Args:
            log_file: Path to log file
            
        Returns:
            Logger instance
        """
        # Create logger with unique name
        logger_name = f"feature_importance_{os.path.basename(log_file)}_{id(self)}"
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
        
        # Remove all existing handlers to avoid duplicates
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # Create file handler
        file_handler = logging.FileHandler(log_file, mode='w')  # 'w' mode to overwrite existing log
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(file_handler)
        
        # Make logger not propagate messages to parent loggers (root)
        # This prevents duplicate logs
        logger.propagate = False
        
        return logger
    
    def _load_and_prepare_data(
        self, 
        input_path: str, 
        target_column: str, 
        task_type: Optional[str],
        encode_categorical: str, 
        max_onehot_cardinality: int,
        logger: logging.Logger
    ) -> Tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.DataFrame, str]:
        """
        Load data, separate features from target, and handle categorical features.
        
        Args:
            input_path: Path to input CSV file
            target_column: Target column name
            task_type: Task type ('classification' or 'regression')
            encode_categorical: Method to encode categorical features
            max_onehot_cardinality: Max unique values for one-hot encoding
            logger: Logger instance
            
        Returns:
            Tuple of (X processed, y, original dataframe, X original, detected task type)
        """
        logger.info(f"Loading data from {input_path}")
        
        try:
            # Load data
#======================================edit and remove the subject_id column==========================================================            
            df = pd.read_csv(input_path)
            subject_id_column = "subject_id"
            if target_column not in df.columns:
                raise ValueError(f"Target column '{target_column}' not found in the dataset")
            
            # Separate features and target
        
            X = df.drop(columns=[target_column])
            if subject_id_column in X.columns:
                X.drop(columns=[subject_id_column])
            y = df[target_column]
            
            # Detect task type if not specified
            detected_task_type = self._detect_task_type(y) if task_type is None else task_type
            
            # Save original X for feature mapping
            X_original = X.copy()
            
            # Handle categorical features
            if encode_categorical != 'none':
                X = self._encode_categorical_features(X, y, target_column, encode_categorical, 
                                                 max_onehot_cardinality, input_path, logger)
            else:
                # Remove categorical columns if encoding is disabled
                cat_cols = X.select_dtypes(include=['object', 'category']).columns
                if len(cat_cols) > 0:
                    logger.warning(f"Removing {len(cat_cols)} categorical columns as encoding is disabled")
                    X = X.select_dtypes(include=[np.number])
            
            # Handle missing values
            if X.isna().any().any():
                logger.warning(f"Dataset contains missing values. Filling with appropriate values.")
                # For numeric columns, fill with median
                num_cols = X.select_dtypes(include=[np.number]).columns
                X[num_cols] = X[num_cols].fillna(X[num_cols].median())
                
                # For any remaining columns, fill with mode
                for col in X.columns:
                    if col not in num_cols:
                        X[col] = X[col].fillna(X[col].mode()[0] if not X[col].mode().empty else 0)
            
            logger.info(f"Data loaded successfully: {X.shape[0]} rows, {X.shape[1]} features")
            
            return X, y, df, X_original, detected_task_type
            
        except Exception as e:
            logger.error(f"Error loading data: {str(e)}")
            raise
    
    def _detect_task_type(self, y: pd.Series) -> str:
        """
        Automatically detect whether the task is classification or regression.
        
        Args:
            y: Target variable
            
        Returns:
            'classification' or 'regression'
        """
        # If target is object type, it's definitely classification
        if y.dtype == 'object' or y.dtype.name == 'category':
            return 'classification'
        
        # Check number of unique values relative to the length of the series
        unique_ratio = len(y.unique()) / len(y)
        
        # Heuristic: if less than 5% unique values or fewer than 10 distinct values, likely classification
        if unique_ratio < 0.05 or len(y.unique()) < 10:
            return 'classification'
        else:
            return 'regression'
    
    def _encode_categorical_features(
        self, 
        X: pd.DataFrame, 
        y: pd.Series, 
        target_column: str,
        encode_method: str, 
        max_onehot_cardinality: int,
        input_path: str,
        logger: logging.Logger
    ) -> pd.DataFrame:
        """
        Encode categorical features using the specified method.
        
        Args:
            X: Feature dataframe
            y: Target variable
            target_column: Name of target column (for target encoding)
            encode_method: Encoding method
            max_onehot_cardinality: Maximum cardinality for one-hot encoding
            input_path: Path to input file (for saving encoding mappings)
            logger: Logger instance
            
        Returns:
            Processed dataframe with encoded features
        """
        # Identify categorical columns
        cat_cols = X.select_dtypes(include=['object', 'category']).columns
        
        if len(cat_cols) == 0:
            logger.info("No categorical features found")
            return X
        
        logger.info(f"Found {len(cat_cols)} categorical features. Encoding method: {encode_method}")
        
        # Create a copy to work with
        X_encoded = X.copy()
        
        # Store encoding mappings for interpretability
        encoding_mappings = {}
        
        for col in cat_cols:
            unique_count = X[col].nunique()
            logger.info(f"Column '{col}' has {unique_count} unique values")
            
            # Determine encoding method if auto
            method = encode_method
            if encode_method == 'auto':
                if unique_count <= max_onehot_cardinality:
                    method = 'onehot'
                else:
                    method = 'label'
            
            # Apply the selected encoding method
            if method == 'onehot':
                logger.info(f"Using one-hot encoding for '{col}'")
                # Get dummies and drop original column
                dummies = pd.get_dummies(X[col], prefix=col, drop_first=True)
                X_encoded = pd.concat([X_encoded, dummies], axis=1)
                # Store mapping info
                encoding_mappings[col] = {'method': 'onehot', 'categories': X[col].unique().tolist()}
                
            elif method == 'label':
                logger.info(f"Using label encoding for '{col}'")
                le = LabelEncoder()
                X_encoded[f"{col}_label"] = le.fit_transform(X[col].astype(str))
                # Store mapping
                encoding_mappings[col] = {
                    'method': 'label',
                    'mapping': dict(zip(le.classes_, range(len(le.classes_))))
                }
                
            elif method == 'target':
                logger.info(f"Using target encoding for '{col}'")
                # Create temporary df with target for encoding
                temp_df = pd.concat([X[[col]], y], axis=1)
                
                # Target encoding (mean target value per category)
                means = temp_df.groupby(col)[target_column].mean()
                X_encoded[f"{col}_target"] = X[col].map(means)
                
                # Add frequency encoding as well
                freq = X[col].value_counts(normalize=True)
                X_encoded[f"{col}_freq"] = X[col].map(freq)
                
                # Store mapping
                encoding_mappings[col] = {
                    'method': 'target',
                    'target_means': means.to_dict(),
                    'frequency': freq.to_dict()
                }
        
        # Drop original categorical columns after encoding
        X_encoded = X_encoded.drop(columns=cat_cols)
        
        # Handle any NaN values from the encoding process
        if X_encoded.isna().any().any():
            missing_count = X_encoded.isna().sum().sum()
            logger.warning(f"Encoding created {missing_count} missing values. Filling with zeros.")
            X_encoded = X_encoded.fillna(0)
        
        # Save encoding mappings for interpretability
        # Use the output directory from the input path instead of a hardcoded directory
        output_dir = os.path.dirname(os.path.abspath(input_path))
        encoding_file = os.path.join(output_dir, 'categorical_encodings.json')
        
        with open(encoding_file, 'w') as f:
            json.dump(encoding_mappings, f, indent=2, default=str)
        logger.info(f"Saved categorical encoding mappings to {encoding_file}")
        
        return X_encoded
    
    def _perform_feature_selection(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        original_df: pd.DataFrame,
        X_original: pd.DataFrame,
        output_dir: str,
        target_column: str,
        task_type: str,
        method: str,
        top_features_list: List[int],
        create_plots: bool,
        top_n_plot: int,
        logger: logging.Logger
    ) -> Dict[str, str]:
        """
        Perform feature selection using the specified method.
        
        Args:
            X: Processed feature dataframe
            y: Target variable
            original_df: Original dataframe with all columns
            X_original: Original features before encoding
            output_dir: Directory to save results
            target_column: Target column name
            task_type: 'classification' or 'regression'
            method: Feature selection method
            top_features_list: List of top feature counts to select
            create_plots: Whether to create visualization plots
            top_n_plot: Number of top features to show in plots
            logger: Logger instance
            
        Returns:
            Dictionary of output file paths
        """
        result_files = {}
        
        # Define available methods with their functions
        if task_type == 'classification':
            methods = {
                'rf': self._select_features_rf_classifier,
                'f_test': self._select_features_f_test_classifier,
                'mutual_info': self._select_features_mutual_info_classifier,
                'rfe': self._select_features_rfe_classifier
            }
        else:  # regression
            methods = {
                'rf': self._select_features_rf_regressor,
                'f_test': self._select_features_f_test_regressor,
                'mutual_info': self._select_features_mutual_info_regressor,
                'rfe': self._select_features_rfe_regressor
            }
        
        # Check if method is valid
        if method not in methods:
            raise ValueError(f"Unknown feature selection method: {method}")
        
        selection_method = methods[method]
        logger.info(f"Using {method} method for feature selection")
        
        # Create feature importance file
        importance_file = os.path.join(output_dir, 'feature_importance.csv')
        result_files['importance'] = importance_file
        
        # Output files for selected features
        selected_files = {}
        
        # For RFE, we need to run the algorithm separately for each n_features
        if method == 'rfe':
            logger.info("Using Recursive Feature Elimination (RFE)")
            selected_features_all = {}
            
            for n_features in top_features_list:
                if n_features > X.shape[1]:
                    logger.warning(f"Requested {n_features} features, but only {X.shape[1]} are available")
                    n_features = X.shape[1]
                
                # Get selected features
                selected_features, scores = selection_method(X, y, n_features, logger)
                selected_features_all[n_features] = selected_features
                
                # Save selected features
                output_file = self._save_selected_features(
                    original_df, target_column, selected_features, 
                    output_dir, n_features, X_original, X, logger
                )
                selected_files[n_features] = output_file
                
                # Generate visualizations if requested
                if create_plots and n_features > 1 and n_features <= 500:
                    self._create_visualizations(X, y, selected_features, output_dir, n_features, task_type, logger)
            
            # For RFE, create an importance file using RF for visualization purposes
            if create_plots:
                all_features, scores = (self._select_features_rf_classifier(X, y, X.shape[1], logger) if task_type == 'classification' 
                                     else self._select_features_rf_regressor(X, y, X.shape[1], logger))
                
                importance_df = pd.DataFrame({
                    'feature': all_features,
                    'importance': scores
                })
                importance_df = importance_df.sort_values('importance', ascending=False)
                importance_df.to_csv(importance_file, index=False)
                
                # Create visualization plots
                self._plot_feature_importance(importance_df, output_dir, top_n_plot, logger)
                self._plot_cumulative_importance(importance_df, output_dir, logger)
                
                # Plot correlation of top features
                top_features = importance_df['feature'].iloc[:min(20, len(importance_df))].tolist()
                self._plot_feature_correlation(X, top_features, output_dir, logger)
        
        else:  # For other methods
            # Get all features with importance values
            all_features, scores = selection_method(X, y, X.shape[1], logger)
            
            # Create importance dataframe
            importance_df = pd.DataFrame({
                'feature': X.columns,
                'importance': scores
            })
            importance_df = importance_df.sort_values('importance', ascending=False)
            importance_df.to_csv(importance_file, index=False)
            logger.info(f"Saved feature importance to {importance_file}")
            
            # Generate visualization plots if requested
            if create_plots:
                logger.info("Generating visualization plots...")
                
                # Plot feature importance
                self._plot_feature_importance(importance_df, output_dir, top_n_plot, logger)
                
                # Plot cumulative importance
                thresholds = self._plot_cumulative_importance(importance_df, output_dir, logger)
                logger.info(f"Feature threshold analysis: {thresholds}")
                
                # Plot correlation of top features
                top_features = importance_df['feature'].iloc[:min(20, len(importance_df))].tolist()
                self._plot_feature_correlation(X, top_features, output_dir, logger)
            
            # For each number of features, save a CSV and create visualizations
            for n_features in top_features_list:
                if n_features > X.shape[1]:
                    logger.warning(f"Requested {n_features} features, but only {X.shape[1]} are available")
                    n_features = X.shape[1]
                
                # Get top n_features from importance dataframe
                selected_features = importance_df['feature'].iloc[:n_features].tolist()
                
                # Save selected features to CSV
                output_file = self._save_selected_features(
                    original_df, target_column, selected_features, 
                    output_dir, n_features, X_original, X, logger
                )
                selected_files[n_features] = output_file
                
                # Generate PCA and t-SNE visualizations if requested
                if create_plots and n_features > 1 and n_features <= 500:
                    self._create_visualizations(X, y, selected_features, output_dir, n_features, task_type, logger)
                
        # Add selected feature files to result
        result_files['selected_features'] = selected_files
        
        return result_files
    
    def _select_features_rf_classifier(self, X: pd.DataFrame, y: pd.Series, n_features: int, logger: logging.Logger) -> Tuple[List[str], np.ndarray]:
        """Select features using Random Forest classifier importance"""
        logger.info(f"Selecting top {n_features} features using Random Forest classifier importance")
        
        # Create and fit Random Forest
        rf = RandomForestClassifier(n_estimators=100, random_state=42)
        rf.fit(X, y)
        
        # Get feature importances
        importances = rf.feature_importances_
        
        # Create indices of top features
        indices = np.argsort(importances)[::-1][:n_features]
        
        return X.columns[indices].tolist(), importances
    
    def _select_features_rf_regressor(self, X: pd.DataFrame, y: pd.Series, n_features: int, logger: logging.Logger) -> Tuple[List[str], np.ndarray]:
        """Select features using Random Forest regressor importance"""
        logger.info(f"Selecting top {n_features} features using Random Forest regressor importance")
        
        # Create and fit Random Forest
        rf = RandomForestRegressor(n_estimators=100, random_state=42)
        rf.fit(X, y)
        
        # Get feature importances
        importances = rf.feature_importances_
        
        # Create indices of top features
        indices = np.argsort(importances)[::-1][:n_features]
        
        return X.columns[indices].tolist(), importances
    
    def _select_features_f_test_classifier(self, X: pd.DataFrame, y: pd.Series, n_features: int, logger: logging.Logger) -> Tuple[List[str], np.ndarray]:
        """Select features using ANOVA F-value for classification"""
        logger.info(f"Selecting top {n_features} features using ANOVA F-test for classification")
        
        # Apply SelectKBest with f_classif
        selector = SelectKBest(f_classif, k=min(n_features, X.shape[1]))
        selector.fit(X, y)
        
        # Get selected feature indices
        mask = selector.get_support()
        
        return X.columns[mask].tolist(), selector.scores_
    
    def _select_features_f_test_regressor(self, X: pd.DataFrame, y: pd.Series, n_features: int, logger: logging.Logger) -> Tuple[List[str], np.ndarray]:
        """Select features using F-test for regression"""
        logger.info(f"Selecting top {n_features} features using F-test for regression")
        
        # Apply SelectKBest with f_regression
        selector = SelectKBest(f_regression, k=min(n_features, X.shape[1]))
        selector.fit(X, y)
        
        # Get selected feature indices
        mask = selector.get_support()
        
        return X.columns[mask].tolist(), selector.scores_
    
    def _select_features_mutual_info_classifier(self, X: pd.DataFrame, y: pd.Series, n_features: int, logger: logging.Logger) -> Tuple[List[str], np.ndarray]:
        """Select features using mutual information for classification"""
        logger.info(f"Selecting top {n_features} features using mutual information for classification")
        
        # Apply SelectKBest with mutual_info_classif
        selector = SelectKBest(mutual_info_classif, k=min(n_features, X.shape[1]))
        selector.fit(X, y)
        
        # Get selected feature indices
        mask = selector.get_support()
        
        return X.columns[mask].tolist(), selector.scores_
    
    def _select_features_mutual_info_regressor(self, X: pd.DataFrame, y: pd.Series, n_features: int, logger: logging.Logger) -> Tuple[List[str], np.ndarray]:
        """Select features using mutual information for regression"""
        logger.info(f"Selecting top {n_features} features using mutual information for regression")
        
        # Apply SelectKBest with mutual_info_regression
        selector = SelectKBest(mutual_info_regression, k=min(n_features, X.shape[1]))
        selector.fit(X, y)
        
        # Get selected feature indices
        mask = selector.get_support()
        
        return X.columns[mask].tolist(), selector.scores_
    
    def _select_features_rfe_classifier(self, X: pd.DataFrame, y: pd.Series, n_features: int, logger: logging.Logger) -> Tuple[List[str], np.ndarray]:
        """Select features using RFE with Random Forest classifier"""
        logger.info(f"Selecting top {n_features} features using RFE with classifier")
        
        try:
            # Create RFE with RandomForestClassifier
            estimator = RandomForestClassifier(n_estimators=100, random_state=42)
            selector = RFE(estimator, n_features_to_select=min(n_features, X.shape[1]), step=0.1)
            
            # Fit RFE
            selector.fit(X, y)
            
            # Get selected feature indices
            mask = selector.get_support()
            
            return X.columns[mask].tolist(), selector.ranking_
        except Exception as e:
            logger.error(f"Error in RFE classification: {str(e)}. Falling back to Random Forest importance.")
            return self._select_features_rf_classifier(X, y, n_features, logger)
    
    def _select_features_rfe_regressor(self, X: pd.DataFrame, y: pd.Series, n_features: int, logger: logging.Logger) -> Tuple[List[str], np.ndarray]:
        """Select features using RFE with Random Forest regressor"""
        logger.info(f"Selecting top {n_features} features using RFE with regressor")
        
        try:
            # Create RFE with RandomForestRegressor
            estimator = RandomForestRegressor(n_estimators=100, random_state=42)
            selector = RFE(estimator, n_features_to_select=min(n_features, X.shape[1]), step=0.1)
            
            # Fit RFE
            selector.fit(X, y)
            
            # Get selected feature indices
            mask = selector.get_support()
            
            return X.columns[mask].tolist(), selector.ranking_
        except Exception as e:
            logger.error(f"Error in RFE regression: {str(e)}. Falling back to Random Forest importance.")
            return self._select_features_rf_regressor(X, y, n_features, logger)
    
    def _save_selected_features(
        self, 
        original_df: pd.DataFrame, 
        target_column: str, 
        selected_features: List[str],
        output_dir: str, 
        n_features: int,
        X_original: Optional[pd.DataFrame] = None,
        X_encoded: Optional[pd.DataFrame] = None,
        logger: Optional[logging.Logger] = None
    ) -> str:
        """
        Save selected features to CSV file with interpretation of encoded features.
        
        Args:
            original_df: Original dataframe with all columns
            target_column: Target column name
            selected_features: List of selected feature names
            output_dir: Directory to save output
            n_features: Number of features selected
            X_original: Original features before encoding
            X_encoded: Encoded features
            logger: Logger instance
            
        Returns:
            Path to the output CSV file
        """
        output_file = os.path.join(output_dir, f'top_{n_features}_features.csv')
        meta_file = os.path.join(output_dir, f'top_{n_features}_features_metadata.json')
        
        # Create feature metadata for interpretability
        if X_encoded is not None and X_original is not None:
            feature_metadata = {}
            
            for feature in selected_features:
                # Check if this is an encoded feature
                if '_' in feature and any(col in feature for col in X_original.columns):
                    # This is likely an encoded feature
                    # Extract original column name and encoding method
                    
                    # Try to find the original column name
                    possible_orig_cols = []
                    for col in X_original.columns:
                        if feature.startswith(col + '_'):
                            possible_orig_cols.append(col)
                    
                    if possible_orig_cols:
                        # Sort by length to get the longest match (most specific)
                        orig_col = sorted(possible_orig_cols, key=len, reverse=True)[0]
                        encoding_type = feature[len(orig_col)+1:]
                        
                        # Store metadata about the encoding
                        feature_metadata[feature] = {
                            'original_column': orig_col,
                            'encoding_type': encoding_type
                        }
            
            # Save feature metadata for interpretability
            if feature_metadata:
                with open(meta_file, 'w') as f:
                    json.dump(feature_metadata, f, indent=2)
                if logger:
                    logger.info(f"Saved feature metadata to {meta_file}")
        
        # Prepare data to save
        try:
            # Include target column in selected features
            columns_to_save = selected_features.copy()
            if target_column not in columns_to_save:
                columns_to_save.append(target_column)
            
            # Check if all columns exist in the original dataframe
            missing_cols = [col for col in columns_to_save if col not in original_df.columns]
            
            if missing_cols:
                # Some selected features are encoded and not in original_df
                if logger:
                    logger.info(f"Creating dataset with encoded features: {', '.join(missing_cols)}")
                
                # Start with features that exist in original_df
                existing_cols = [col for col in columns_to_save if col in original_df.columns]
                selected_df = original_df[existing_cols].copy()
                
                # Add encoded features from X_encoded
                for col in missing_cols:
                    if X_encoded is not None and col in X_encoded.columns:
                        selected_df[col] = X_encoded[col]
                    else:
                        if logger:
                            logger.warning(f"Feature {col} not found in encoded or original data")
            else:
                # All selected features are in the original dataframe
                selected_df = original_df[columns_to_save].copy()
        except Exception as e:
            error_msg = str(e)
            if logger:
                logger.error(f"Error creating selected features dataset: {error_msg}")
            # Fallback: just save the selected features from X_encoded with the target
            selected_df = pd.DataFrame()
            
            if X_encoded is not None:
                for col in selected_features:
                    if col in X_encoded.columns:
                        selected_df[col] = X_encoded[col]
                selected_df[target_column] = original_df[target_column].reset_index(drop=True)
            else:
                # Last resort fallback
                selected_df = pd.DataFrame({target_column: original_df[target_column]})
        
        # Save to CSV
        selected_df.to_csv(output_file, index=False)
        if logger:
            logger.info(f"Saved top {n_features} features to {output_file}")
        
        return output_file
    
    def _create_visualizations(
        self, 
        X: pd.DataFrame, 
        y: pd.Series,
        selected_features: List[str],
        output_dir: str,
        n_features: int,
        task_type: str,
        logger: Optional[logging.Logger] = None
    ):
        """
        Create visualization plots for the selected features.
        
        Args:
            X: Feature dataframe
            y: Target variable
            selected_features: List of selected feature names
            output_dir: Directory to save output
            n_features: Number of features selected
            task_type: 'classification' or 'regression'
            logger: Logger instance
        """
        # Create plots directory
        plots_dir = os.path.join(output_dir, "plots")
        os.makedirs(plots_dir, exist_ok=True)
        
        try:
            # Generate PCA visualization
            self._plot_pca_visualization(X, y, selected_features, plots_dir, n_features, task_type, logger)
            
            # Generate t-SNE visualization for smaller datasets
            if len(X) <= 5000 or n_features <= 100:
                self._plot_tsne_visualization(X, y, selected_features, plots_dir, n_features, task_type, logger)
            else:
                if logger:
                    logger.info(f"Skipping t-SNE for {n_features} features due to dataset size")
        except Exception as e:
            if logger:
                logger.error(f"Error creating visualizations: {str(e)}")
    
    def _plot_feature_importance(
        self, 
        importance_df: pd.DataFrame, 
        output_dir: str, 
        top_n: int = 30,
        logger: Optional[logging.Logger] = None
    ):
        """
        Create a bar plot of feature importance for the top N features.
        
        Args:
            importance_df: DataFrame with feature names and importance scores
            output_dir: Directory to save the plot
            top_n: Number of top features to show
            logger: Logger instance
        """
        plots_dir = os.path.join(output_dir, "plots")
        os.makedirs(plots_dir, exist_ok=True)
        
        try:
            plt.figure(figsize=(12, 8))
            
            # Use only top N features for readability
            plot_df = importance_df.head(min(top_n, len(importance_df)))
            
            # Create horizontal bar plot
            sns.barplot(x='importance', y='feature', data=plot_df)
            
            # Customize plot
            plt.title(f'Top {len(plot_df)} Feature Importance', fontsize=16)
            plt.xlabel('Importance Score', fontsize=14)
            plt.ylabel('Features', fontsize=14)
            plt.tight_layout()
            
            # Save plot
            importance_plot_file = os.path.join(plots_dir, 'feature_importance_plot.png')
            plt.savefig(importance_plot_file, dpi=300, bbox_inches='tight')
            plt.close()
            
            if logger:
                logger.info(f"Saved feature importance plot to {importance_plot_file}")
        except Exception as e:
            if logger:
                logger.error(f"Error creating feature importance plot: {str(e)}")
            plt.close()
    
    def _plot_cumulative_importance(
        self, 
        importance_df: pd.DataFrame, 
        output_dir: str,
        logger: Optional[logging.Logger] = None
    ) -> Dict[float, int]:
        """
        Create a plot of cumulative feature importance.
        
        Args:
            importance_df: DataFrame with feature names and importance scores
            output_dir: Directory to save the plot
            logger: Logger instance
            
        Returns:
            Dictionary mapping importance thresholds to number of features
        """
        plots_dir = os.path.join(output_dir, "plots")
        os.makedirs(plots_dir, exist_ok=True)
        
        try:
            plt.figure(figsize=(10, 6))
            
            # Calculate cumulative importance
            importance_df = importance_df.sort_values('importance', ascending=False).reset_index(drop=True)
            cumulative_importance = importance_df['importance'].cumsum() / importance_df['importance'].sum()
            
            # Plot
            plt.plot(range(1, len(cumulative_importance) + 1), cumulative_importance, 'b-')
            plt.xlabel('Number of Features', fontsize=14)
            plt.ylabel('Cumulative Importance', fontsize=14)
            plt.title('Cumulative Feature Importance', fontsize=16)
            
            # Add horizontal lines at common thresholds
            thresholds = [0.8, 0.9, 0.95, 0.99]
            colors = ['r', 'g', 'orange', 'purple']
            
            threshold_results = {}
            for threshold, color in zip(thresholds, colors):
                n_features = (cumulative_importance >= threshold).argmax() + 1
                threshold_results[threshold] = n_features
                
                plt.axhline(y=threshold, color=color, linestyle='--', 
                          label=f'{threshold*100:.0f}% importance: {n_features} features')
                plt.axvline(x=n_features, color=color, linestyle='--')
            
            plt.legend(loc='lower right')
            plt.grid(True, linestyle='--', alpha=0.7)
            plt.tight_layout()
            
            # Save plot
            cumulative_plot_file = os.path.join(plots_dir, 'cumulative_importance_plot.png')
            plt.savefig(cumulative_plot_file, dpi=300, bbox_inches='tight')
            plt.close()
            
            if logger:
                logger.info(f"Saved cumulative importance plot to {cumulative_plot_file}")
            
            return threshold_results
        except Exception as e:
            if logger:
                logger.error(f"Error creating cumulative importance plot: {str(e)}")
            plt.close()
            return {}
    
    def _plot_feature_correlation(
        self, 
        X: pd.DataFrame, 
        selected_features: List[str], 
        output_dir: str, 
        logger: Optional[logging.Logger] = None,
        max_features: int = 20
    ):
        """
        Create a correlation heatmap of the selected features.
        
        Args:
            X: Feature dataframe
            selected_features: List of feature names to include
            output_dir: Directory to save the plot
            logger: Logger instance
            max_features: Maximum number of features to include in the plot
        """
        plots_dir = os.path.join(output_dir, "plots")
        os.makedirs(plots_dir, exist_ok=True)
        
        try:
            # Use up to max_features to keep the plot readable
            if len(selected_features) > max_features:
                plot_features = selected_features[:max_features]
                if logger:
                    logger.info(f"Showing correlation heatmap with top {max_features} features")
            else:
                plot_features = selected_features
            
            # Ensure all selected features exist in X
            plot_features = [f for f in plot_features if f in X.columns]
            
            if not plot_features:
                if logger:
                    logger.warning("No valid features for correlation heatmap")
                return
                
            # Calculate correlation matrix
            corr = X[plot_features].corr()
            
            # Create heatmap
            plt.figure(figsize=(12, 10))
            mask = np.triu(np.ones_like(corr, dtype=bool))  # Create mask for upper triangle
            cmap = sns.diverging_palette(230, 20, as_cmap=True)
            
            sns.heatmap(corr, mask=mask, cmap=cmap, vmax=1, vmin=-1, center=0,
                       square=True, linewidths=.5, annot=True, fmt=".2f", cbar_kws={"shrink": .7})
            
            plt.title(f'Correlation Matrix of Top {len(plot_features)} Features', fontsize=16)
            plt.tight_layout()
            
            # Save plot
            corr_plot_file = os.path.join(plots_dir, 'feature_correlation_heatmap.png')
            plt.savefig(corr_plot_file, dpi=300, bbox_inches='tight')
            plt.close()
            
            if logger:
                logger.info(f"Saved correlation heatmap to {corr_plot_file}")
        except Exception as e:
            if logger:
                logger.error(f"Error creating correlation heatmap: {str(e)}")
            plt.close()
    
    def _plot_pca_visualization(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        selected_features: List[str],
        output_dir: str,
        n_features: int,
        task_type: str,
        logger: Optional[logging.Logger] = None
    ):
        """
        Create a PCA visualization of the selected features.
        
        Args:
            X: Feature dataframe
            y: Target variable
            selected_features: List of selected feature names
            output_dir: Directory to save the plot
            n_features: Number of features selected
            task_type: 'classification' or 'regression'
            logger: Logger instance
        """
        try:
            # Filter selected features that exist in X
            valid_features = [f for f in selected_features if f in X.columns]
            
            if len(valid_features) < 2:
                if logger:
                    logger.warning(f"Not enough valid features for PCA visualization (need at least 2, got {len(valid_features)})")
                return
            
            # Only use selected features
            X_selected = X[valid_features]
            
            # Apply PCA for dimensionality reduction to 2D
            pca = PCA(n_components=2, random_state=42)
            X_pca = pca.fit_transform(X_selected)
            
            # Create dataframe for plotting
            pca_df = pd.DataFrame({
                'PCA1': X_pca[:, 0],
                'PCA2': X_pca[:, 1],
                'target': y
            })
            
            # Plot
            plt.figure(figsize=(10, 8))
            
            # Handle both categorical and numerical targets
            if task_type == 'classification' or (pd.api.types.is_numeric_dtype(y) and len(y.unique()) <= 10):
                # Categorical target - use hue
                ax = sns.scatterplot(x='PCA1', y='PCA2', hue='target', 
                                 data=pca_df, palette='viridis', alpha=0.7, s=60)
                plt.legend(title='Class', bbox_to_anchor=(1.05, 1), loc='upper left')
            else:
                # Continuous target - use a scatter plot with colormap
                scatter = plt.scatter(pca_df['PCA1'], pca_df['PCA2'], c=pca_df['target'], 
                                    cmap='viridis', alpha=0.6, s=50)
                plt.colorbar(scatter, label='Target Value')
            
            # Calculate explained variance
            explained_variance = pca.explained_variance_ratio_
            
            plt.title(f'PCA of Top {n_features} Features\nExplained Variance: {sum(explained_variance):.2%}', 
                    fontsize=14)
            plt.xlabel(f'PCA1 ({explained_variance[0]:.2%})', fontsize=12)
            plt.ylabel(f'PCA2 ({explained_variance[1]:.2%})', fontsize=12)
            plt.grid(True, linestyle='--', alpha=0.7)
            plt.tight_layout()
            
            # Save plot
            pca_plot_file = os.path.join(output_dir, f'pca_visualization_top_{n_features}.png')
            plt.savefig(pca_plot_file, dpi=300, bbox_inches='tight')
            plt.close()
            
            if logger:
                logger.info(f"Saved PCA visualization to {pca_plot_file}")
        except Exception as e:
            if logger:
                logger.error(f"Error creating PCA visualization: {str(e)}")
            plt.close()
    
    def _plot_tsne_visualization(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        selected_features: List[str],
        output_dir: str,
        n_features: int,
        task_type: str,
        logger: Optional[logging.Logger] = None,
        perplexity: int = 30
    ):
        """
        Create a t-SNE visualization of the selected features.
        
        Args:
            X: Feature dataframe
            y: Target variable
            selected_features: List of selected feature names
            output_dir: Directory to save the plot
            n_features: Number of features selected
            task_type: 'classification' or 'regression'
            logger: Logger instance
            perplexity: Perplexity parameter for t-SNE
        """
        try:
            # Filter selected features that exist in X
            valid_features = [f for f in selected_features if f in X.columns]
            
            if len(valid_features) < 2:
                if logger:
                    logger.warning(f"Not enough valid features for t-SNE visualization (need at least 2, got {len(valid_features)})")
                return
            
            # Only use selected features
            X_selected = X[valid_features]
            
            # Apply t-SNE for dimensionality reduction to 2D
            perplexity = min(perplexity, len(X_selected) - 1)  # Perplexity must be less than n_samples - 1
            tsne = TSNE(n_components=2, perplexity=perplexity, random_state=42, n_jobs=-1)
            X_tsne = tsne.fit_transform(X_selected)
            
            # Create dataframe for plotting
            tsne_df = pd.DataFrame({
                'TSNE1': X_tsne[:, 0],
                'TSNE2': X_tsne[:, 1],
                'target': y
            })
            
            # Plot
            plt.figure(figsize=(10, 8))
            
            # Handle both categorical and numerical targets
            if task_type == 'classification' or (pd.api.types.is_numeric_dtype(y) and len(y.unique()) <= 10):
                # Categorical target - use hue
                ax = sns.scatterplot(x='TSNE1', y='TSNE2', hue='target', 
                                 data=tsne_df, palette='viridis', alpha=0.7, s=60)
                plt.legend(title='Class', bbox_to_anchor=(1.05, 1), loc='upper left')
            else:
                # Continuous target - use a scatter plot with colormap
                scatter = plt.scatter(tsne_df['TSNE1'], tsne_df['TSNE2'], c=tsne_df['target'], 
                                    cmap='viridis', alpha=0.6, s=50)
                plt.colorbar(scatter, label='Target Value')
            
            plt.title(f't-SNE Visualization of Top {n_features} Features', fontsize=14)
            plt.xlabel('t-SNE Dimension 1', fontsize=12)
            plt.ylabel('t-SNE Dimension 2', fontsize=12)
            plt.grid(True, linestyle='--', alpha=0.7)
            plt.tight_layout()
            
            # Save plot
            tsne_plot_file = os.path.join(output_dir, f'tsne_visualization_top_{n_features}.png')
            plt.savefig(tsne_plot_file, dpi=300, bbox_inches='tight')
            plt.close()
            
            if logger:
                logger.info(f"Saved t-SNE visualization to {tsne_plot_file}")
        except Exception as e:
            if logger:
                logger.error(f"Error creating t-SNE visualization: {str(e)}")
            plt.close()

