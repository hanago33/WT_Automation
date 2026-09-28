*** Settings ***
Library    Process
Library    OperatingSystem
Resource   project_config.resource

*** Variables ***
${GM_LOG_FILE}           ${CURDIR}${/}..${/}gm_automation.log

*** Keywords ***
初始化调度配置
    ${env_api_key}=    Get Environment Variable    VOLC_API_KEY    ${EMPTY}
    IF    $env_api_key
        Set Suite Variable    ${VOLC_API_KEY}    ${env_api_key}
    END
    File Should Exist    ${RECORDED_SCRIPT}
    File Should Exist    ${UI_TARS_RUNNER}
    Directory Should Exist    ${UI_TARS_REPO_ROOT}
    Should Not Be Empty    ${VOLC_API_KEY}    msg=未获取到 VOLC_API_KEY，请先配置环境变量 VOLC_API_KEY。
    准备 UI-TARS Local Computer 配置

准备 UI-TARS Local Computer 配置
    File Should Exist    ${UI_TARS_CLI_CONFIG}
    Run Keyword And Ignore Error    Remove File    ${UI_TARS_STDOUT}
    Run Keyword And Ignore Error    Remove File    ${UI_TARS_STDERR}
    Run Keyword And Ignore Error    Remove File    ${GM_LOG_FILE}

执行 WT 自动化流程
    Log    [步骤1] 执行 WT 自动化完整流程（固定步骤 + AI 介入步骤）。
    ${result}=    Run Process    py    -3.11    ${RECORDED_SCRIPT}
    ...    cwd=${PROJECT_ROOT}
    ...    stdout=PIPE
    ...    stderr=PIPE
    ...    env:VOLC_API_KEY=${VOLC_API_KEY}
    ...    env:UI_TARS_VLM_BASE_URL=${UI_TARS_VLM_BASE_URL}
    ...    env:MODEL_NAME=${MODEL_NAME}
    ...    env:UI_TARS_REPO_ROOT=${UI_TARS_REPO_ROOT}
    ...    env:UI_TARS_CLI_CONFIG=${UI_TARS_CLI_CONFIG}
    Should Be Equal As Integers    ${result.rc}    0    msg=WT 自动化执行失败：${result.stderr}
    IF    $result.stdout != ''
        Log    WT 自动化输出：${result.stdout}
    END
    Log    WT 自动化日志已保存到：${GM_LOG_FILE}
    Log    [步骤1完成] WT 自动化完整流程执行完毕。
