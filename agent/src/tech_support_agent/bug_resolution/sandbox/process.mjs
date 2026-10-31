import { spawn } from "node:child_process";

export const CHILD_ENV = {
  PATH: "/usr/local/bin:/usr/bin:/bin",
  HOME: "/tmp",
  CI: "true",
  PLAYWRIGHT_BROWSERS_PATH: "/opt/browsers",
};

export function killGroup(child) {
  try {
    process.kill(-child.pid, "SIGKILL");
  } catch (error) {
    if (error.code !== "ESRCH") throw error;
  }
}

export async function boundedProcess(
  executable,
  args,
  { cwd, timeout = 60000 } = {},
) {
  const child = spawn(executable, args, {
    cwd,
    env: CHILD_ENV,
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = Buffer.alloc(0);
  let timedOut = false;
  const collect = (data) => {
    if (output.length < 32768)
      output = Buffer.concat([output, data.subarray(0, 32768 - output.length)]);
  };
  child.stdout.on("data", collect);
  child.stderr.on("data", collect);
  const timer = setTimeout(() => {
    timedOut = true;
    killGroup(child);
  }, timeout);
  try {
    const code = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", resolve);
    });
    return {
      status: timedOut ? "timeout" : code === 0 ? "passed" : "failed",
      exit_code: code,
      output: output.toString("utf8"),
    };
  } finally {
    clearTimeout(timer);
    if (child.pid) killGroup(child);
  }
}
