import handler from "vinext/server/fetch-handler";

interface SiteEnvironment {
  API_ORIGIN?: string;
  API_PROXY_TOKEN?: string;
}

export default {
  async fetch(request: Request, env: SiteEnvironment, ctx: ExecutionContext) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/") && !url.pathname.startsWith("/v1/")) {
      return handler.fetch(request, env, ctx);
    }
    if (url.pathname === "/api/status") return Response.json({ status: "ok" });
    if (!env.API_ORIGIN || !env.API_PROXY_TOKEN) {
      return Response.json({ error: { code: "backend_unavailable", message: "Сервер временно недоступен." } }, { status: 503 });
    }
    const upstream = new URL(env.API_ORIGIN);
    if (upstream.protocol !== "https:") {
      return Response.json({ error: { code: "backend_unavailable", message: "Сервер временно недоступен." } }, { status: 503 });
    }
    // Never resolve a visitor-controlled URL: only this configured origin is used.
    upstream.pathname = url.pathname;
    upstream.search = url.search;
    const headers = new Headers();
    for (const name of ["authorization", "cookie", "content-type", "accept", "origin"]) {
      const value = request.headers.get(name);
      if (value) headers.set(name, value);
    }
    if (headers.has("origin") && headers.get("origin") !== url.origin) {
      return Response.json({ error: { code: "invalid_origin", message: "Недопустимый адрес запроса." } }, { status: 403 });
    }
    headers.set("x-rubai-proxy-token", env.API_PROXY_TOKEN);
    const visitorIp = request.headers.get("cf-connecting-ip");
    if (visitorIp) headers.set("x-forwarded-for", visitorIp);
    if (url.pathname === "/api/auth/sites") {
      for (const [from, to] of [
        ["oai-authenticated-user-id", "x-rubai-sites-user-id"],
        ["oai-authenticated-user-email", "x-rubai-sites-user-email"],
      ]) {
        const value = request.headers.get(from);
        if (value) headers.set(to, value);
      }
    }
    try {
      const response = await fetch(upstream, {
        method: request.method,
        headers,
        body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
        redirect: "manual",
        signal: AbortSignal.timeout(180_000),
      });
      const responseHeaders = new Headers(response.headers);
      responseHeaders.set("cache-control", "private, no-store");
      responseHeaders.set("referrer-policy", "no-referrer");
      responseHeaders.set("x-content-type-options", "nosniff");
      return new Response(response.body, { status: response.status, headers: responseHeaders });
    } catch {
      return Response.json({ error: { code: "backend_unavailable", message: "Сервер временно недоступен. Попробуйте позже." } }, { status: 502 });
    }
  },
};
