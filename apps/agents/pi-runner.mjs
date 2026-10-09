import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { createAgentSession, createExtensionRuntime, ModelRuntime, SessionManager, SettingsManager } from "@earendil-works/pi-coding-agent";

const input = JSON.parse(await new Promise((resolve, reject) => {
  let value = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", chunk => { value += chunk; });
  process.stdin.on("end", () => resolve(value));
  process.stdin.on("error", reject);
}));
const workDir = process.cwd();
const agentDir = path.join(workDir, ".pi-agent");
await mkdir(agentDir, { recursive: true });

class EmptyResourceLoader {
  getExtensions() { return { extensions: [], errors: [], runtime: createExtensionRuntime() }; }
  getSkills() { return { skills: [], diagnostics: [] }; }
  getPrompts() { return { prompts: [], diagnostics: [] }; }
  getThemes() { return { themes: [], diagnostics: [] }; }
  getAgentsFiles() { return { agentsFiles: [] }; }
  getSystemPrompt() { return "You are a text-only assistant. Answer the user's request directly. You have no tools."; }
  getSystemPromptSource() { return undefined; }
  getAppendSystemPrompt() { return []; }
  getAppendSystemPromptSources() { return []; }
  extendResources() {}
  async reload() {}
}

try {
  await writeFile(path.join(agentDir, "models.json"), JSON.stringify({
    providers: {
      rubai: {
        baseUrl: input.platform_api_base,
        apiKey: "in-memory-key",
        api: "openai-completions",
        headers: input.proxy_token ? { "X-Rubai-Proxy-Token": input.proxy_token } : {},
        models: [{
          id: input.model,
          name: input.model,
          reasoning: false,
          input: ["text"],
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          contextWindow: 65536,
          maxTokens: 4096,
        }],
      },
    },
  }));
  const modelRuntime = await ModelRuntime.create({
    authPath: path.join(agentDir, "auth.json"),
    modelsPath: path.join(agentDir, "models.json"),
  });
  const model = modelRuntime.getModel("rubai", input.model);
  if (!model) throw new Error("Model unavailable");
  await modelRuntime.setRuntimeApiKey("rubai", input.api_key);

  const settingsManager = SettingsManager.inMemory({ compaction: { enabled: false } });
  const resourceLoader = new EmptyResourceLoader();
  const { session } = await createAgentSession({
    cwd: workDir,
    agentDir,
    model,
    modelRuntime,
    settingsManager,
    resourceLoader,
    sessionManager: SessionManager.inMemory(workDir),
    tools: [],
    noTools: true,
  });
  try {
    await session.prompt(input.prompt);
    process.stdout.write(JSON.stringify({ text: session.getLastAssistantText() || "" }));
  } finally {
    session.dispose();
  }
} catch {
  process.stderr.write("Agent request failed.\n");
  process.exitCode = 1;
}
