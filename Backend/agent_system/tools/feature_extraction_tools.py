import glob
import os
import re
import logging
import json
import yaml
import time
import shutil
import tempfile
import traceback
import ast
from typing import Optional, List, Dict, Tuple, Union, Any

import numpy as np
import pandas as pd
import seaborn as sns

from concurrent.futures import ProcessPoolExecutor, as_completed, ThreadPoolExecutor
import asyncio

import SimpleITK as sitk
from radiomics import featureextractor

import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')


from sklearn.feature_selection import SelectKBest, f_classif, f_regression, mutual_info_classif, mutual_info_regression, RFE
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.preprocessing import LabelEncoder
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE

class PyRadiomicsFeatureExtractionTool:
    name = "pyradiomics_feature_extraction"
    description = """
    This tool extracts radiomic features from medical images (CT, MRI, etc.) using PyRadiomics.
    It processes images and their corresponding segmentation masks, and can handle multiple labels within masks.
    Features are extracted for each label and saved separately to CSV files.
    """
    
    inputs = {
        "image_dir": {
            "type": "string",
            "description": "Directory containing the medical images (e.g., MRI, CT)"
        },
        "mask_dir": {
            "type": "string",
            "description": "Directory containing the segmentation masks corresponding to the images"
        },
        "output_dir": {
            "type": "string",
            "description": "Directory where extracted features will be saved"
        },
        "image_types": {
            "type": "array",
            "description": "Types of image preprocessing to apply (e.g., Original, Wavelet, LoG). Default is all available types.",
            "required": False,
            "nullable": True
        },
        "feature_classes": {
            "type": "array",
            "description": "Classes of features to extract (e.g., firstorder, shape, glcm). Default is all available classes.",
            "required": False,
            "nullable": True
        },
        "specific_features": {
            "type": "array",
            "description": "Specific features to extract. If provided, only these features will be extracted.",
            "required": False,
            "nullable": True
        },
        "mask_labels": {
            "type": "array",
            "description": "Specific label values to extract features for. Default is all non-zero labels found in masks.",
            "required": False,
            "nullable": True
        },
        "image_pattern": {
            "type": "string",
            "description": "Filename pattern to match image files. Default is '*.nii.gz'.",
            "required": False,
            "nullable": True
        },
        "mask_pattern": {
            "type": "string",
            "description": "Filename pattern to match mask files. Default is '*.nii.gz'.",
            "required": False,
            "nullable": True
        },
        "normalize": {
            "type": "boolean",
            "description": "Whether to normalize the image intensity values. Default is True.",
            "required": False,
            "nullable": True
        },
        "bin_width": {
            "type": "number",
            "description": "Bin width for discretizing image intensities. Default is 25.",
            "required": False,
            "nullable": True
        },
        "resample": {
            "type": "boolean",
            "description": "Whether to resample the image to isotropic voxels. Default is True.",
            "required": False,
            "nullable": True
        },
        "pixel_spacing": {
            "type": "array",
            "description": "Target pixel spacing for resampling [x, y, z]. Default is [1, 1, 1].",
            "required": False,
            "nullable": True
        },
        "force_2d": {
            "type": "boolean",
            "description": "Whether to extract features slice by slice (2D) instead of 3D. Default is False.",
            "required": False,
            "nullable": True
        },
        "n_workers": {
            "type": "integer",
            "description": "Number of parallel workers for processing images. Default is 1 (serial processing).",
            "required": False,
            "nullable": True
        },
        "targets_csv": {
            "type": "string",
            "description": "Path to a CSV file containing target values for each subject. Optional.",
            "required": False,
            "nullable": True
        },
        "id_column": {
            "type": "string",
            "description": "Name of the ID column in the targets CSV. Only used if targets_csv is provided.",
            "required": False,
            "nullable": True
        },
        "target_column": {
            "type": "string",
            "description": "Name of the target column in the targets CSV. Only used if targets_csv is provided.",
            "required": False,
            "nullable": True
        },
        "id_pattern": {
            "type": "string",
            "description": "Regex pattern to extract subject ID from filenames. Default is to use the filename without extension.",
            "required": False,
            "nullable": True
        }
    }
    
    output_type = "object"
    
    def forward(
        self,
        image_dir: str,
        mask_dir: str,
        output_dir: str,
        image_types: Optional[List[str]] = None,
        feature_classes: Optional[List[str]] = None,
        specific_features: Optional[List[str]] = None,
        mask_labels: Optional[List[int]] = None,
        image_pattern: Optional[str] = "*.nii.gz",
        mask_pattern: Optional[str] = "*.nii.gz",
        normalize: Optional[bool] = True,
        bin_width: Optional[float] = 25.0,
        resample: Optional[bool] = True,
        pixel_spacing: Optional[List[float]] = None,
        force_2d: Optional[bool] = False,
        n_workers: Optional[int] = 1,
        targets_csv: Optional[str] = None,
        id_column: Optional[str] = None,
        target_column: Optional[str] = None,
        id_pattern: Optional[str] = None
    ):
        """
        Extract radiomic features from medical images using PyRadiomics.
        
        Args:
            image_dir: Directory containing the medical images
            mask_dir: Directory containing the segmentation masks
            output_dir: Directory where extracted features will be saved
            image_types: Image preprocessing types (e.g., ["Original", "Wavelet", "LoG"])
            feature_classes: Feature classes to extract (e.g., ["firstorder", "shape", "glcm"])
            specific_features: Specific features to extract (e.g., ["original_firstorder_Mean"])
            mask_labels: Specific label values to extract features for
            image_pattern: Filename pattern to match image files
            mask_pattern: Filename pattern to match mask files
            normalize: Whether to normalize image intensities
            bin_width: Bin width for discretizing image intensities
            resample: Whether to resample the image
            pixel_spacing: Target pixel spacing for resampling [x, y, z]
            force_2d: Whether to extract features in 2D mode
            n_workers: Number of parallel workers
            targets_csv: Path to CSV with target values
            id_column: Name of ID column in targets CSV
            target_column: Name of target column in targets CSV
            id_pattern: Regex pattern to extract subject ID from filenames
            
        Returns:
            Dictionary with feature extraction results and file paths
        """
        # Create a temp directory variable that we can clean up in finally block
        temp_dir = None
        
        try:
            # Validate input directories exist
            self._validate_directories(image_dir, mask_dir, output_dir)
            
            # Set up logging
            os.makedirs(output_dir, exist_ok=True)
            log_file = os.path.join(output_dir, "radiomics_extraction.log")
            
            # Reset the root logger to prevent duplicate logging
            root_logger = logging.getLogger()
            if root_logger.handlers:
                for handler in root_logger.handlers[:]:
                    root_logger.removeHandler(handler)
                    
            # Configure logging with basicConfig (affects the global state)
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(levelname)s - %(message)s',
                handlers=[
                    logging.FileHandler(log_file),
                    logging.StreamHandler()
                ]
            )
            
            # Use the root logger
            logger = logging.getLogger()
            
            # Set default values
            if pixel_spacing is None:
                pixel_spacing = [1.0, 1.0, 1.0]
            
            # Validate id_pattern if provided
            if id_pattern:
                try:
                    re.compile(id_pattern)
                except re.error:
                    logger.warning(f"Invalid regex pattern provided: {id_pattern}. Defaulting to filename matching.")
                    id_pattern = None
            
            # Log configuration
            logger.info(f"Starting radiomic feature extraction with configuration:")
            logger.info(f"Image directory: {image_dir}")
            logger.info(f"Mask directory: {mask_dir}")
            logger.info(f"Output directory: {output_dir}")
            logger.info(f"Image types: {image_types if image_types else 'All available'}")
            logger.info(f"Feature classes: {feature_classes if feature_classes else 'All available'}")
            logger.info(f"Specific features: {specific_features if specific_features else 'None'}")
            logger.info(f"Mask labels: {mask_labels if mask_labels else 'All non-zero'}")
            logger.info(f"Normalize: {normalize}")
            logger.info(f"Bin width: {bin_width}")
            logger.info(f"Resample: {resample}")
            logger.info(f"Pixel spacing: {pixel_spacing}")
            logger.info(f"Force 2D: {force_2d}")
            logger.info(f"Number of workers: {n_workers}")
            
            # Prepare PyRadiomics parameters
            params = self._create_pyradiomics_params(
                image_types=image_types,
                feature_classes=feature_classes,
                specific_features=specific_features,
                normalize=normalize,
                bin_width=bin_width,
                resample=resample,
                pixel_spacing=pixel_spacing,
                force_2d=force_2d
            )
            
            # Save parameters to a YAML file
            params_file = os.path.join(output_dir, "radiomics_params.yaml")
            with open(params_file, 'w') as f:
                yaml.dump(params, f)
            logger.info(f"PyRadiomics parameters saved to {params_file}")
            
            # Initialize feature extractor
            extractor = featureextractor.RadiomicsFeatureExtractor(params_file)
            logger.info(f"Feature extractor initialized with {len(extractor.enabledFeatures)} features")
            
            # Load target values if provided
            target_dict = None
            if targets_csv:
                if not os.path.exists(targets_csv):
                    logger.warning(f"Target CSV file not found: {targets_csv}")
                else:
                    logger.info(f"Loading target values from {targets_csv}")
                    target_dict = self._load_targets(
                        targets_csv, 
                        id_column=id_column, 
                        target_column=target_column
                    )
                    logger.info(f"Loaded {len(target_dict)} target values")
            
            # Find image and mask files
            image_files = sorted(glob.glob(os.path.join(image_dir, image_pattern)))
            mask_files = sorted(glob.glob(os.path.join(mask_dir, mask_pattern)))
            
            if not image_files:
                logger.error(f"No image files found in {image_dir} with pattern {image_pattern}")
                return {
                    "status": "error",
                    "error_message": f"No image files found in {image_dir} with pattern {image_pattern}",
                    "image_dir": image_dir,
                    "mask_dir": mask_dir
                }
                
            if not mask_files:
                logger.error(f"No mask files found in {mask_dir} with pattern {mask_pattern}")
                return {
                    "status": "error",
                    "error_message": f"No mask files found in {mask_dir} with pattern {mask_pattern}",
                    "image_dir": image_dir,
                    "mask_dir": mask_dir
                }
            
            logger.info(f"Found {len(image_files)} image files and {len(mask_files)} mask files")
            
            # Match images and masks
            image_mask_pairs, unmatched = self._match_images_and_masks(
                image_files, 
                mask_files, 
                id_pattern=id_pattern,
                logger=logger
            )
            
            logger.info(f"Matched {len(image_mask_pairs)} image-mask pairs")
            if unmatched['images'] or unmatched['masks']:
                logger.warning(f"Unmatched images: {len(unmatched['images'])}, unmatched masks: {len(unmatched['masks'])}")
                if unmatched['images']:
                    logger.debug(f"Unmatched image examples: {unmatched['images'][:5]}")
                if unmatched['masks']:
                    logger.debug(f"Unmatched mask examples: {unmatched['masks'][:5]}")
            
            # Check if we have any pairs to process
            if not image_mask_pairs:
                logger.error("No matching image-mask pairs found. Check your files and patterns.")
                return {
                    "status": "error",
                    "error_message": "No matching image-mask pairs found",
                    "image_dir": image_dir,
                    "mask_dir": mask_dir
                }
            
            # Determine available labels in masks if not specified
            if mask_labels is None:
                mask_labels = self._find_available_labels(mask_files, logger=logger)
                logger.info(f"Found {len(mask_labels)} unique labels in masks: {mask_labels}")
            
            # Create a temporary directory for extracted masks by label
            temp_dir = tempfile.mkdtemp(dir=output_dir)
            logger.info(f"Created temporary directory for masks: {temp_dir}")
            
            # Process each image-mask pair
            start_time = time.time()
            
            # Initialize feature dictionaries for each label
            all_features_by_label = {label: [] for label in mask_labels}
            
            # Process serially or in parallel based on n_workers
            if n_workers <= 1:
                # Serial processing
                for i, (subject_id, files) in enumerate(image_mask_pairs.items()):
                    try:
                        self._process_subject(
                            subject_id, 
                            files, 
                            mask_labels, 
                            extractor, 
                            temp_dir, 
                            all_features_by_label, 
                            target_dict,
                            i + 1,
                            len(image_mask_pairs),
                            logger=logger
                        )
                    except Exception as e:
                        logger.error(f"Error processing subject {subject_id}: {str(e)}")
                        logger.debug(traceback.format_exc())
            else:
                # Parallel processing
                logger.info(f"Using {n_workers} workers for parallel processing")
                
                # Create arguments for parallel processing
                process_args = []
                for i, (subject_id, files) in enumerate(image_mask_pairs.items()):
                    # Create a unique temp directory for each subject to avoid conflicts
                    subject_temp_dir = os.path.join(temp_dir, f"subject_{subject_id}")
                    os.makedirs(subject_temp_dir, exist_ok=True)
                    
                    target_value = None
                    if target_dict and subject_id in target_dict:
                        target_value = target_dict[subject_id]
                    
                    process_args.append((
                        subject_id, 
                        files['image'],
                        files['mask'], 
                        mask_labels, 
                        params_file, 
                        subject_temp_dir,
                        target_value,
                        i + 1,
                        len(image_mask_pairs),
                        log_file  # Pass log file path to enable logging in subprocess
                    ))
                
                # Execute in parallel
                with ProcessPoolExecutor(max_workers=n_workers) as executor:
                    futures = [executor.submit(self._process_subject_parallel, *args) for args in process_args]
                    
                    for future in as_completed(futures):
                        try:
                            subject_id, subject_features_by_label = future.result()
                            # Merge results
                            for label, features in subject_features_by_label.items():
                                if features:  # Only add if features were extracted
                                    all_features_by_label[label].append(features)
                        except Exception as e:
                            logger.error(f"Error in parallel processing: {str(e)}")
                            logger.debug(traceback.format_exc())
            
            # Calculate processing time
            processing_time = time.time() - start_time
            logger.info(f"Feature extraction completed in {processing_time:.2f} seconds")
            
            # Save features to CSV files
            csv_paths = {}
            
            # Check if any features were extracted
            total_features_count = sum(len(features) for features in all_features_by_label.values())
            if total_features_count == 0:
                logger.warning("No features were successfully extracted for any label")
                
            for label, features in all_features_by_label.items():
                if not features:
                    logger.warning(f"No features extracted for label {label}")
                    continue
                
                # Convert to DataFrame
                df = pd.DataFrame(features)
                
                # Arrange columns (ID and target columns first, then features)
                id_cols = [col for col in df.columns if col.lower() in ['id', 'subject_id', 'patientid']]
                target_cols = [col for col in df.columns if col.lower() in ['target', 'label', 'outcome']]
                feature_cols = [col for col in df.columns if col not in id_cols + target_cols]
                
                # Sort feature columns for consistency
                feature_cols.sort()
                
                # Reorder columns - make sure all columns exist
                all_cols = [col for col in id_cols + target_cols + feature_cols if col in df.columns]
                df = df[all_cols]
                
                # Save to CSV
                csv_path = os.path.join(output_dir, f"radiomic_features_label_{label}.csv")
                df.to_csv(csv_path, index=False)
                logger.info(f"Saved {len(df)} subjects with {len(df.columns) - len(id_cols) - len(target_cols)} features for label {label} to {csv_path}")
                
                csv_paths[f"label_{label}"] = csv_path
            
            # Clean up temporary directory (this will be done in the finally block)
            
            # Calculate feature counts properly
            feature_counts = {}
            for label, features_list in all_features_by_label.items():
                if features_list:
                    df = pd.DataFrame(features_list)
                    id_cols = [col for col in df.columns if col.lower() in ['id', 'subject_id', 'patientid']]
                    target_cols = [col for col in df.columns if col.lower() in ['target', 'label', 'outcome']]
                    feature_count = len(df.columns) - len(id_cols) - len(target_cols)
                    feature_counts[label] = feature_count
            
            # Return results
            return {
                "status": "success",
                "csv_paths": csv_paths,
                "num_subjects": len(image_mask_pairs),
                "num_features": feature_counts,
                "mask_labels": mask_labels,
                "log_file": log_file,
                "params_file": params_file,
                "processing_time_seconds": processing_time
            }
            
        except Exception as e:
            # Handle any unexpected exceptions
            if 'logger' in locals():
                logger.error(f"Error during radiomic feature extraction: {str(e)}")
                logger.debug(traceback.format_exc())
            else:
                print(f"Error during radiomic feature extraction: {str(e)}")
                traceback.print_exc()
                
            return {
                "status": "error",
                "error_message": str(e),
                "image_dir": image_dir,
                "mask_dir": mask_dir,
                "output_dir": output_dir
            }
            
        finally:
            # Clean up temp directory if it was created
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                    if 'logger' in locals():
                        logger.info(f"Removed temporary directory: {temp_dir}")
                    else:
                        print(f"Removed temporary directory: {temp_dir}")
                except Exception as e:
                    if 'logger' in locals():
                        logger.warning(f"Failed to remove temporary directory {temp_dir}: {str(e)}")
                    else:
                        print(f"Failed to remove temporary directory {temp_dir}: {str(e)}")
    
    def _validate_directories(self, image_dir, mask_dir, output_dir):
        """Validate that input directories exist"""
        for directory, name in [(image_dir, "Image directory"), (mask_dir, "Mask directory")]:
            if not os.path.exists(directory):
                raise ValueError(f"{name} does not exist: {directory}")
            if not os.path.isdir(directory):
                raise ValueError(f"{name} is not a directory: {directory}")
    
    def _create_pyradiomics_params(
        self, 
        image_types=None, 
        feature_classes=None, 
        specific_features=None,
        normalize=True,
        bin_width=25.0,
        resample=True,
        pixel_spacing=None,
        force_2d=False
    ):
        """Create PyRadiomics parameters dictionary"""
        # Default image types based on modality - more conservative default
        if image_types is None:
            image_types = ["Original", "Wavelet"]
        
        # Default feature classes with all their features
        if feature_classes is None and specific_features is None:
            feature_classes = [
                "firstorder", "shape", "glcm", "glrlm", "glszm", "gldm", "ngtdm"
            ]
        
        # Initialize parameters dictionary
        params = {
            "imageType": {},
            "featureClass": {},
            "setting": {
                "force2D": force_2d,
                "normalize": normalize,
                "normalizeScale": 100,
                "binWidth": bin_width,
                "interpolator": "sitkBSpline",
                "correctMask": True,
                "padDistance": 10,
                "removeOutliers": 3
            }
        }
        
        # Add resampling if needed
        if resample:
            if pixel_spacing is None:
                pixel_spacing = [1.0, 1.0, 1.0]
            params["setting"]["resampledPixelSpacing"] = pixel_spacing
        
        # Configure image types
        for img_type in image_types:
            if img_type == "Original":
                params["imageType"]["Original"] = {}
            elif img_type == "Wavelet":
                params["imageType"]["Wavelet"] = {}
            elif img_type == "LoG":
                # Configure LoG filter with different sigma values
                params["imageType"]["LoG"] = {"sigma": [1.0, 2.0, 3.0, 4.0, 5.0]}
            elif img_type == "Exponential":
                params["imageType"]["Exponential"] = {}
            elif img_type == "Gradient":
                params["imageType"]["Gradient"] = {}
            elif img_type == "SquareRoot":
                params["imageType"]["SquareRoot"] = {}
            elif img_type == "LBP2D":
                params["imageType"]["LBP2D"] = {}
            elif img_type == "LBP3D":
                params["imageType"]["LBP3D"] = {}
        
        # Configure feature classes if using all features
        if specific_features is None:
            for feature_class in feature_classes:
                params["featureClass"][feature_class] = []
        else:
            # If specific features are provided, parse and enable them
            for feature in specific_features:
                # Parse feature string (e.g., "original_firstorder_Mean")
                parts = feature.split('_')
                if len(parts) >= 2:
                    # The last part is the feature name
                    feature_name = parts[-1]
                    # The second-to-last part is the feature class
                    feature_class = parts[-2]
                    
                    # Ensure the feature class exists in the parameters
                    if feature_class not in params["featureClass"]:
                        params["featureClass"][feature_class] = []
                    
                    # Add the feature to the list if it's not already there
                    if feature_name not in params["featureClass"][feature_class]:
                        params["featureClass"][feature_class].append(feature_name)
        
        return params
    
    def _load_targets(self, targets_csv, id_column=None, target_column=None, logger=None):
        """Load target values from CSV file"""
        try:
            # Read the CSV file
            df = pd.read_csv(targets_csv)
            
            # Determine columns to use
            if id_column is None:
                id_column = df.columns[0]  # Use first column as ID
                if logger:
                    logger.info(f"No ID column specified, using first column: {id_column}")
            
            if target_column is None:
                target_column = df.columns[1]  # Use second column as target
                if logger:
                    logger.info(f"No target column specified, using second column: {target_column}")
            
            # Check if columns exist
            if id_column not in df.columns:
                if logger:
                    logger.error(f"ID column '{id_column}' not found in {targets_csv}")
                raise ValueError(f"ID column '{id_column}' not found in {targets_csv}")
            
            if target_column not in df.columns:
                if logger:
                    logger.error(f"Target column '{target_column}' not found in {targets_csv}")
                raise ValueError(f"Target column '{target_column}' not found in {targets_csv}")
            
            # Create dictionary mapping IDs to target values
            target_dict = {}
            for _, row in df.iterrows():
                # Convert ID to string for consistent matching
                subject_id = str(row[id_column])
                target_dict[subject_id] = row[target_column]
            
            return target_dict
            
        except Exception as e:
            if logger:
                logger.error(f"Error loading targets: {str(e)}")
                logger.debug(traceback.format_exc())
            return {}
    
    def _match_images_and_masks(self, image_files, mask_files, id_pattern=None, logger=None):
        """Match image and mask files by subject ID"""
        image_mask_pairs = {}
        unmatched = {"images": [], "masks": []}
        
        # Extract subject IDs based on the pattern or use filenames
