import * as path from "path";
import * as vscode from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  TransportKind,
} from "vscode-languageclient/node";

let client: LanguageClient | undefined;
let outputChannel: vscode.OutputChannel;
let statusBarItem: vscode.StatusBarItem;

// ── Activation ──────────────────────────────────────────────

export async function activate(
  context: vscode.ExtensionContext,
): Promise<void> {
  outputChannel = vscode.window.createOutputChannel("DevLens");
  outputChannel.appendLine("DevLens extension activating...");

  // Status bar
  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left,
    100,
  );
  statusBarItem.text = "$(search) DevLens";
  statusBarItem.tooltip = "DevLens Code Analysis";
  statusBarItem.command = "devlens.analyzeFile";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // Register commands
  context.subscriptions.push(
    vscode.commands.registerCommand("devlens.analyzeFile", cmdAnalyzeFile),
    vscode.commands.registerCommand("devlens.showDashboard", cmdShowDashboard),
    vscode.commands.registerCommand("devlens.runAIReview", cmdRunAIReview),
    vscode.commands.registerCommand("devlens.restartServer", cmdRestartServer),
    vscode.commands.registerCommand("devlens.clearCache", cmdClearCache),
    vscode.commands.registerCommand("devlens.configureAI", cmdConfigureAI),
  );

  // Watch for configuration changes
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("devlens")) {
        onConfigurationChanged();
      }
    }),
  );

  // Start the language server
  const config = vscode.workspace.getConfiguration("devlens");
  if (config.get<boolean>("enabled", true)) {
    await startLanguageServer(context);
  }

  outputChannel.appendLine("DevLens extension activated.");
}

// ── Deactivation ────────────────────────────────────────────

export async function deactivate(): Promise<void> {
  if (client) {
    await client.stop();
    client = undefined;
  }
}

// ── Language Server Management ──────────────────────────────

async function startLanguageServer(
  context: vscode.ExtensionContext,
): Promise<void> {
  const config = vscode.workspace.getConfiguration("devlens");
  const pythonPath = config.get<string>("pythonPath", "python");
  const extraArgs = config.get<string[]>("args", []);
  const logLevel = config.get<string>("logLevel", "info");

  const serverArgs = [
    "-m",
    "devlens.language_server",
    "--mode",
    "stdio",
    "--log-level",
    logLevel,
    ...extraArgs,
  ];

  const serverOptions: ServerOptions = {
    run: {
      command: pythonPath,
      args: serverArgs,
      transport: TransportKind.stdio,
    },
    debug: {
      command: pythonPath,
      args: serverArgs,
      transport: TransportKind.stdio,
    },
  };

  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: "file", language: "python" },
      { scheme: "file", language: "javascript" },
      { scheme: "file", language: "typescript" },
      { scheme: "file", language: "javascriptreact" },
      { scheme: "file", language: "typescriptreact" },
      { scheme: "file", language: "java" },
      { scheme: "file", language: "go" },
      { scheme: "file", language: "rust" },
      { scheme: "file", language: "ruby" },
      { scheme: "file", language: "php" },
      { scheme: "file", language: "c" },
      { scheme: "file", language: "cpp" },
      { scheme: "file", language: "csharp" },
    ],
    outputChannel,
    traceOutputChannel: outputChannel,
    initializationOptions: getInitializationOptions(),
  };

  client = new LanguageClient(
    "devlens",
    "DevLens Language Server",
    serverOptions,
    clientOptions,
  );

  try {
    await client.start();
    outputChannel.appendLine("DevLens language server started.");
    updateStatusBar("ready");
  } catch (error) {
    outputChannel.appendLine(
      `Failed to start DevLens language server: ${error}`,
    );
    updateStatusBar("error");
    vscode.window.showErrorMessage(
      "DevLens: Failed to start language server. " +
        "Make sure DevLens is installed: pip install devlens",
    );
  }
}

async function stopLanguageServer(): Promise<void> {
  if (client) {
    await client.stop();
    client = undefined;
  }
  updateStatusBar("stopped");
}

function getInitializationOptions(): Record<string, unknown> {
  const config = vscode.workspace.getConfiguration("devlens");
  return {
    enabled: config.get<boolean>("enabled", true),
    lintOnSave: config.get<boolean>("lintOnSave", true),
    lintOnOpen: config.get<boolean>("lintOnOpen", true),
    lintOnChange: config.get<boolean>("lintOnChange", false),
    debounceMs: config.get<number>("debounceMs", 500),
    pythonPath: config.get<string>("pythonPath", "python"),
    configPath: config.get<string>("configPath", ""),
    logLevel: config.get<string>("logLevel", "info"),
    showCodeLens: config.get<boolean>("showCodeLens", true),
    maxFileSize: config.get<number>("maxFileSize", 500000),
    severityFilter: config.get<string>("severityFilter", "low"),
    aiReview: {
      enabled: config.get<boolean>("aiReview.enabled", false),
      provider: config.get<string>("aiReview.provider", "openai"),
      model: config.get<string>("aiReview.model", ""),
    },
  };
}

// ── Status Bar ──────────────────────────────────────────────

function updateStatusBar(
  state: "ready" | "analyzing" | "error" | "stopped",
): void {
  switch (state) {
    case "ready":
      statusBarItem.text = "$(search) DevLens";
      statusBarItem.backgroundColor = undefined;
      statusBarItem.tooltip = "DevLens: Ready";
      break;
    case "analyzing":
      statusBarItem.text = "$(loading~spin) DevLens";
      statusBarItem.tooltip = "DevLens: Analyzing...";
      break;
    case "error":
      statusBarItem.text = "$(error) DevLens";
      statusBarItem.backgroundColor = new vscode.ThemeColor(
        "statusBarItem.errorBackground",
      );
      statusBarItem.tooltip = "DevLens: Error -- click to restart";
      statusBarItem.command = "devlens.restartServer";
      break;
    case "stopped":
      statusBarItem.text = "$(circle-slash) DevLens";
      statusBarItem.tooltip = "DevLens: Stopped";
      break;
  }
}

