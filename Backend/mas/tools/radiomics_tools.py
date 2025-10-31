import glob
import os
import re
import logging
import yaml
import time
import shutil
import tempfile
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Optional, List
import numpy as np
import pandas as pd
import SimpleITK as sitk
from radiomics import featureextractor

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
