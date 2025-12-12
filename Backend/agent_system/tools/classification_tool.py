import os
import time
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, List, Tuple, Union, Any, Annotated
from pydantic import Field
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import (
    classification_report,
)
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler, OneHotEncoder, LabelEncoder
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, AdaBoostClassifier
from sklearn.svm import SVC
from sklearn.neighbors import KNeighborsClassifier
from sklearn.tree import DecisionTreeClassifier
from sklearn.naive_bayes import GaussianNB
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.metrics import (
    ConfusionMatrixDisplay, RocCurveDisplay, PrecisionRecallDisplay, 
    classification_report
)
from sklearn.model_selection import LearningCurveDisplay
from sklearn.ensemble import VotingClassifier
import joblib

class PyCaretClassificationTool:
    name = "pycaret_classification"
    description = """
    This tool uses PyCaret to train and evaluate classification models.
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
            "description": "Name of the target column for classification"
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
        "fix_imbalance": {
            "type": "boolean",
            "description": "Whether to fix class imbalance",
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
        "include_models": {
            "type": "string",
            "description": "Comma-separated list of models to include in comparison",
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
        fold: Optional[int] = 2,
        session_id: Optional[int] = None,
        fix_imbalance: Optional[bool] = True,
        data_split_stratify: Optional[bool] = True,
        data_split_shuffle: Optional[bool] = True,
        preprocess: Optional[bool] = True,
        ignore_features: Optional[str] = None,
        numeric_features: Optional[str] = None,
        categorical_features: Optional[str] = None,
        date_features: Optional[str] = None,
        n_select: Optional[int] = 3,
        normalize: Optional[bool] = False,
        include_models: Optional[str] = None,
    ):
        """
        Train and evaluate classification models using PyCaret.
        
        Args:
            input_path: Path to input CSV file with training data
            output_dir: Directory to save outputs
            target_column: Target column name for classification
            experiment_name: Name of the experiment
            fold: Number of cross-validation folds
            session_id: Random seed for reproducibility
            fix_imbalance: Whether to fix class imbalance
            data_split_stratify: Whether to use stratified sampling
            data_split_shuffle: Whether to shuffle data before splitting
            preprocess: Whether to apply preprocessing steps
            ignore_features: Comma-separated list of features to ignore
            numeric_features: Comma-separated list of numeric features
            categorical_features: Comma-separated list of categorical features
            date_features: Comma-separated list of date features
            n_select: Number of top models to select for blending
            normalize: Whether to normalize numeric features
            include_models: Comma-separated list of models to include
        Returns:
            Dictionary with model training results and file paths
        """
        try:
            # Setup logging
            os.makedirs(output_dir, exist_ok=True)
            log_file = os.path.join(output_dir, "pycaret_classification.log")
            
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
                experiment_name = f"classification_exp_{timestamp}"
            
            # Generate session_id if not provided
            if session_id is None:
                import random
                session_id = random.randint(1, 10000)
            
            logging.info(f"Starting classification experiment: {experiment_name}")
            logging.info(f"Input data: {input_path}")
            logging.info(f"Output directory: {output_dir}")
            logging.info(f"Target column: {target_column}")
            
            # Import PyCaret's classification module
            # try:
            #     from pycaret.classification import (
            #         setup, compare_models, tune_model, blend_models, 
            #         pull, predict_model, save_model, load_model,
            #         plot_model, interpret_model
            #     )
                
            #     # Check PyCaret version for compatibility adjustments
            #     import pycaret
            #     pycaret_version = getattr(pycaret, "__version__", "unknown")
            #     logging.info(f"PyCaret version: {pycaret_version}")
                
            #     logging.info("PyCaret imported successfully")
            # except ImportError as e:
            #     logging.error(f"Error importing PyCaret: {str(e)}")
            #     logging.error("Please install PyCaret: pip install pycaret")
            #     raise ImportError("PyCaret is required for this tool. Please install it with: pip install pycaret")
            
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
                'fix_imbalance': fix_imbalance,
                'preprocess': preprocess,
                'data_split_stratify': data_split_stratify,
                'data_split_shuffle': data_split_shuffle,
                'log_experiment': False  # Set to False to avoid MLflow errors
            }
            
            logging.info("Disabling experiment logging to avoid MLflow-related errors")
                
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
                
                
            
            # Check GPU availability and cuml installation if use_gpu is requested
            # if use_gpu:
            #     try:
            #         import cuml
            #         logging.info("RAPIDS cuML is available for GPU acceleration")
            #     except ImportError:
            #         logging.warning("'cuml' is not installed but use_gpu=True was specified.")
            #         if ignore_gpu_errors:
            #             logging.warning("Running on CPU instead. To use GPU, install cuml: pip install cuml")
            #             use_gpu = False
            #             setup_params['use_gpu'] = False
            #         else:
            #             raise ImportError("GPU acceleration requested but cuml is not installed. Install with: pip install cuml")

            # Configuration variables (adjust as needed based on your previous params)
            # n_select comes from your original code
            n_select = n_select if 'n_select' in locals() else 1 
            cv_folds = fold if 'fold' in locals() else 5  # Default to 5 if 'fold' variable is missing

            logging.info("Setting up Scikit-Learn experiment")

            try:
                # ---------------------------------------------------------
                # 1. PREPARE DATA (Replaces setup())
                # ---------------------------------------------------------
                
                # Separate Features and Target
                if target_column not in data.columns:
                    raise ValueError(f"Target column '{target_column}' not found in dataset.")
                    
                X = data.drop(columns=[target_column])
                y = data[target_column]
                
                # Handle Target Encoding (if target is text, e.g., "Yes"/"No")
                le = LabelEncoder()
                y = le.fit_transform(y)
                
                logging.info("Splitting data into Train and Test sets")
                X_train, X_test, y_train, y_test = train_test_split(
                    X, y, 
                    test_size=0.2,  # You can make this an input parameter if you wish
                    stratify=y if data_split_stratify else None,
                    shuffle=data_split_shuffle,
                    random_state=session_id
                )
                logging.info(f"Train shape: {X_train.shape}, Test shape: {X_test.shape}")
                # Identify Column Types automatically
                numeric_features = X.select_dtypes(include=['int64', 'float64']).columns
                categorical_features = X.select_dtypes(include=['object', 'category', 'bool']).columns
                
                logging.info(f"Features detected: {len(numeric_features)} numeric, {len(categorical_features)} categorical")

                # Define Preprocessing Pipelines
                # Numeric: Impute missing with Median -> Scale
                numeric_transformer = Pipeline(steps=[
                    ('imputer', SimpleImputer(strategy='median')),
                    ('scaler', StandardScaler())
                ])

                # Categorical: Impute missing with 'missing' -> OneHotEncode
                categorical_transformer = Pipeline(steps=[
                    ('imputer', SimpleImputer(strategy='constant', fill_value='missing')),
                    ('onehot', OneHotEncoder(handle_unknown='ignore'))
                ])

                # Combine into a single preprocessor
                preprocessor = ColumnTransformer(
                    transformers=[
                        ('num', numeric_transformer, numeric_features),
                        ('cat', categorical_transformer, categorical_features)
                    ])

                logging.info("Scikit-Learn data preprocessing pipeline established")

                # ---------------------------------------------------------
                # 2. DEFINE MODELS (Replaces compare_models internal logic)
                # ---------------------------------------------------------
                
                # Dictionary of classifiers to test
                models_to_test = [
                    ('Logistic Regression', LogisticRegression(max_iter=1000, random_state=42)),
                    ('Random Forest', RandomForestClassifier(random_state=42)),
                    ('Gradient Boosting', GradientBoostingClassifier(random_state=42)),
                    ('SVM', SVC(probability=True, random_state=42)),
                    ('K-Neighbors', KNeighborsClassifier()),
                    ('Decision Tree', DecisionTreeClassifier(random_state=42)),
                    ('Naive Bayes', GaussianNB()),
                    ('AdaBoost', AdaBoostClassifier(random_state=42))
                ]
                
                # Filter based on include/exclude strings if they exist
                if 'include_models' in locals() and include_models:
                    include_list = [m.strip().lower() for m in include_models.split(',')]
                    # This is a basic filter, might need adjustment based on how you name keys
                    # models_to_test = [m for m in models_to_test if m[0].lower() in include_list]

                logging.info(f"Starting comparison of {len(models_to_test)} models...")

                # ---------------------------------------------------------
                # 3. COMPARE MODELS (Replaces compare_models())
                # ---------------------------------------------------------
                
                model_results = []
                trained_pipelines = {}

                for name, model in models_to_test:
                    try:
                        # Create a full pipeline: Preprocessing -> Model
                        clf = Pipeline(steps=[('preprocessor', preprocessor),
                                            ('classifier', model)])
                        
                        # Run Cross-Validation
                        # scoring='accuracy' is default, can change to 'f1' or 'roc_auc'
                        # Run Cross-Validation on TRAIN set only
                        scores = cross_val_score(clf, X_train, y_train, cv=cv_folds, scoring='accuracy', n_jobs=-1)
                        
                        mean_score = scores.mean()
                        std_score = scores.std()
                        
                        logging.info(f"Model: {name} | Accuracy: {mean_score:.4f} (+/- {std_score:.4f})")
                        
                        # Train the model on TRAIN data to return it later
                        clf.fit(X_train, y_train)
                        
                        model_results.append({
                            'Model': name,
                            'Accuracy': mean_score,
                            'Std Dev': std_score,
                            'Object': clf # We store the actual trained pipeline object
                        })
                        
                    except Exception as e:
                        logging.warning(f"Failed to evaluate {name}: {str(e)}")

                # ---------------------------------------------------------
                # 4. PROCESS RESULTS (Replaces pull() and selection)
                # ---------------------------------------------------------

                if not model_results:
                    logging.error("No models were successfully trained.")
                    # Fallback to Random Forest if everything failed
                    logging.warning("Creating fallback Random Forest model")
                    logging.warning("Creating fallback Random Forest model")
                    fallback_clf = Pipeline(steps=[('preprocessor', preprocessor),
                                                ('classifier', RandomForestClassifier(random_state=42))])
                    fallback_clf.fit(X_train, y_train)
                    best = [fallback_clf]
                    
                    # Create a dummy result entry for the CSV
                    model_results.append({
                        'Model': 'Random Forest (Fallback)', 
                        'Accuracy': 0.0, 
                        'Std Dev': 0.0, 
                        'Object': fallback_clf
                    })
                else:
                    # Create DataFrame (Replaces pull())
                    results_df = pd.DataFrame(model_results).sort_values(by='Accuracy', ascending=False)
                    
                    # Select Top N
                    top_n = results_df.head(n_select)
                    best = top_n['Object'].tolist() # This is the list of best model objects
                    
                    logging.info(f"Top {len(best)} models selected: {top_n['Model'].tolist()}")

                    # Save Results to CSV
                    compare_results_path = os.path.join(output_dir, 'model_comparison_results.csv')
                    # Drop the 'Object' column before saving to CSV as it's not text
                    results_df.drop(columns=['Object']).to_csv(compare_results_path, index=False)
                    
                    created_files = [] # Initialize if not defined outside
                    created_files.append(('Model Comparison Results', compare_results_path))
                    logging.info(f"Saved model comparison results to {compare_results_path}")

            except Exception as e:
                logging.error(f"Critical error in Scikit-Learn workflow: {str(e)}")
                raise e
            # create_plots_for_model function 
            def create_plots_for_model(model, X_test, y_test, plots_dir):
                """
                Generates and saves standard classification plots using Scikit-Learn & Matplotlib.
                
                Args:
                    model: Trained Scikit-Learn pipeline/model
                    X_test: Test features (DataFrame)
                    y_test: Test labels (Series/Array)
                    plots_dir: Directory to save plots
                    
                Returns:
                    List of tuples: [('Plot Name', 'Path/to/file.png'), ...]
                """
                model_plots = []
                
                # ensure directory exists
                os.makedirs(plots_dir, exist_ok=True)
                
                # Store original dir to be safe (though we use absolute paths below)
                original_dir = os.getcwd()
                
                # Determine if Binary or Multiclass
                n_classes = len(np.unique(y_test))
                is_binary = n_classes == 2
                
                logging.info(f"Generating plots in {plots_dir} (Binary: {is_binary})")

                try:
                    # ---------------------------------------------------------
                    # 1. Confusion Matrix
                    # ---------------------------------------------------------
                    try:
                        plt.figure(figsize=(8, 6))
                        # from_estimator automatically uses the model to predict X_test
                        ConfusionMatrixDisplay.from_estimator(model, X_test, y_test, cmap='Blues')
                        plt.title('Confusion Matrix')
                        
                        save_path = os.path.join(plots_dir, 'confusion_matrix.png')
                        plt.savefig(save_path, bbox_inches='tight')
                        plt.close() # Close to free memory
                        
                        model_plots.append(('Confusion Matrix', save_path))
                        logging.info("Created Confusion Matrix")
                    except Exception as e:
                        logging.warning(f"Failed to create Confusion Matrix: {e}")

                    # ---------------------------------------------------------
                    # 2. ROC Curve & AUC (Binary Only)
                    # ---------------------------------------------------------
                    if is_binary:
                        try:
                            plt.figure(figsize=(8, 6))
                            RocCurveDisplay.from_estimator(model, X_test, y_test)
                            plt.title('ROC Curve')
                            plt.plot([0, 1], [0, 1], 'r--') # Random guess line
                            
                            save_path = os.path.join(plots_dir, 'auc.png')
                            plt.savefig(save_path, bbox_inches='tight')
                            plt.close()
                            
                            model_plots.append(('ROC AUC', save_path))
                            logging.info("Created ROC Curve")
                        except Exception as e:
                            logging.warning(f"Failed to create ROC Curve: {e}")

                    # ---------------------------------------------------------
                    # 3. Precision-Recall Curve (Binary Only)
                    # ---------------------------------------------------------
                    if is_binary:
                        try:
                            plt.figure(figsize=(8, 6))
                            PrecisionRecallDisplay.from_estimator(model, X_test, y_test)
                            plt.title('Precision-Recall Curve')
                            
                            save_path = os.path.join(plots_dir, 'pr.png')
                            plt.savefig(save_path, bbox_inches='tight')
                            plt.close()
                            
                            model_plots.append(('Precision Recall', save_path))
                            logging.info("Created PR Curve")
                        except Exception as e:
                            logging.warning(f"Failed to create PR Curve: {e}")

                    # ---------------------------------------------------------
                    # 4. Classification Report (Heatmap)
                    # ---------------------------------------------------------
                    try:
                        plt.figure(figsize=(8, 6))
                        y_pred = model.predict(X_test)
                        report_dict = classification_report(y_test, y_pred, output_dict=True)
                        sns.heatmap(pd.DataFrame(report_dict).iloc[:-1, :].T, annot=True, cmap='viridis')
                        plt.title('Classification Report')
                        
                        save_path = os.path.join(plots_dir, 'class_report.png')
                        plt.savefig(save_path, bbox_inches='tight')
                        plt.close()
                        
                        model_plots.append(('Class Report', save_path))
                        logging.info("Created Classification Report Heatmap")
                    except Exception as e:
                        logging.warning(f"Failed to create Class Report: {e}")

                    # ---------------------------------------------------------
                    # 5. Feature Importance
                    # ---------------------------------------------------------
                    try:
                        # We need to extract the actual classifier from the pipeline
                        # Assuming structure is Pipeline(steps=[('preprocessor', ...), ('classifier', ...)])
                        classifier = model.named_steps['classifier']
                        
                        # Check if model has built-in importance
                        importances = None
                        if hasattr(classifier, 'feature_importances_'):
                            importances = classifier.feature_importances_
                        elif hasattr(classifier, 'coef_'):
                            importances = np.abs(classifier.coef_[0])
                        
                        if importances is not None:
                            # Attempt to get feature names (Best effort)
                            try:
                                # Try to get names from preprocessor
                                feature_names = model.named_steps['preprocessor'].get_feature_names_out()
                            except:
                                # Fallback to numeric indices
                                feature_names = [f'Feature {i}' for i in range(len(importances))]

                            # Create DataFrame for plotting
                            feat_df = pd.DataFrame({'Feature': feature_names, 'Importance': importances})
                            feat_df = feat_df.sort_values(by='Importance', ascending=False).head(20) # Top 20
                            
                            plt.figure(figsize=(10, 8))
                            sns.barplot(x='Importance', y='Feature', hue='Feature', data=feat_df, palette='viridis', legend=False)
                            plt.title('Feature Importance (Top 20)')
                            
                            save_path = os.path.join(plots_dir, 'feature.png')
                            plt.savefig(save_path, bbox_inches='tight')
                            plt.close()
                            
                            model_plots.append(('Feature Importance', save_path))
                            logging.info("Created Feature Importance Plot")
                    except Exception as e:
                        logging.warning(f"Failed to create Feature Importance: {e}")

                    # ---------------------------------------------------------
                    # 6. Learning Curve (Time Consuming - careful on large data)
                    # ---------------------------------------------------------
                    try:
                        # Only run learning curve if we have enough data (e.g., > 20 samples)
                        if len(X_test) > 20:
                            logging.info("Generating Learning Curve (this may take a moment)...")
                            plt.figure(figsize=(8, 6))
                            LearningCurveDisplay.from_estimator(
                                model, X_test, y_test, score_type="both", scoring='accuracy'
                            )
                            plt.title("Learning Curve")
                            save_path = os.path.join(plots_dir, 'learning.png')
                            plt.savefig(save_path, bbox_inches='tight')
                            plt.close()
                            model_plots.append(('Learning Curve', save_path))
                        else:
                            logging.info("Skipping Learning Curve: Data too small for Cross-Validation")
                    except Exception as e:
                        logging.warning(f"Failed to create Learning Curve: {e}")

                except Exception as e:
                    logging.error(f"Global error in create_plots_for_model: {e}")
                
                return model_plots

            # #create_interpretations_for_model function
            # def create_interpretations_for_model(model, plots_dir, created_files):
            #     model_interpretations = []
                
            #     # Store the original working directory
            #     original_dir = os.getcwd()
                
            #     try:
            #         # Change to the plots directory before creating interpretations
            #         os.chdir(plots_dir)
            #         logging.info(f"Changed working directory to {plots_dir}")
                    
            #         # Try to create summary interpretation
            #         try:
            #             logging.info("Attempting to create model interpretation summary")
            #             interpret_model(model, plot='summary', save=True)
                        
            #             # Check if file was created
            #             expected_filename = "SHAP Summary.png"
            #             if os.path.exists(expected_filename):
            #                 full_path = os.path.join(plots_dir, expected_filename)
            #                 model_interpretations.append(('Interpretation: summary', full_path))
            #                 logging.info(f"Successfully created interpretation summary at {full_path}")
            #             else:
            #                 logging.warning("Interpretation summary file was not found after creation")
                            
            #         except Exception as e:
            #             logging.warning(f"Failed to create summary interpretation: {str(e)}")
                    
            #         # Try to create reason plot
            #         try:
            #             logging.info("Attempting to create model interpretation reason")
            #             interpret_model(model, plot='reason', observation=1, save=True)
                        
            #             # Check if file was created
            #             expected_filename = "SHAP Reason Code.png"
            #             if os.path.exists(expected_filename):
            #                 full_path = os.path.join(plots_dir, expected_filename)
            #                 model_interpretations.append(('Interpretation: reason', full_path))
            #                 logging.info(f"Successfully created interpretation reason at {full_path}")
            #             else:
            #                 logging.warning("Interpretation reason file was not found after creation")
                            
            #         except Exception as e:
            #             logging.warning(f"Failed to create reason interpretation: {str(e)}")
                
            #     finally:
            #         # Always restore the original working directory
            #         os.chdir(original_dir)
            #         logging.info(f"Restored working directory to {original_dir}")
                
            #     return model_interpretations
            
            # Function to evaluate model on test data 
            def evaluate_model_on_test_data(model, X_test, output_path, model_name, y_test=None):
                """
                Evaluates the model on test data and saves predictions to CSV.
                Replicates PyCaret's 'predict_model' output format.
                
                Args:
                    model: Trained Scikit-Learn pipeline
                    X_test: Test features (DataFrame)
                    output_path: Path to save the CSV
                    model_name: Name of the model for logging
                    y_test: Optional Test labels (Series) to include actual values in output
                """
                try:
                    logging.info(f"Evaluating {model_name} on test data using Scikit-Learn")
                    
                    # 1. Create a copy of the test data so we don't modify the original
                    results_df = X_test.copy()
                    
                    # 2. Get Predictions (Labels)
                    y_pred = model.predict(X_test)
                    results_df['prediction_label'] = y_pred
                    
                    # 3. Get Probabilities (Scores) - Replicates 'prediction_score'
                    # Check if the model supports probability (e.g., SVM needs probability=True)
                    if hasattr(model, "predict_proba"):
                        try:
                            # Get probabilities for all classes
                            probas = model.predict_proba(X_test)
                            # Take the maximum probability as the 'score' (confidence)
                            results_df['prediction_score'] = np.max(probas, axis=1)
                        except Exception as prob_e:
                            logging.warning(f"Could not calculate prediction scores: {prob_e}")
                            results_df['prediction_score'] = 0.0
                    else:
                        # For models like RidgeClassifier or LinearSVM without probability=True
                        results_df['prediction_score'] = 1.0 # Default confidence

                    # 4. Add Actual Labels (Ground Truth) if provided
                    if y_test is not None:
                        # Check if y_test is a Pandas Series (has .values) or a Numpy Array
                        if hasattr(y_test, 'values'):
                             results_df['actual_label'] = y_test.values
                        else:
                             results_df['actual_label'] = y_test

                    # 5. Save to CSV
                    results_df.to_csv(output_path, index=False)
                    logging.info(f"Saved {model_name} test predictions to {output_path}")
                    
                    return output_path

                except Exception as e:
                    logging.error(f"Error evaluating {model_name} on test data: {str(e)}")
                    return None

            
            try:
                # Create a top-level models directory
                models_dir = os.path.join(output_dir, "models")
                os.makedirs(models_dir, exist_ok=True)
                
                # Dictionary to track all individual model paths
                individual_model_paths = {}
                model_test_predictions = {}
                
                # We use the models found in the previous 'comparison' step
                # (Skipping explicit GridSearchCV here to avoid complex param grid definitions)
                tuned_models = []
                
                for i, model in enumerate(best):
                    model_index = i + 1
                    model_name = f"model_{model_index}" # Renamed from 'tuned' to generic
                    logging.info(f"Processing model {model_index}/{len(best)}")
                    
                    try:
                        # Create a dedicated directory for this model
                        model_dir = os.path.join(models_dir, model_name)
                        os.makedirs(model_dir, exist_ok=True)
                        
                        # In Sklearn, we use the model as-is from the comparison step
                        current_model = model
                        tuned_models.append(current_model)
                        
                        # Save the model itself using Joblib
                        model_path = os.path.join(model_dir, f'{model_name}.pkl')
                        try:
                            joblib.dump(current_model, model_path)
                            created_files.append((f'{model_name} Saved Model', model_path))
                            individual_model_paths[model_name] = model_path
                            logging.info(f"Saved {model_name} to {model_path}")
                        except Exception as e:
                            logging.error(f"Error saving {model_name}: {str(e)}")
                        
                        # Create plots directory for this model
                        plots_dir = os.path.join(model_dir, "plots")
                        os.makedirs(plots_dir, exist_ok=True)
                        
                        # Generate plots for this model (Using the sklearn function we created earlier)
                        logging.info(f"Generating plots for {model_name}")
                        # NOTE: We pass X_test and y_test explicitly now
                        model_plots = create_plots_for_model(current_model, X_test, y_test, plots_dir)
                        created_files.extend(model_plots)
                        
                        # --- INTERPRETATIONS REMOVED AS REQUESTED ---
                        
                        # Evaluate on test set using the helper function we created
                        test_pred_path = os.path.join(model_dir, f'independent_eval_results_{model_name}.csv')
                        test_result = evaluate_model_on_test_data(current_model, X_test, test_pred_path, model_name, y_test=y_test)
                        
                        if test_result:
                            created_files.append((f'{model_name} Test Predictions', test_result))
                            model_test_predictions[model_name] = test_result
                        
                    except Exception as e:
                        logging.error(f"Error processing {model_name}: {str(e)}")
                
                # ---------------------------------------------------------
                # BLENDING MODELS (VotingClassifier)
                # ---------------------------------------------------------
                blended_model = None
                blended_model_path = None
                
                if len(tuned_models) > 1:
                    logging.info("Creating blended model (VotingClassifier) from top models")
                    try:
                        # Create dedicated directory
                        blended_dir = os.path.join(models_dir, "blended_model")
                        os.makedirs(blended_dir, exist_ok=True)
                        
                        # Prepare estimators list [(name, model), ...]
                        estimators_list = []
                        for idx, m in enumerate(tuned_models):
                            # We must use unique names for estimators
                            estimators_list.append((f"model_{idx}", m))

                        # Create VotingClassifier (Soft vote averages probabilities)
                        blended_model = VotingClassifier(estimators=estimators_list, voting='soft')
                        
                        # We must fit the blender on the training data
                        logging.info("Fitting blended model on training data...")
                        blended_model.fit(X_train, y_train)
                        
                        # Save the blended model
                        blended_model_path = os.path.join(blended_dir, 'blended_model.pkl')
                        try:
                            joblib.dump(blended_model, blended_model_path)
                            created_files.append(('Blended Model Saved Model', blended_model_path))
                            individual_model_paths["blended_model"] = blended_model_path
                            logging.info(f"Saved blended model to {blended_model_path}")
                        except Exception as e:
                            logging.error(f"Error saving blended model: {str(e)}")
                        
                        # Create plots directory
                        plots_dir = os.path.join(blended_dir, "plots")
                        os.makedirs(plots_dir, exist_ok=True)
                        
                        # Generate plots for blended model
                        logging.info("Generating plots for blended model")
                        blended_plots = create_plots_for_model(blended_model, X_test, y_test, plots_dir)
                        created_files.extend(blended_plots)
                        
                        # --- INTERPRETATIONS REMOVED AS REQUESTED ---
                        
                        # Evaluate blended model
                        blended_test_pred_path = os.path.join(blended_dir, 'independent_eval_results_blended_model.csv')
                        test_result = evaluate_model_on_test_data(blended_model, X_test, blended_test_pred_path, "blended_model", y_test=y_test)
                        
                        if test_result:
                            created_files.append(('Blended Model Test Predictions', test_result))
                            model_test_predictions["blended_model"] = test_result
                        
                        final_model = blended_model
                        
                    except Exception as e:
                        logging.error(f"Error creating blended model: {str(e)}")
                        logging.info("Using the best individual model as the final model")
                        final_model = tuned_models[0]
                else:
                    # If only one model exists, that is the final model
                    final_model = tuned_models[0] if tuned_models else None
                
                # ---------------------------------------------------------
                # FINAL SUMMARY & CLEANUP
                # ---------------------------------------------------------
                
                # Save the final model to the main output directory
                if final_model:
                    final_model_path = os.path.join(output_dir, f'{experiment_name}_final_model.pkl')
                    try:
                        joblib.dump(final_model, final_model_path)
                        created_files.append(('Final Model', final_model_path))
                        logging.info(f"Saved final model to {final_model_path}")
                    except Exception as e:
                        logging.error(f"Error saving final model: {str(e)}")
                        final_model_path = None
                
                # Create summary report CSV
                try:
                    summary_path = os.path.join(output_dir, "model_summary.csv")
                    summary_data = []
                    
                    # Add individual models
                    for i, model in enumerate(tuned_models):
                        m_name = f"model_{i+1}"
                        summary_data.append({
                            "Model": m_name,
                            "Type": "Individual Model",
                            "Model Path": individual_model_paths.get(m_name, "Not saved"),
                            "Test Predictions": model_test_predictions.get(m_name, "Not available")
                        })
                    
                    # Add blended model
                    if blended_model is not None:
                        summary_data.append({
                            "Model": "blended_model",
                            "Type": "Blended Ensemble",
                            "Model Path": individual_model_paths.get("blended_model", "Not saved"),
                            "Test Predictions": model_test_predictions.get("blended_model", "Not available")
                        })
                    
                    pd.DataFrame(summary_data).to_csv(summary_path, index=False)
                    created_files.append(('Model Summary', summary_path))
                    logging.info(f"Created model summary at {summary_path}")
                    
                except Exception as e:
                    logging.error(f"Error creating model summary: {str(e)}")
                    summary_path = None

                logging.info("Classification modeling completed successfully")
                
                # Return Dictionary (Matching your original return structure)
                return {
                    "status": "success",
                    "experiment_name": experiment_name,
                    "input_path": input_path,
                    "output_dir": output_dir,
                    "final_model_path": final_model_path,
                    "individual_model_paths": individual_model_paths,
                    "model_test_predictions": model_test_predictions,
                    "model_summary": summary_path,
                    "created_files": created_files
                }

            except Exception as e:
                logging.error(f"Error in Scikit-Learn classification: {str(e)}", exc_info=True)
                return {
                    "status": "error",
                    "error_message": str(e),
                    "input_path": input_path,
                    "output_dir": output_dir
                }
        except Exception as e:
            logger.error(e)
            return {
                    "status": "error",
                    "error_message": str(e),
                    "input_path": input_path,
                    "output_dir": output_dir
                }
            
# async def classification_modelling(
#     # input_path: Annotated[str, Field(description="The absolute path to the input CSV dataset containing the training data.")],
#     # output_dir: Annotated[str, Field(description="The directory path where all artifacts (trained models, plots, logs, and CSV results) will be saved.")],
#     # target_column: Annotated[str, Field(description="The name of the column in the dataset that acts as the prediction target (label).")],
#     experiment_name: Annotated[Optional[str], Field(description="A unique identifier for the experiment. If not provided, a timestamped name will be generated.")] = None,
#     fold: Annotated[Optional[int], Field(description="The number of cross-validation folds to use during model evaluation. Defaults to 2.")] = 2,
#     session_id: Annotated[Optional[int], Field(description="A random seed for reproducibility. If None, a random seed is generated.")] = None,
#     fix_imbalance: Annotated[Optional[bool], Field(description="If True, applies class balancing techniques (like SMOTE) to handle uneven target distribution.")] = True,
#     data_split_stratify: Annotated[Optional[bool], Field(description="If True, stratifies the train/test split to maintain class proportions.")] = True,
#     data_split_shuffle: Annotated[Optional[bool], Field(description="If True, shuffles the data before splitting.")] = True,
#     preprocess: Annotated[Optional[bool], Field(description="If True, applies automated preprocessing (imputation, encoding) to the data.")] = True,
#     ignore_features: Annotated[Optional[str], Field(description="A comma-separated list of feature names to exclude from training.")] = None,
#     numeric_features: Annotated[Optional[str], Field(description="A comma-separated list of column names to be treated explicitly as numeric.")] = None,
#     categorical_features: Annotated[Optional[str], Field(description="A comma-separated list of column names to be treated explicitly as categorical.")] = None,
#     date_features: Annotated[Optional[str], Field(description="A comma-separated list of column names to be treated explicitly as dates.")] = None,
#     n_select: Annotated[Optional[int], Field(description="The number of top-performing models to select for the final blended/voting ensemble.")] = 1,
#     normalize: Annotated[Optional[bool], Field(description="If True, scales numeric features to a standard range (e.g., Z-score normalization).")] = False,
#     include_models: Annotated[Optional[str], Field(description="A comma-separated list of specific model IDs to include in the comparison.")] = None,
# ) -> Dict[str, Any]:
#     """
#     Asynchronously executes the full Classification Modelling pipeline.

