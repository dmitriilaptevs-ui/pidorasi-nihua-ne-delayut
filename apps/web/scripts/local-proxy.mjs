#!/usr/bin/env node
// Temporary loopback TCP proxy. No access logs, remote listeners or autostart.
// Next.js remains an ordinary user process on 127.0.0.1:3001.
import net from "node:net";

const isRoot = process.getuid?.() === 0;
let targetUid, targetGid;
if (isRoot) {
  targetUid = Number(process.env.SUDO_UID);
  targetGid = Number(process.env.SUDO_GID);
  if (!Number.isSafeInteger(targetUid) || targetUid <= 0 || !Number.isSafeInteger(targetGid) || targetGid < 0) {
    console.error("Run via sudo from your regular account; never as a standalone root service.");
    process.exit(1);
  }
}
let ready = false;
const sockets = new Set();
const server = net.createServer((client) => {
  if (!ready || client.remoteAddress !== "127.0.0.1") { client.destroy(); return; }
  const upstream = net.connect({ host: "127.0.0.1", port: 3001 });
  sockets.add(client); sockets.add(upstream);
  client.setTimeout(180_000, () => client.destroy());
  upstream.setTimeout(180_000, () => upstream.destroy());
  client.on("error", () => upstream.destroy());
  upstream.on("error", () => client.destroy());
  client.on("close", () => { sockets.delete(client); upstream.destroy(); });
  upstream.on("close", () => { sockets.delete(upstream); client.destroy(); });
  client.pipe(upstream).pipe(client);
});
server.maxConnections = 128;
server.on("error", (error) => {
  console.error(error.code === "EACCES" ? "Port 80 requires sudo. Run this proxy script with sudo, NOT the Next.js app." : `Proxy failed (${error.code || "unknown"}).`);
  process.exitCode = 1;
});
server.listen(80, "127.0.0.1", () => {
  try {
    if (isRoot) { process.setgroups([]); process.setgid(targetGid); process.setuid(targetUid); }
    ready = true;
    console.log("rubai local proxy: http://localhost → 127.0.0.1:3001");
    console.log(`Running as uid ${process.getuid?.()}; no request logging. Ctrl+C stops it.`);
  } catch {
    console.error("Could not drop privileges; refusing connections.");
    server.close();
    process.exit(1);
  }
});
function stop() { ready = false; for (const socket of sockets) socket.destroy(); server.close(); }
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
