*** Settings ***
Resource    resources/dispatch_keywords.resource

*** Test Cases ***
WT 完整自动化流程
    [Documentation]    执行 WT 数据导入、投影、覆盖区、网格、导出的完整流程
    初始化调度配置
    执行 WT 自动化流程
    Log    ==================== WT 自动化流程全部执行完毕 ====================