#     Use this tool in scenarios such as:
#     1. When you need to train multiple machine learning algorithms on a dataset and automatically select the best one.
#     2. To generate a complete scientific report containing model metrics, confusion matrices, and ROC curves.
#     3. When you need to perform advanced data preprocessing (normalization, balancing) as part of the training workflow.
#     """
    
#     input_path = r'testing_folders\radiomic_features_label_1.csv'
#     output_dir = r"Backend\agent_system\memory_output_logs"
#     target_column = "target"
#     def run_classification_modeling():
#         kwargs = dict(
#             input_path=input_path,
#             output_dir=output_dir,
#             target_column=target_column,
#             experiment_name=experiment_name,
#             fold=fold,
#             session_id=session_id,
#             fix_imbalance=fix_imbalance,
#             data_split_stratify=data_split_stratify,
#             data_split_shuffle=data_split_shuffle,
#             preprocess=preprocess,
#             ignore_features=ignore_features,
#             numeric_features=numeric_features,
#             categorical_features=categorical_features,
#             date_features=date_features,
#             n_select=n_select,
#             normalize=normalize,
#             include_models=include_models,
#         )
#         modelling_classification_tool = PyCaretClassificationTool()
#         return modelling_classification_tool.forward(**kwargs)

#     loop = asyncio.get_event_loop()
#     with ThreadPoolExecutor() as pool:
#         result = await loop.run_in_executor(pool, run_classification_modeling)
    
