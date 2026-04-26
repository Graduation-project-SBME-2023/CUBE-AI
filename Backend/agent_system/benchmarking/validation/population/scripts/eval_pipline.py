import os
import sys
import json
import time
import asyncio
import logging
import pandas as pd
from pathlib import Path
from sklearn.metrics import precision_score, recall_score, f1_score, confusion_matrix, jaccard_score

from autogen_ext.models.openai import OpenAIChatCompletionClient

# ==========================================
# 1. PATH CONFIGURATIONS
# ==========================================
BASE_DIR = Path(r"C:\sbme 26\GP\refactored project\OHIF-Standalone\Backend\agent_system")
WORKSPACE_DIR = BASE_DIR / "workspace_test"
CONFIG_PATH = WORKSPACE_DIR / "config.json"
BENCHMARK_JSON_PATH = BASE_DIR / r"benchmarking\validation\population\inclusion_bench\benchmark.json"
DATA_CSV_DIR = BASE_DIR / r"benchmarking\validation\population\data_generation\data\separated"
OUTPUT_REPORT_PATH = BASE_DIR / r"benchmarking\validation\population\scripts\deep_evaluation_report.json"

# Inject BASE_DIR into sys.path to allow importing from the parent/root directories
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

# Import the PopulationPlannerAgent using the absolute path relative to BASE_DIR
from agents.execution_agents.population_agent import PopulationPlannerAgent

# ==========================================
# 2. DATA MAPPINGS & SETUP
# ==========================================
# Maps the benchmark paper IDs to their respective CSV files
PAPER_TO_CSV_MAP = {
    "paper_1_cervical_lymph": "lymph_nodes_data.csv",
    "paper_2_rectal_cancer": "rectal_Cancer_trus_data.csv",
    "paper_3_lung_adenocarcinoma": "lung_cancer_data.csv",
    "paper_4_breast_nml": "Breast_non_mass_lesions_data.csv",
    "paper_5_breast_aln": "Breast_lesion_data.csv",
    "negative_control": "Negative_control.csv"
}

# Add any Ollama models you want to benchmark against each other here
MODELS_TO_TEST = [
    "deepseek-v3.1:671b-cloud",
    # "llama3.1:70b",
    # "qwen2.5:72b"
]

