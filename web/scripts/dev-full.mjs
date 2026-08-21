#!/usr/bin/env node
/**
 * `pnpm dev:full` — runs the whole stack for local testing: the FastAPI
 * backend (via `uv run uvicorn`, auto-reloading) and the Vite dev server,
 * in one terminal, with prefixed/colored output. Ctrl+C stops both.
 *
 * The frontend proxies /api to the backend (vite.config.ts), so
 * http://localhost:5173 alone is a fully working app — drives/jobs/queue,
 * the SSE event stream, and settings all round-trip through the real API.
 *
 * Data/work/library directories default to ./data, ./work, ./library at the
 * repo root — the same paths docker-compose.yml bind-mounts, and already
 * gitignored. Override any of them (or SPINDLE_PORT, SPINDLE_RUN_BACKGROUND_WORKERS,
 * or add secrets like SPINDLE_TMDB_API_KEY) via a .env file at the repo root;
 * pydantic-settings loads it automatically.
 */
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(webDir, "..");

const BACKEND_PORT = process.env.SPINDLE_PORT ?? "8478";
const FRONTEND_PORT = process.env.PORT ?? "5173";

const PROCS = [
  {
    name: "api",
    color: "36", // cyan
    command: "uv",
    args: [
      "run",
      "uvicorn",
      "spindle.main:create_app",
      "--factory",
      "--reload",
      "--host",
      "127.0.0.1",
      "--port",
      BACKEND_PORT,
    ],
    cwd: repoRoot,
    env: {
      SPINDLE_DATA_DIR: path.join(repoRoot, "data"),
      SPINDLE_WORK_DIR: path.join(repoRoot, "work"),
      SPINDLE_LIBRARY_DIR: path.join(repoRoot, "library"),
      SPINDLE_PORT: BACKEND_PORT,
      // The DriveWatcher/Dispatcher degrade gracefully with no optical
      // hardware (see adapters/optical.py) — leaving them on means the
      // real dispatcher and SSE event stream are exercised, not stubbed.
      SPINDLE_RUN_BACKGROUND_WORKERS: "true",
    },
  },
  {
    name: "web",
    color: "35", // magenta
    command: "pnpm",
    args: ["exec", "vite", "--port", FRONTEND_PORT],
    cwd: webDir,
    env: {},
  },
];

const children = [];
let shuttingDown = false;

function log(name, color, line) {
  process.stdout.write(`\x1b[${color}m[${name}]\x1b[0m ${line}\n`);
}

function prefixStream(stream, name, color) {
  let buffer = "";
  stream.on("data", (chunk) => {
    buffer += chunk.toString();
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() ?? "";
    for (const line of lines) log(name, color, line);
  });
}

// `shell: true` wraps each command in cmd.exe/sh, so killing just the direct
// child can orphan the real process (uvicorn's own --reload subprocess,
// vite) instead of stopping it. Killing the whole tree/group is the only
// reliable way to actually free the ports on Ctrl+C.
function killTree(child) {
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    try {
      process.kill(-child.pid, "SIGTERM");
    } catch {
      child.kill("SIGTERM");
    }
  }
}

function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) {
    if (!child.killed) killTree(child);
  }
  setTimeout(() => process.exit(code ?? 0), 500);
}

for (const proc of PROCS) {
  // A single joined string (rather than command + args[]) avoids Node's
  // DEP0190 warning for shell:true — safe here since every arg is a fixed
  // flag or a numeric port, nothing that needs quoting.
  const child = spawn([proc.command, ...proc.args].join(" "), {
    cwd: proc.cwd,
    env: { ...process.env, ...proc.env },
    shell: true,
    detached: process.platform !== "win32",
  });
  children.push(child);
  prefixStream(child.stdout, proc.name, proc.color);
  prefixStream(child.stderr, proc.name, proc.color);
  child.on("exit", (code) => {
    log(proc.name, proc.color, `exited (${code}) — stopping the rest of the stack`);
    shutdown(code ?? 1);
  });
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

log(
  "dev:full",
  "33",
  `backend on http://127.0.0.1:${BACKEND_PORT}, frontend on http://localhost:${FRONTEND_PORT} — open the frontend URL, it proxies /api for you.`,
);