// ── Commands ────────────────────────────────────────────────

async function cmdAnalyzeFile(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("DevLens: No active file to analyze.");
    return;
  }
  if (!client) {
    vscode.window.showWarningMessage(
      "DevLens: Language server is not running.",
    );
    return;
  }
  updateStatusBar("analyzing");
  try {
    const result = await client.sendRequest("workspace/executeCommand", {
      command: "devlens.analyzeFile",
      arguments: [editor.document.uri.toString()],
    });
    updateStatusBar("ready");
    if (result && typeof result === "object") {
      const r = result as Record<string, unknown>;
      vscode.window.showInformationMessage(
        `DevLens: Score ${r.score}/100 | ${r.findings} issue(s) | ${r.duration_ms}ms`,
      );
    }
  } catch (error) {
    updateStatusBar("ready");
    vscode.window.showErrorMessage(`DevLens: Analysis failed -- ${error}`);
  }
}

async function cmdShowDashboard(): Promise<void> {
  if (!client) {
    vscode.window.showWarningMessage(
      "DevLens: Language server is not running.",
    );
    return;
  }
  try {
    const result = await client.sendRequest("workspace/executeCommand", {
      command: "devlens.showDashboard",
      arguments: [],
    });
    if (result && typeof result === "object") {
      const r = result as Record<string, unknown>;
      if (r.output) {
        const uri = vscode.Uri.file(r.output as string);
        await vscode.env.openExternal(uri);
      } else if (r.error) {
        vscode.window.showErrorMessage(`DevLens: ${r.error}`);
      }
    }
  } catch (error) {
    vscode.window.showErrorMessage(
      `DevLens: Failed to open dashboard -- ${error}`,
    );
  }
}

async function cmdRunAIReview(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("DevLens: No active file to review.");
    return;
  }
  if (!client) {
    vscode.window.showWarningMessage(
      "DevLens: Language server is not running.",
    );
    return;
  }
  const config = vscode.workspace.getConfiguration("devlens");
  if (!config.get<boolean>("aiReview.enabled", false)) {
    const enable = await vscode.window.showInformationMessage(
      "DevLens: AI Review is disabled. Enable it?",
      "Enable",
      "Cancel",
    );
    if (enable === "Enable") {
      await config.update("aiReview.enabled", true, true);
    } else {
      return;
    }
  }
  updateStatusBar("analyzing");
  try {
    const result = await client.sendRequest("workspace/executeCommand", {
      command: "devlens.runAIReview",
      arguments: [editor.document.uri.toString()],
    });
    updateStatusBar("ready");
    if (result && typeof result === "object") {
      const r = result as Record<string, unknown>;
      if (r.error) {
        vscode.window.showErrorMessage(`DevLens AI: ${r.error}`);
      } else if (r.review) {
        // Show AI review in output channel
        outputChannel.appendLine("\n--- AI Review ---");
        outputChannel.appendLine(JSON.stringify(r.review, null, 2));
        outputChannel.appendLine("--- End AI Review ---\n");
        outputChannel.show();
      }
    }
  } catch (error) {
    updateStatusBar("ready");
    vscode.window.showErrorMessage(`DevLens AI: Review failed -- ${error}`);
  }
}

async function cmdRestartServer(): Promise<void> {
  outputChannel.appendLine("Restarting DevLens language server...");
  await stopLanguageServer();
  const context = {
    subscriptions: [],
  } as unknown as vscode.ExtensionContext;
  await startLanguageServer(context);
  vscode.window.showInformationMessage("DevLens: Language server restarted.");
}

async function cmdClearCache(): Promise<void> {
  if (!client) {
    vscode.window.showWarningMessage(
      "DevLens: Language server is not running.",
    );
    return;
  }
  try {
    await client.sendRequest("workspace/executeCommand", {
      command: "devlens.clearCache",
      arguments: [],
    });
    vscode.window.showInformationMessage("DevLens: Analysis cache cleared.");
  } catch (error) {
    vscode.window.showErrorMessage(
      `DevLens: Failed to clear cache -- ${error}`,
    );
  }
}

async function cmdConfigureAI(): Promise<void> {
  const providers = [
    { label: "OpenAI (GPT-4o)", value: "openai" },
    { label: "Anthropic (Claude)", value: "anthropic" },
  ];

  const selected = await vscode.window.showQuickPick(providers, {
    placeHolder: "Select AI provider for code review",
  });

  if (selected) {
    const config = vscode.workspace.getConfiguration("devlens");
    await config.update("aiReview.provider", selected.value, true);
    await config.update("aiReview.enabled", true, true);
    vscode.window.showInformationMessage(
      `DevLens: AI provider set to ${selected.label}. ` +
        `Configure your API key in .devlens.yml or environment variable.`,
    );
  }
}

// ── Configuration Change Handler ────────────────────────────

async function onConfigurationChanged(): Promise<void> {
  const config = vscode.workspace.getConfiguration("devlens");
  const enabled = config.get<boolean>("enabled", true);

  if (enabled && !client) {
    const context = {
      subscriptions: [],
    } as unknown as vscode.ExtensionContext;
    await startLanguageServer(context);
  } else if (!enabled && client) {
    await stopLanguageServer();
  } else if (client) {
    // Send updated settings to the server
    const settings = getInitializationOptions();
    await client.sendNotification(
      "workspace/didChangeConfiguration",
      { settings: { devlens: settings } },
    );
    outputChannel.appendLine("DevLens: Configuration updated.");
  }
}
