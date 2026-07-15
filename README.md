# WT Automation

WT Automation is a Windows-focused automation toolkit for recording, editing, validating, and executing WT workflow steps. The project includes a Tkinter flow editor, Robot Framework resources, Python execution helpers, control-map/template assets, and tests for core conversion and execution logic.

## Main Entry Points

- `WT_Launcher.py` - main launcher UI.
- `WT_Flow_Editor.py` - visual workflow editor.
- `WT_AUT_recorded.py` - recorded workflow execution entry point.
- `wt_flow_executor.py` - action execution and flow dispatch.
- `wt_flow_locator.py` - UI control locating and scoring.
- `flow_recorder_converter.py` - recorder-script to flow-definition conversion.

## Project Layout

- `resources/` - Robot Framework resource files.
- `control_maps/` - UI control-map definitions.
- `image_templates/` - image template assets.
- `flow_packages/` - reusable flow packages and registries.
- `samples/` - sample flows, recorder scripts, and legacy examples.
- `tests/` - pytest regression tests.
- `tools/ORC/` - bundled OCR/Tesseract runtime files.
- `docs/` - project documents and generated presentation/paper material.

## Local Setup

Install Python dependencies:

```powershell
pip install -r requirements-template-builder.txt
```

Run tests:

```powershell
pytest
```

Start the launcher:

```powershell
python WT_Launcher.py
```

## Secrets And Local State

Do not commit local API keys or runtime state. The repository ignores `launcher_state.json`, `.env*`, logs, caches, backup folders, debug screenshots, and generated artifacts by default.

API keys should be supplied through environment variables such as `VOLC_API_KEY` or `UI_TARS_API_KEY` when needed.
