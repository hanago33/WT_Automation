'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createRequire } = require('node:module');
const Module = require('node:module');

const DEFAULT_REPO_ROOT =
  process.env.UI_TARS_REPO_ROOT || 'C:\\Users\\14830\\UI-TARS-desktop';
const DEFAULT_CONFIG_PATH =
  process.env.UI_TARS_CLI_CONFIG ||
  path.join(os.homedir(), '.ui-tars-cli.json');
const STRICT_SYSTEM_PROMPT = `You are a desktop GUI agent.

You must operate the already opened desktop application window visible on the current screen.
Do not solve the task by creating, editing, or saving files in the workspace unless the GUI itself performs those actions.
Do not describe an action in natural language. Do not output explanations outside the required format.

You must output exactly this format:
Thought: <one short sentence>
Action: <exactly one action call from the action space>

Allowed actions:
click(start_box='[x1, y1, x2, y2]')
left_double(start_box='[x1, y1, x2, y2]')
right_single(start_box='[x1, y1, x2, y2]')
drag(start_box='[x1, y1, x2, y2]', end_box='[x3, y3, x4, y4]')
hotkey(key='')
type(content='')
scroll(start_box='[x1, y1, x2, y2]', direction='down or up or right or left')
wait()
finished()
call_user()

Rules:
- Use only one action call per turn.
- The Action line must be a valid function call from the list above.
- For click, left_double, right_single, drag, and scroll, every box must use exactly four comma-separated integers:
  [x1, y1, x2, y2]
- Never output shorthand coordinates like [282 975], [282,975], [0.282], [x, y], or a single point.
- If you only know one click point, convert it to a small valid box around that point, for example:
  click(start_box='[278, 971, 286, 979]')
- If you cannot produce a valid box with confidence, use wait() or call_user() instead of guessing.
- If the target window is covered, first activate the existing application window from the real desktop UI.
- If the target cannot be identified with confidence, output call_user().
- Never return only a prose description of what should be clicked.
- After clicking Save, do not assume success immediately.
- After clicking Save, your next turns must verify the result on screen:
  - if the save dialog is still visible, continue handling that dialog,
  - if a confirm/replace/format dialog appears, handle it first,
  - only when the save dialog has disappeared and the main application window is back in the expected state may you continue.
- If the save dialog remains open, do not close the main application window.
- If you are unsure whether the save actually succeeded, do not output finished(); use wait() or continue checking the UI.
- Do not output finished() until all of these are true:
  1. the copy has been saved successfully,
  2. any save or confirm dialog has disappeared,
  3. the main application window is visible again in the foreground,
  4. the final target window state matches the user instruction.

## User Instruction
`;

function readJsonFile(filePath, label) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    throw new Error(`读取${label}失败: ${filePath} (${reason})`);
  }
}

function parseBoolean(value) {
  if (value == null || value === '') {
    return undefined;
  }

  const normalized = String(value).trim().toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(normalized)) {
    return true;
  }
  if (['0', 'false', 'no', 'off'].includes(normalized)) {
    return false;
  }

  return undefined;
}

function formatError(error) {
  if (error instanceof Error) {
    return error.stack || error.message;
  }
  return String(error);
}

function isSuccessfulTerminalStatus(status) {
  return status === 'end';
}

function registerUiTarsAliases(repoRoot) {
  const aliasMap = new Map([
    ['@ui-tars/sdk', path.join(repoRoot, 'packages', 'ui-tars', 'sdk', 'dist', 'index.js')],
    ['@ui-tars/sdk/core', path.join(repoRoot, 'packages', 'ui-tars', 'sdk', 'dist', 'core.js')],
    [
      '@ui-tars/operator-nut-js',
      path.join(repoRoot, 'packages', 'ui-tars', 'operators', 'nut-js', 'dist', 'index.js'),
    ],
    [
      '@ui-tars/shared/types',
      path.join(repoRoot, 'packages', 'ui-tars', 'shared', 'dist', 'types', 'index.js'),
    ],
    [
      '@ui-tars/shared/constants',
      path.join(repoRoot, 'packages', 'ui-tars', 'shared', 'dist', 'constants', 'index.js'),
    ],
    [
      '@ui-tars/shared/utils',
      path.join(repoRoot, 'packages', 'ui-tars', 'shared', 'dist', 'utils', 'index.js'),
    ],
    [
      '@ui-tars/action-parser',
      path.join(repoRoot, 'packages', 'ui-tars', 'action-parser', 'dist', 'index.js'),
    ],
  ]);

  for (const [specifier, targetPath] of aliasMap) {
    if (!fs.existsSync(targetPath)) {
      throw new Error(`缺少 UI-TARS 依赖产物: ${specifier} -> ${targetPath}`);
    }
  }

  const originalResolveFilename = Module._resolveFilename;
  if (originalResolveFilename.__uiTarsPatched) {
    return;
  }

  const patchedResolveFilename = function patchedResolveFilename(
    request,
    parent,
    isMain,
    options,
  ) {
    if (aliasMap.has(request)) {
      return aliasMap.get(request);
    }
    return originalResolveFilename.call(this, request, parent, isMain, options);
  };

  patchedResolveFilename.__uiTarsPatched = true;
  Module._resolveFilename = patchedResolveFilename;
}

