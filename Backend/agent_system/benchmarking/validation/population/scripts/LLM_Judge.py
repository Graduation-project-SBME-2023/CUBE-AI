import ollama
import json

def eval_criteria(extraction_data, model="deepseek-v3.1:671b-cloud"):
    
    system_prompt = """You are an expert clinical data evaluator. Your task is to compare AI-extracted patient criteria ('Agent') against the ground truth ('Golden') criteria.
    
    You will evaluate both Inclusion and Exclusion criteria based on:
    1. Recall (Missing): Did the Agent miss any critical constraints from the Golden set?
    2. Precision (Extra): Did the Agent hallucinate or add overly broad constraints not present in the Golden set?
    3. Semantic Equivalence: Recognize when the Agent and Golden sets say the same thing using different medical terminology.
    
    You MUST output your response in valid JSON format exactly like this:
    {
        "inclusion_analysis": "Brief analysis of the inclusion criteria match.",
        "exclusion_analysis": "Brief analysis of the exclusion criteria match.",
        "missed_golden_criteria": ["List any golden criteria the agent completely missed"],
        "hallucinated_agent_criteria": ["List any agent criteria that are wrong or too broad"],
        "overall_score": <int> from 1 to 10
    }
    """

    user_prompt = f"Please evaluate the following extraction:\n{json.dumps(extraction_data, indent=2)}"

    try:
        response = ollama.chat(
            model=model,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt}
            ],
            format='json' 
        )
        
        # 1. Get the raw string
        raw_text = response['message']['content'].strip()
        
        # 2. Check if the model returned nothing
        if not raw_text:
             return {"error": "The LLM returned an empty string. The model might be struggling with the prompt or ran out of memory."}
        
        # 3. Clean up potential markdown formatting (```json ... ```)
        if raw_text.startswith("```json"):
            raw_text = raw_text.replace("```json", "", 1)
        if raw_text.endswith("```"):
            raw_text = raw_text[::-1].replace("```", "", 1)[::-1] # remove last occurrence
            
        raw_text = raw_text.strip()

        # 4. Try parsing it
        return json.loads(raw_text)

    except json.JSONDecodeError as e:
        # If it STILL fails, this will show you exactly what the LLM outputted
        return {
            "error": "Failed to parse JSON", 
            "details": str(e),
            "raw_llm_output": raw_text 
        }
    except Exception as e:
        return {"error": "Ollama connection/execution failed", "details": str(e)}
    
# test code
# Your exact data
data = {
    "golden_inclusion": [
        "Enhanced arterial phase CT images of the neck",
        "Single or multiple enlarged lymph nodes with shortest diameter > 1cm on CT",
        "Pathology confirmed by surgery, biopsy, or clinical follow-up"
    ],
    "agent_inclusion": [
        "Patients with cervical lymph node imaging studies",
        "Studies showing enlarged cervical lymph nodes",
        "Cross-sectional imaging modalities (CT, MRI, ultrasound)",
        "Adults (age \u2265 18 years)"
    ],
    "golden_exclusion": [
        "Lesion reexamination after treatment",
        "CT images with artefacts affecting observation/measurement"
    ],
    "agent_exclusion": [
        "Pediatric patients (age < 18 years)",
        "Studies without visible cervical lymph nodes",
        "Non-cross-sectional imaging (e.g., plain films)",
        "Studies with significant artifacts affecting lymph node assessment",
        "Patients with known head and neck malignancies (to focus on benign/unknown etiology)",
        "Post-treatment or post-surgical cases"
    ]
}

# Run the judge
print("Evaluating criteria match...")
result = eval_criteria(data, model="deepseek-v3.1:671b-cloud")

# Print the structured JSON result
print(json.dumps(result, indent=4))