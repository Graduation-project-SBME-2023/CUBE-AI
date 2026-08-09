# Backend testing guide

All backend automated tests and manual test harnesses live under
`Backend/tests`. Production services do not need to be running for the
deterministic suite.

## Quick start

The recommended Windows command uses Docker, so it does not depend on a local
Python installation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File Backend/tests/run_tests.ps1 `
  -Suite deterministic
```

List every available suite:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File Backend/tests/run_tests.ps1 `
  -Suite list
```

The execution-policy flag is needed only on Windows installations that block
local PowerShell scripts.

## Runner options

`Backend/tests/run_tests.ps1` accepts:

| Option | Values | Purpose |
|---|---|---|
| `-Suite` | `deterministic` | Fast unit, contract, and mocked full-pipeline tests. This is the PR gate. |
| `-Suite` | `agent-regression` | Consolidated agent-system resilience and segmentation regression tests. |
| `-Suite` | `radiology` | Model-artifact and VoxTell geometry tests in the MONAI/Torch image. |
| `-Suite` | `integration` | Health checks against running Docker services. |
| `-Suite` | `all` | Runs deterministic, agent regression, then radiology. |
| `-Runtime` | `docker` | Default. Uses the correct project container image. |
| `-Runtime` | `local` | Uses the Python command supplied by `-Python`. |
| `-Python` | executable/path | Local Python command; default is `python`. |
| `-Coverage` | switch | Generates terminal, XML, and HTML coverage for the deterministic suite. |

Examples:

```powershell
# Deterministic tests with coverage
powershell -NoProfile -ExecutionPolicy Bypass `
  -File Backend/tests/run_tests.ps1 `
  -Suite deterministic -Coverage

# Agent regression suite
powershell -NoProfile -ExecutionPolicy Bypass `
  -File Backend/tests/run_tests.ps1 `
  -Suite agent-regression

# Local Python 3.11 environment
powershell -NoProfile -ExecutionPolicy Bypass `
  -File Backend/tests/run_tests.ps1 `
  -Suite deterministic -Runtime local `
  -Python C:/Python311/python.exe

# Running-service checks after docker compose is healthy
powershell -NoProfile -ExecutionPolicy Bypass `
  -File Backend/tests/run_tests.ps1 `
  -Suite integration
```

JUnit reports are written to `Backend/test-results`. Coverage output is written
to `Backend/coverage.xml` and `Backend/htmlcov`.

## Direct pytest commands

Install Python 3.11.3, the backend runtime requirements, and then the separate
test requirements:

```powershell
python -m pip install -r Backend/agent_system/requirements.txt
python -m pip install -r Backend/requirements-test.txt
```

Run only deterministic tests:

```powershell
python -m pytest -c Backend/pytest.ini `
  Backend/tests/unit Backend/tests/contracts Backend/tests/pipeline `
  -m "unit or contract or pipeline"
```

Run agent regressions:

```powershell
python -m pytest -c Backend/pytest.ini `
  Backend/tests/regression/agent_system -m regression
```

Run radiology regressions from an environment containing MONAI and Torch:

```powershell
python -m pytest -c Backend/pytest.ini `
  Backend/tests/regression/radiology -m radiology