#============== modify to extract the real subj id without extension ==========================        
        def extract_id(filepath, pattern=None):
            filename = os.path.basename(filepath)

            # Try regex pattern first
            if pattern:
                try:
                    match = re.search(pattern, filename)
                    if match and match.groups():
                        return match.group(1)
                except re.error:
                    if logger:
                        logger.warning(f"Invalid regex pattern: {pattern}. Falling back to filename matching.")
                    # fall back to filename handling below
            if filename.endswith(".nii.gz"):
                return filename[:-7]  # remove full ".nii.gz"
            elif filename.endswith(".nii"):
                return filename[:-4]  # remove ".nii"
            else:
                return os.path.splitext(filename)[0]

        # Create dictionaries for images and masks with subject IDs as keys
        images_dict = {}
        for f in image_files:
            subject_id = extract_id(f, id_pattern)
            # Handle duplicate subject IDs
            if subject_id in images_dict:
                if logger:
                    logger.warning(f"Duplicate subject ID '{subject_id}' found in images: {images_dict[subject_id]} and {f}")
            images_dict[subject_id] = f
            
        masks_dict = {}
        for f in mask_files:
            subject_id = extract_id(f, id_pattern)
            # Handle duplicate subject IDs
            if subject_id in masks_dict:
                if logger:
                    logger.warning(f"Duplicate subject ID '{subject_id}' found in masks: {masks_dict[subject_id]} and {f}")
            masks_dict[subject_id] = f
        
        # Find matching pairs
        all_subjects = set(images_dict.keys()) | set(masks_dict.keys())
        for subject_id in all_subjects:
            if subject_id in images_dict and subject_id in masks_dict:
                image_mask_pairs[subject_id] = {
                    "image": images_dict[subject_id],
                    "mask": masks_dict[subject_id]
                }
            else:
                if subject_id in images_dict:
                    unmatched["images"].append(images_dict[subject_id])
                if subject_id in masks_dict:
                    unmatched["masks"].append(masks_dict[subject_id])
        
        return image_mask_pairs, unmatched
    
    def _find_available_labels(self, mask_files, max_files_to_check=10, logger=None):
        """Find unique label values in mask files"""
        unique_labels = set()
        
        # Limit the number of files to check to avoid lengthy processing
        files_to_check = mask_files[:min(max_files_to_check, len(mask_files))]
        
        for mask_file in files_to_check:
            try:
                # Read the mask file
                mask_image = sitk.ReadImage(mask_file)
                mask_array = sitk.GetArrayFromImage(mask_image)
                
                # Find unique non-zero values
                labels = np.unique(mask_array)
                labels = labels[labels > 0]  # Exclude background (0)
                
                for label in labels:
                    unique_labels.add(int(label))
                    
            except Exception as e:
                if logger:
                    logger.warning(f"Error reading mask {mask_file}: {str(e)}")
                
        # Ensure we found at least one label
        if not unique_labels and mask_files:
            if logger:
                logger.warning("No non-zero labels found in the mask files.")
            # Fallback to label 1 if no labels were found
            unique_labels.add(1)
            if logger:
                logger.info("Added fallback label 1 to ensure processing continues.")
        
        return sorted(list(unique_labels))
    
    def _process_subject(
        self, 
        subject_id, 
        files, 
        mask_labels, 
        extractor, 
        temp_dir, 
        all_features_by_label, 
        target_dict,
        subject_index,
        total_subjects,
        logger=None
    ):
        """Process a single subject and extract features for each label"""
        try:
            if logger:
                logger.info(f"Processing subject {subject_id} ({subject_index}/{total_subjects})")
            
            # Read image and mask
            image_path = files['image']
            mask_path = files['mask']
            
            # Validate files exist
            if not os.path.exists(image_path):
                if logger:
                    logger.error(f"Image file does not exist: {image_path}")
                return
                
            if not os.path.exists(mask_path):
                if logger:
                    logger.error(f"Mask file does not exist: {mask_path}")
                return
            
            # Read the image and mask
            orig_image = sitk.ReadImage(image_path)
            orig_mask = sitk.ReadImage(mask_path)
            
            # Process each label
            for label in mask_labels:
                # Create a binary mask for this label
                mask_array = sitk.GetArrayFromImage(orig_mask)
                binary_mask = (mask_array == label).astype(np.uint8)
                
                # Skip if no voxels with this label
                if np.sum(binary_mask) == 0:
                    if logger:
                        logger.info(f"Subject {subject_id}: No voxels with label {label}, skipping")
                    continue
                
                # Create a SimpleITK image from the binary mask
                binary_mask_image = sitk.GetImageFromArray(binary_mask)
                binary_mask_image.CopyInformation(orig_mask)
                
                # Save the binary mask temporarily
                label_mask_path = os.path.join(temp_dir, f"{subject_id}_label_{label}.nii.gz")
                sitk.WriteImage(binary_mask_image, label_mask_path)
                
                # Extract features
                try:
                    result = extractor.execute(image_path, label_mask_path)
                    
                    # Convert to regular dictionary and remove diagnostic keys
                    features = {str(key): value for key, value in result.items() 
                              if not key.startswith('diagnostics_')}
                    
                    # Add subject ID and target value if available
                    features['subject_id'] = subject_id
                    if target_dict and subject_id in target_dict:
                        features['target'] = target_dict[subject_id]
                    
                    # Add to the corresponding label's feature list
                    all_features_by_label[label].append(features)
                    
                    if logger:
                        logger.info(f"Subject {subject_id}: Extracted {len(features)} features for label {label}")
                    
                except Exception as e:
                    if logger:
                        logger.error(f"Error extracting features for subject {subject_id}, label {label}: {str(e)}")
                        logger.debug(traceback.format_exc())
                
                # Clean up the temporary mask file
                try:
                    if os.path.exists(label_mask_path):
                        os.remove(label_mask_path)
                except Exception as e:
                    if logger:
                        logger.warning(f"Error removing temporary mask file {label_mask_path}: {str(e)}")
            
        except Exception as e:
            if logger:
                logger.error(f"Error processing subject {subject_id}: {str(e)}")
                logger.debug(traceback.format_exc())
    
    @staticmethod
    def _process_subject_parallel(
        subject_id,
        image_path,
        mask_path, 
        mask_labels, 
        params_file,
        temp_dir,
        target_value,
        subject_index,
        total_subjects,
        log_file
    ):
        """Process a single subject in parallel mode"""
        # Set up basic logging for the subprocess
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file)
            ]
        )
        
        # Use the root logger
        logger = logging.getLogger()
        
        # Initialize variables for cleanup
        created_files = []
        
        try:
            # Create temp directory if it doesn't exist
            os.makedirs(temp_dir, exist_ok=True)
            
            logger.info(f"Parallel process: processing subject {subject_id} ({subject_index}/{total_subjects})")
            
            # Validate files exist
            if not os.path.exists(image_path):
                logger.error(f"Image file does not exist: {image_path}")
                return subject_id, {}
                
            if not os.path.exists(mask_path):
                logger.error(f"Mask file does not exist: {mask_path}")
                return subject_id, {}
            
            # Initialize local extractor
            extractor = featureextractor.RadiomicsFeatureExtractor(params_file)
            
            # Read image and mask
            orig_image = sitk.ReadImage(image_path)
            orig_mask = sitk.ReadImage(mask_path)
            
            # Initialize features by label
            features_by_label = {label: None for label in mask_labels}
            
            # Process each label
            for label in mask_labels:
                # Create a binary mask for this label
                mask_array = sitk.GetArrayFromImage(orig_mask)
                binary_mask = (mask_array == label).astype(np.uint8)
                
                # Skip if no voxels with this label
                if np.sum(binary_mask) == 0:
                    logger.info(f"Subject {subject_id}: No voxels with label {label}, skipping")
                    continue
                
                # Create a SimpleITK image from the binary mask
                binary_mask_image = sitk.GetImageFromArray(binary_mask)
                binary_mask_image.CopyInformation(orig_mask)
                
                # Save the binary mask temporarily
                label_mask_path = os.path.join(temp_dir, f"{subject_id}_label_{label}.nii.gz")
                sitk.WriteImage(binary_mask_image, label_mask_path)
                created_files.append(label_mask_path)
                
                # Extract features
                try:
                    result = extractor.execute(image_path, label_mask_path)
                    
                    # Convert to regular dictionary and remove diagnostic keys
                    features = {str(key): value for key, value in result.items() 
                              if not key.startswith('diagnostics_')}
                    
                    # Add subject ID and target value if available
                    features['subject_id'] = subject_id
                    if target_value is not None:
                        features['target'] = target_value
                    
                    # Store features for this label
                    features_by_label[label] = features
                    
                    logger.info(f"Subject {subject_id}: Extracted {len(features)} features for label {label}")
                    
                except Exception as e:
                    logger.error(f"Error extracting features for subject {subject_id}, label {label}: {str(e)}")
                    logger.debug(traceback.format_exc())
            
            return subject_id, features_by_label
            
        except Exception as e:
            logger.error(f"Error processing subject {subject_id}: {str(e)}")
            logger.debug(traceback.format_exc())
            return subject_id, {}
            
        finally:
            # Clean up temporary files
            for file_path in created_files:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception as e:
                    logger.warning(f"Failed to clean up temporary file {file_path}: {str(e)}")
            
            # Try to clean up the temp directory, but only if it's empty
            try:
                if os.path.exists(temp_dir) and not os.listdir(temp_dir):
                    os.rmdir(temp_dir)
            except Exception as e:
                logger.warning(f"Failed to remove temporary directory {temp_dir}: {str(e)}")

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
        return PyRadiomicsFeatureExtractionTool().forward(**kwargs)

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
        return FeatureImportanceAnalysisTool().forward(**kwargs)

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        result = await loop.run_in_executor(pool, run_analysis)

    return result 
