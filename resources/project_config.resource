*** Variables ***
${PROVIDER}             volcengine
${VOLC_API_KEY}         
${MODEL_NAME}           doubao-seed-2-0-lite-260428
${UI_TARS_VLM_BASE_URL}    https://ark.cn-beijing.volces.com/api/v3
${PROJECT_ROOT}         ${CURDIR}${/}..
${RECORDED_SCRIPT}      ${CURDIR}${/}..${/}WT_AUT_recorded.py
${GM_READY_CHECK_SCRIPT}    ${CURDIR}${/}..${/}wait_global_mapper_ready.py
${UI_TARS_REPO_ROOT}    C:\\Users\\14830\\UI-TARS-desktop
${UI_TARS_DIR}          C:\\Users\\14830\\UI-TARS-desktop\\apps\\ui-tars
${UI_TARS_RUNNER}       ${CURDIR}${/}..${/}ui_tars_runner.js
${UI_TARS_ALIAS}        ui_tars_dev
${UI_TARS_CLI_CONFIG}   C:\\Users\\14830\\.ui-tars-cli.json
${UI_TARS_STDOUT}       ${CURDIR}${/}..${/}ui_tars_stdout.log
${UI_TARS_STDERR}       ${CURDIR}${/}..${/}ui_tars_stderr.log
${APP_START_WAIT}       5
${GM_READY_TIMEOUT}     180
${GM_EXE}               C:\\Program Files\\Meteodyn\\MeteodynUniverse\\MUPSmartClient.exe
${SOURCE_FILE_PATH}     
${OUTPUT_DIR}           