```

## Test layout and responsibility

### Test infrastructure

| File | Function |
|---|---|
| `Backend/pytest.ini` | Test discovery, strict markers, asyncio behavior, and suite marker definitions. |
| `Backend/conftest.py` | Canonical import order, temporary storage isolation, shared workspaces, fake MCP tool descriptors, agent-constructor capture, and automatic regression/radiology markers. |
| `Backend/requirements-test.txt` | Test-only packages: pytest, asyncio support, coverage, mocks, timeouts, and HTTP mocking. |
| `Backend/tests/run_tests.ps1` | Unified Docker/local runner, suite selection, JUnit reports, coverage, and integration-service URLs. |
| `Backend/tests/support/mcp_loader.py` | Loads repository MCP tool modules privately without colliding with the installed `mcp` SDK or legacy `tools` package. |

### Execution-agent unit tests

| File | Function |
|---|---|
| `unit/agents/test_population_agent.py` | Population agent factory, allowed tools, environment, manual/automatic labeling mode, and tool-order filtering. |
| `unit/agents/test_segmentation_agent.py` | Session/workspace validation, segmentation tool wiring, module cleanup, and structured segmentation output. |
| `unit/agents/test_feature_extraction_agent.py` | Required runtime context and exact radiomics MCP tool contract. |
| `unit/agents/test_modeling_agent.py` | Classification tool exposure, environment propagation, and prevention of unrelated tool access. |
| `unit/agents/test_result_reporter_agent.py` | Result reporter identity, iteration budget, and reporting-only tool contract. |
| `unit/agents/test_latex_reporter_agent.py` | LaTeX reporter templates, plots, output readers, and PDF-tool contract. |

### Planning and literature tests

| File | Function |
|---|---|
| `unit/planning/test_context_compiler.py` | Creates ordered task messages for all six agents, locks ROI context to segmentation, formats prompts, and bounds knowledge summaries. |
| `unit/literature/test_literature_review.py` | Tests all five literature-review agent prompt roles, dynamic context routing, weighted PICO relevance scoring, and malformed input recovery. |

### Function-level tests

| File | Function |
|---|---|
| `unit/functions/test_execution_state.py` | Status normalization, first-incomplete-step selection, safe recovered states, and restart rules. |
| `unit/functions/test_feature_validation.py` | Minimum mask voxels and NIfTI image/mask affine alignment warnings. |
| `unit/functions/test_feature_cache.py` | Stable cache keys, invalidation by mtime/bin width, JSON round trips, and corrupt-cache fallback. |
| `unit/functions/test_workspace_helpers.py` | Logical/legacy workspace path mapping and empty input behavior. |

### Data-contract tests

| File | Function |
|---|---|
| `contracts/test_task_message_contract.py` | TaskMessage JSON round trip and isolation of mutable defaults. |
| `contracts/test_agent_output_contract.py` | Agent output write/read round trip, aggregate pipeline status, corrupt-file handling, and Pydantic validation. |

### Pipeline tests

| File | Function |
|---|---|
| `pipeline/test_pipeline_builder.py` | Full six-agent graph, planner ordering, unknown/duplicate IDs, predecessor filters, resume slicing, and invalid plans. |
| `pipeline/test_pipeline_orchestration.py` | Runs the production `run_pipeline` function with deterministic model, graph, monitor, and WebSocket substitutes; verifies complete pipeline signaling. |

### Integration tests

| File | Function |
|---|---|
| `integration/test_service_health.py` | Opt-in HTTP checks for agents, control API, MONAI Label, and Orthanc. It runs only when `RUN_BACKEND_INTEGRATION=1`. |

### Consolidated agent regression tests

| File | Function |
|---|---|
| `regression/agent_system/test_clinical_roi_policy.py` | Clinical ROI precedence, ambiguity, whole-organ versus lesion rules, and prompt policy consistency. |
| `regression/agent_system/test_empty_pipeline_guards.py` | Empty/malformed planning outputs, payload limits, fallback behavior, and user failure notifications. |
| `regression/agent_system/test_llm_retry.py` | Retry behavior for normal and streaming model-client requests. |
| `regression/agent_system/test_pipeline_failure_guard.py` | Fatal segmentation-tool failure detection and exception propagation. |
| `regression/agent_system/test_population_handoff.py` | Durable population artifacts and retry-on-incomplete-handoff behavior. |
| `regression/agent_system/test_progressive_segmentation.py` | Pair-level automatic segmentation progress, callbacks, snapshots, and partial completion. |
| `regression/agent_system/test_segmentation_hardening.py` | Segmentation schemas, ROI resolution, geometry, empty masks, recovery, reuse, failures, and checkpoint safety. |
| `regression/agent_system/test_segmentation_naming.py` | Deterministic DICOM segmentation series descriptions. |
| `regression/agent_system/test_session_async_safety.py` | Deferred persistence, queue lifecycle, and async session/WebSocket safety. |
| `regression/agent_system/test_storage_paths.py` | Logical storage mapping and traversal rejection. |
| `regression/agent_system/test_task_cancellation.py` | Task-manager cancellation, segmentation cancellation guards, and cleanup. |
| `regression/agent_system/test_timeout_resilience.py` | Tool deadlines, durable inference polling, and best-effort persistence lifecycle. |

### Consolidated radiology regression tests

| File | Function |
|---|---|
| `regression/radiology/test_model_artifacts.py` | Model snapshot completeness, archive validation, redownload behavior, and download skipping. |
| `regression/radiology/test_qwen_embedding_model.py` | Qwen embedding model metadata, required files, size/integrity checks, and recovery. |
| `regression/radiology/test_voxtell_artifacts.py` | VoxTell v1.0/v1.1 checkpoints, embedding archives, truncation removal, and verified downloads. |
| `regression/radiology/test_voxtell_geometry.py` | VoxTell image orientation, NRRD/DICOM geometry, and tensor/image conversion behavior. |

### Manual and load-test harnesses

These files are intentionally not collected by pytest. Run them only against a
backend instance you intend to exercise.

| File | Function and example |
|---|---|
| `manual/view_payload_harness.py` | Sends deterministic feature/model chart payloads to the frontend. Example: `python Backend/tests/manual/view_payload_harness.py --views all --session test-session`. |
| `manual/upload_pdf_smoke.py` | Sends one LaTeX PDF WebSocket-output event. Example: `python Backend/tests/manual/upload_pdf_smoke.py`. |
| `manual/endpoint_load_test.py` | Concurrent GET/POST load generator with latency CSV. Example: `python Backend/tests/manual/endpoint_load_test.py --url http://localhost:8000/info/ --requests 20 --concurrency 4 --log load-results.csv`. |

## Isolation and quality rules

- Unit, contract, and pipeline tests must not contact Ollama, databases,
  Orthanc, MONAI Label, or the public internet.
- Generated files must use `tmp_path`, `workspace_dir`, or a test-specific
  temporary directory.
- Fixtures must use synthetic data, never patient data.
- Model clients and MCP discovery are mocked in deterministic tests.
- Tests must not depend on execution order.
- Every agent handoff should be asserted as a persisted schema or explicit
  contract.
- Live service, LLM, GPU, and slow tests must carry their corresponding marker.