# Configure Logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    handlers=[
        logging.FileHandler(BASE_DIR / "benchmarking" / "validation" / "population" / "scripts" / "eval_log.txt"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Deep_Eval")

# ==========================================
# 3. HELPER FUNCTIONS
# ==========================================

def load_ground_truth() -> pd.DataFrame:
    """
    Loads all CSVs and merges them into a single Dataframe.
    Assigns the 'target_paper' label based on the dictionary map.
    """
    all_data = []
    
    for paper_id, csv_filename in PAPER_TO_CSV_MAP.items():
        csv_path = DATA_CSV_DIR / csv_filename
        if not csv_path.exists():
            logger.error(f"CRITICAL: Missing data file {csv_path}")
            continue
            
        df = pd.read_csv(csv_path)
        df['target_paper_label'] = paper_id
        all_data.append(df)
        
    master_df = pd.concat(all_data, ignore_index=True)
    logger.info(f"Successfully loaded ground truth: {len(master_df)} total cases across {len(all_data)} categories.")
    return master_df

def load_benchmarks() -> dict:
    """Loads the golden criteria and hypotheses from the JSON."""
    with open(BENCHMARK_JSON_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def clear_workspace():
    """Wipes the config.json between agent runs to prevent data bleeding."""
    if CONFIG_PATH.exists():
        try:
            CONFIG_PATH.unlink()
            logger.info(f"Cleared workspace config: {CONFIG_PATH}")
        except Exception as e:
            logger.warning(f"Failed to clear workspace config: {e}")

def calculate_deep_metrics(y_true_binary: list, y_pred_binary: list, target_paper_id: str, pred_uids: list, df_ground_truth: pd.DataFrame) -> dict:
    """
    Calculates advanced evaluation metrics including cross-contamination analysis.
    """
    precision = precision_score(y_true_binary, y_pred_binary, zero_division=0)
    recall = recall_score(y_true_binary, y_pred_binary, zero_division=0)
    f1 = f1_score(y_true_binary, y_pred_binary, zero_division=0)
    iou = jaccard_score(y_true_binary, y_pred_binary, zero_division=0)
    
    # Calculate Specificity (True Negative Rate)
    tn, fp, fn, tp = confusion_matrix(y_true_binary, y_pred_binary, labels=[0, 1]).ravel()
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0

    # Cross-Contamination Analysis (Where did the False Positives come from?)
    contamination_breakdown = {}
    if fp > 0:
        # Get the rows that the agent selected (safe string matching)
        selected_df = df_ground_truth[df_ground_truth['volume_series_instance_uid'].astype(str).str.strip().isin(pred_uids)]
        # Filter down to the ones that are NOT the target paper
        fp_df = selected_df[selected_df['target_paper_label'] != target_paper_id]
        
        # Count occurrences of wrong papers
        contamination_counts = fp_df['target_paper_label'].value_counts().to_dict()
        contamination_breakdown = contamination_counts

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1_score": round(f1, 4),
        "specificity": round(specificity, 4),
        "jaccard_index_iou": round(iou, 4),
        "confusion_matrix": {
            "true_positives": int(tp),
            "false_positives": int(fp),
            "true_negatives": int(tn),
            "false_negatives": int(fn)
        },
        "false_positive_sources": contamination_breakdown
    }

# ==========================================
# 4. MAIN ASYNC EVALUATION LOOP
# ==========================================

async def evaluate_model(model_name: str, df_ground_truth: pd.DataFrame, benchmarks: dict, results_ledger: dict):
    logger.info(f"\n\n{'='*80}\nCOMMENCING EVALUATION SUITE FOR MODEL: {model_name}\n{'='*80}")
    
    # Create the model client for AutoGen v0.4
    model_client_instance = OpenAIChatCompletionClient(
        model=model_name,
        api_key="EMPTY",
        base_url="http://localhost:11434/v1",
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": True,
            "family": "unknown"
        }
    )
    
    results_ledger[model_name] = {"papers": {}}
    f1_scores = []
    
    for paper_id, data in benchmarks.items():
        hypothesis = data.get("hypothesis")
        if not hypothesis:
            continue
            
        logger.info(f"\n--- Testing Paper: {paper_id} ---")
        clear_workspace()
        
        start_time = time.time()
        
        try:
            # 1. Instantiate the Agent (Injecting the specific LLM Client)
            planner_agent = await PopulationPlannerAgent.create(
                model_client=model_client_instance, 
                session_id=f"eval_{model_name}_{paper_id}",
                workspace_folder=str(WORKSPACE_DIR)
            )
            
            # 2. Execute - STRICTLY Hypothesis using native v0.4 method
            await planner_agent.run(
                task=f"Research Hypothesis: {hypothesis}\nIdentify and retrieve the optimal patient cohort."
            )
            
        except Exception as e:
            logger.error(f"Agent execution failed for {paper_id}: {str(e)}")
            continue
            
        execution_time = time.time() - start_time
        
        # 3. Extract Results from config.json
        selected_uids = []
        agent_inclusion = []
        agent_exclusion = []
        
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                    raw_uids = config_data.get("datasetlist", [])
                    # Deduplicate and cleanly cast UIDs to string to ensure perfect Pandas matching
                    selected_uids = list(set([str(uid).strip() for uid in raw_uids if uid]))
                    
                    agent_inclusion = config_data.get("inclusion_criteria", [])
                    agent_exclusion = config_data.get("exclusion_criteria", [])
            except Exception as e:
                logger.error(f"Failed reading config.json: {e}")
                
        # 4. Build Binary Vectors for Scoring (Enforce string casting)
        y_true = (df_ground_truth['target_paper_label'] == paper_id).astype(int).tolist()
        
        db_uids = df_ground_truth['volume_series_instance_uid'].astype(str).str.strip()
        y_pred = db_uids.isin(selected_uids).astype(int).tolist()
        
        # 5. Calculate Deep Metrics
        metrics = calculate_deep_metrics(y_true, y_pred, paper_id, selected_uids, df_ground_truth)
        f1_scores.append(metrics["f1_score"])
        
        logger.info(f"[{model_name}] F1: {metrics['f1_score']} | Spec: {metrics['specificity']} | Time: {round(execution_time, 2)}s")
        if metrics['confusion_matrix']['false_positives'] > 0:
            logger.warning(f"Contamination Detected: {metrics['false_positive_sources']}")
        
        # 6. Record
        results_ledger[model_name]["papers"][paper_id] = {
            "execution_time_seconds": round(execution_time, 2),
            "total_unique_selected": len(selected_uids),
            "metrics": metrics,
            "qualitative_extraction": {
                "golden_inclusion": data.get("inclusion_criteria", []),
                "agent_inclusion": agent_inclusion,
                "golden_exclusion": data.get("exclusion_criteria", []),
                "agent_exclusion": agent_exclusion
            }
        }
    
    # Calculate model's macro F1 across all papers
    if f1_scores:
        results_ledger[model_name]["overall_macro_f1"] = round(sum(f1_scores) / len(f1_scores), 4)

async def main():
    os.environ["WORKSPACE_DIR"] = str(WORKSPACE_DIR)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    df_ground_truth = load_ground_truth()
    benchmarks = load_benchmarks()
    
    if df_ground_truth.empty or not benchmarks:
        logger.error("Initialization failed. Check file paths.")
        return

    final_report = {
        "evaluation_metadata": {
            "total_cases_in_db": len(df_ground_truth),
            "models_tested": MODELS_TO_TEST,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        },
        "model_results": {}
    }
    
    # Run evaluations sequentially per model
    for model in MODELS_TO_TEST:
        await evaluate_model(model, df_ground_truth, benchmarks, final_report["model_results"])
        
    # Save the deep analytical report
    with open(OUTPUT_REPORT_PATH, 'w', encoding='utf-8') as f:
        json.dump(final_report, f, indent=4)
        
    logger.info(f"\n\n{'='*80}\nEVALUATION COMPLETE. \nFull Deep Report saved to: {OUTPUT_REPORT_PATH}\n{'='*80}")

if __name__ == "__main__":
    asyncio.run(main())