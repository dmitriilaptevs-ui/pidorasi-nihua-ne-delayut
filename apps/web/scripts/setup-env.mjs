import { existsSync, readFileSync, writeFileSync, chmodSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";

const target = new URL("../.env.local", import.meta.url);
if (existsSync(target)) {
  console.log("Existing .env.local preserved. No credentials were overwritten.");
} else {
  const template = readFileSync(new URL("../.env.example", import.meta.url), "utf8");
  const value = template.replace("SESSION_SECRET=\n", `SESSION_SECRET=${randomBytes(32).toString("base64url")}\n`);
  writeFileSync(target, value, { flag: "wx", mode: 0o600 });
  console.log("Created private local configuration with a random session secret.");
}
chmodSync(target, 0o600);
console.log(`Edit locally: ${fileURLToPath(target)}`);
console.log("Hidden file: Ctrl+H in your file manager. Never paste credentials into chat or GitHub.");
