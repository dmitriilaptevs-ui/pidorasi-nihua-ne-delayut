import { cpSync, mkdirSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const projectRoot = fileURLToPath(new URL("../../", import.meta.url));
const webRoot = fileURLToPath(new URL("../../apps/web/", import.meta.url));
const cli = fileURLToPath(new URL("../../apps/web/node_modules/vinext/dist/cli.js", import.meta.url));
const build = spawnSync(process.execPath, [cli, "build"], {
  cwd: webRoot,
  stdio: "inherit",
  env: { ...process.env, SITES_BUILD: "1" },
});
if (build.error) throw build.error;
if (build.status !== 0) process.exit(build.status ?? 1);

// Sites packages the monorepo root; its Worker output is built by apps/web.
rmSync(new URL("../../dist/", import.meta.url), { recursive: true, force: true });
mkdirSync(new URL("../../dist/", import.meta.url), { recursive: true });
cpSync(`${webRoot}/dist`, `${projectRoot}/dist`, { recursive: true });
