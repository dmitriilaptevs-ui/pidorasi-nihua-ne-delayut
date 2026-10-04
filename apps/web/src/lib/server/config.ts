export interface ServerConfig {
  origin: string;
  sessionSecret: string;
  vk: { clientId: string; appType: "public" | "confidential"; serviceToken: string };
  yandex: { clientId: string; clientSecret: string };
  openRouterKey: string;
  freeModelAllowlist: string[];
}

/** This iteration is deliberately local-only, not a public deployment. */
export function config(): ServerConfig {
  const url = new URL(process.env.APP_ORIGIN || "http://localhost");
  if (!(["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)) ||
      !["http:", "https:"].includes(url.protocol) || url.username || url.password ||
      url.pathname !== "/" || url.search || url.hash) {
    throw new Error("The onboarding lab requires a loopback APP_ORIGIN.");
  }
  const appType = process.env.VK_APP_TYPE?.trim() || "confidential";
  if (appType !== "public" && appType !== "confidential") {
    throw new Error("VK_APP_TYPE must match the registered application type.");
  }
  return {
    origin: url.origin,
    sessionSecret: process.env.SESSION_SECRET?.trim() || "",
    vk: {
      clientId: process.env.VK_CLIENT_ID?.trim() || "",
      appType,
      serviceToken: process.env.VK_SERVICE_TOKEN?.trim() || "",
    },
    yandex: {
      clientId: process.env.YANDEX_CLIENT_ID?.trim() || "",
      clientSecret: process.env.YANDEX_CLIENT_SECRET?.trim() || "",
    },
    openRouterKey: process.env.OPENROUTER_API_KEY?.trim() || "",
    freeModelAllowlist: (process.env.OPENROUTER_FREE_MODELS || "").split(",").map((x) => x.trim()).filter(Boolean),
  };
}

export function validSecret(secret: string): boolean {
  return /^[A-Za-z0-9_-]{43,}$/.test(secret);
}
