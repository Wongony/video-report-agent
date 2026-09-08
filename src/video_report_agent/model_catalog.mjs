// Use the installed Pi catalog, authentication and provider transports.
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
const [piRoot, agentDir] = process.argv.slice(2);
const { ModelRuntime } = await import(pathToFileURL(join(piRoot, 'dist/core/model-runtime.js')));
const { AuthStorage } = await import(pathToFileURL(join(piRoot, 'dist/core/auth-storage.js')));
const { getSupportedThinkingLevels } = await import(pathToFileURL(join(piRoot, 'node_modules/@earendil-works/pi-ai/dist/compat.js')));
let input = '';
for await (const chunk of process.stdin) input += chunk;
const request = JSON.parse(input);
const credentials = AuthStorage.create(join(agentDir, 'auth.json'));
if (request.action === 'save-key') {
  await credentials.modify(request.provider, async () => ({ type: 'api_key', key: request.api_key }));
  process.stdout.write('{}');
} else {
  const runtime = await ModelRuntime.create({ credentials, modelsPath: join(agentDir, 'models.json'), allowModelNetwork: false });
  if (runtime.getError()) throw new Error(runtime.getError());
  if (request.action === 'check') {
    try {
      const model = runtime.getModel(request.provider, request.model);
      if (!model) throw new Error('Unknown model');
      const reply = await runtime.completeSimple(model, {
        messages: [{ role: 'user', content: 'Reply OK.', timestamp: Date.now() }]
      }, { maxTokens: 32, reasoning: request.thinking === 'off' ? undefined : request.thinking,
           ...(request.api_key ? { apiKey: request.api_key } : {}), signal: AbortSignal.timeout(20000) });
      const connected = ['stop', 'length'].includes(reply.stopReason);
      process.stdout.write(JSON.stringify({ connected }));
    } catch {
      process.stdout.write(JSON.stringify({ connected: false }));
    }
    process.exit(0);
  }
  const models = runtime.getModels().map(model => ({
    provider: model.provider, model: model.id, name: model.name,
    thinking_levels: getSupportedThinkingLevels(model),
    supports_api_key: !!runtime.getProvider(model.provider)?.auth?.apiKey,
    configured: runtime.hasConfiguredAuth(model.provider)
  }));
  process.stdout.write(JSON.stringify({ models }));
}
