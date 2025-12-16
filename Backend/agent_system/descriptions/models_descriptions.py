POPULATION_PLANNER_AGENT_DESCRIPTION = """
        (Planner) specialized AI that takes a research hypothesis, uses tools to gather data on relevant
        patients and studies, and then meticulously selects and defines the optimal study population
        (including inclusion/exclusion criteria) for the research. Its final action is to structure
        and save this definition using a designated output tool
        """
        
REPORTER_AGENT_DESCRIPTION = """
specialized AI that takes the final research findings, transformers it to latex and render it to pdf
"""

RESULT_AGENT_DESCRIPTION = """
Result Aggregation & Hypothesis Validation This agent functions as the final node in the research pipeline. It consumes the study's initial hypothesis,
execution plan, dataset characteristics, and model performance metrics. Its primary logic engine determines if the hypothesis
is 'accepted' or 'rejected' based on the empirical evidence. The output is a strictly
formatted JSON object containing a decision logic, a brief justification, and a full-text scientific summary formatted in Markdown.
this agent have the capability to access all the data about the research and the findings
"""

MODELING_AGENT_DESCRIPTION = """
specialized ai that have the capability to get the radiomics features and then run machine
learning algorithms on it and save the results
"""