async function main() {
  const rawArgs = process.argv.slice(2);
  const checkOnly = rawArgs.includes('--check');
  const prompt = rawArgs.filter((arg) => arg !== '--check').join(' ').trim();

  if (!checkOnly && !prompt) {
    throw new Error('缺少 AI 指令，请传入要执行的桌面操作描述。');
  }

  const repoRoot = path.resolve(DEFAULT_REPO_ROOT);
  const repoPackageJson = path.join(repoRoot, 'package.json');
  if (!fs.existsSync(repoPackageJson)) {
    throw new Error(`未找到 UI-TARS 仓库: ${repoPackageJson}`);
  }

  registerUiTarsAliases(repoRoot);

  if (!fs.existsSync(DEFAULT_CONFIG_PATH)) {
    throw new Error(`未找到 UI-TARS 配置文件: ${DEFAULT_CONFIG_PATH}`);
  }

  const fileConfig = readJsonFile(DEFAULT_CONFIG_PATH, 'UI-TARS 配置');
  const repoRequire = createRequire(repoPackageJson);
  const { GUIAgent } = repoRequire('@ui-tars/sdk');
  const { NutJSOperator } = repoRequire('@ui-tars/operator-nut-js');

  const useResponsesApi =
    parseBoolean(process.env.UI_TARS_USE_RESPONSES_API) ??
    Boolean(fileConfig.useResponsesApi);

  const modelConfig = {
    baseURL: process.env.UI_TARS_VLM_BASE_URL || fileConfig.baseURL,
    apiKey:
      process.env.VOLC_API_KEY ||
      process.env.UI_TARS_API_KEY ||
      fileConfig.apiKey,
    model: process.env.MODEL_NAME || process.env.UI_TARS_MODEL || fileConfig.model,
    useResponsesApi,
  };

  if (!modelConfig.baseURL || !modelConfig.apiKey || !modelConfig.model) {
    throw new Error('模型配置不完整，请检查 baseURL、apiKey、model。');
  }

  console.log(`[ui-tars-runner] repo=${repoRoot}`);
  console.log(`[ui-tars-runner] config=${DEFAULT_CONFIG_PATH}`);
  console.log(`[ui-tars-runner] model=${modelConfig.model}`);
  console.log(`[ui-tars-runner] baseURL=${modelConfig.baseURL}`);

  if (checkOnly) {
    console.log('[ui-tars-runner] check passed');
    return;
  }

  const abortController = new AbortController();
  process.on('SIGINT', () => {
    console.error('[ui-tars-runner] 收到中断信号，正在停止 GUIAgent...');
    abortController.abort();
  });

  let lastStatus = 'INIT';
  let lastMessage = '';

  const guiAgent = new GUIAgent({
    model: modelConfig,
    operator: new NutJSOperator(),
    systemPrompt: STRICT_SYSTEM_PROMPT,
    signal: abortController.signal,
    onData: ({ data }) => {
      if (data && data.status && data.status !== lastStatus) {
        lastStatus = data.status;
        console.log(`[ui-tars-runner][status] ${lastStatus}`);
      }

      const conversations = Array.isArray(data?.conversations)
        ? data.conversations
        : [];

      for (const item of conversations) {
        if (!item || item.from === 'screenshotBase64') {
          continue;
        }

        const rawValue =
          typeof item.value === 'string'
            ? item.value
            : JSON.stringify(item.value ?? '');
        const text = rawValue.replace(/\s+/g, ' ').trim();
        if (!text || text === lastMessage) {
          continue;
        }

        lastMessage = text;
        console.log(`[ui-tars-runner][${item.from || 'unknown'}] ${text}`);
      }
    },
    onError: ({ error }) => {
      console.error(`[ui-tars-runner][error] ${formatError(error)}`);
    },
  });

  await guiAgent.run(prompt);
  console.log(`[ui-tars-runner] finished status=${lastStatus}`);
  if (!isSuccessfulTerminalStatus(lastStatus)) {
    throw new Error(`GUIAgent 未完成目标，最终状态: ${lastStatus}`);
  }
}

process.on('unhandledRejection', (error) => {
  console.error(`[ui-tars-runner][fatal] ${formatError(error)}`);
  process.exitCode = 1;
});

main().catch((error) => {
  console.error(`[ui-tars-runner][fatal] ${formatError(error)}`);
  process.exitCode = 1;
});