#     return result
            
# asyncio.run(classification_modelling(experiment_name="testing"))
# tool = PyCaretClassificationTool()

# tool.forward(r'testing_folders\radiomic_features_label_1.csv', "my_models", "target", "testing1")



from typing import Optional, Dict, Any
import asyncio
from concurrent.futures import ThreadPoolExecutor

# Make sure to import your PyCaretClassificationTool here
# from your_module import PyCaretClassificationTool 

async def classification_modelling(
    experiment_name: Optional[str] = None,
    fold: Optional[int] = 2,
    session_id: Optional[int] = None,
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
    include_models: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Asynchronously executes the full Classification Modelling pipeline.

    Args:
        experiment_name: A unique identifier for the experiment. If not provided, a timestamped name will be generated.
        fold: The number of cross-validation folds to use during model evaluation. Defaults to 2.
        session_id: A random seed for reproducibility. If None, a random seed is generated.
        fix_imbalance: If True, applies class balancing techniques (like SMOTE) to handle uneven target distribution.
        data_split_stratify: If True, stratifies the train/test split to maintain class proportions.
        data_split_shuffle: If True, shuffles the data before splitting.
        preprocess: If True, applies automated preprocessing (imputation, encoding) to the data.
        ignore_features: A comma-separated list of feature names to exclude from training.
        numeric_features: A comma-separated list of column names to be treated explicitly as numeric.
        categorical_features: A comma-separated list of column names to be treated explicitly as categorical.
        date_features: A comma-separated list of column names to be treated explicitly as dates.
        n_select: The number of top-performing models to select for the final blended/voting ensemble.
        normalize: If True, scales numeric features to a standard range (e.g., Z-score normalization).
        include_models: A comma-separated list of specific model IDs to include in the comparison.
    """
    
    # Hardcoded paths as per your original snippet
    input_path = r'testing_folders\radiomic_features_label_1.csv'
    output_dir = r"Backend\agent_system\memory_output_logs"
    target_column = "target"

    def run_classification_modeling():
        kwargs = dict(
            input_path=input_path,
            output_dir=output_dir,
            target_column=target_column,
            experiment_name=experiment_name,
            fold=fold,
            session_id=session_id,
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
            include_models=include_models,
        )
        # Ensure this class is imported or defined in scope
        modelling_classification_tool = PyCaretClassificationTool()
        return modelling_classification_tool.forward(**kwargs)

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        result = await loop.run_in_executor(pool, run_classification_modeling)
    
    return result