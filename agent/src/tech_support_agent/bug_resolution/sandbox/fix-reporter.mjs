import path from "node:path";

export default class FixReporter {
  constructor() { this.tests = []; this.errors = []; }
  onError(error) { this.errors.push(String(error.message || error).slice(0, 4000)); }
  onTestEnd(test, result) {
    const assertions = [];
    const walk = (steps) => {
      for (const step of steps) {
        if (step.category === "expect" && step.error && step.location) {
          assertions.push({ title: step.title, file: path.relative("/work/project", step.location.file),
            line: step.location.line, message: String(step.error.message || "").slice(0, 4000) });
        }
        walk(step.steps || []);
      }
    };
    walk(result.steps);
    this.tests.push({ title: test.title, file: path.relative("/work/project", test.location.file),
      status: result.status, expected_status: test.expectedStatus, assertions,
      errors: result.errors.map((error) => String(error.message || error).slice(0, 4000)) });
  }
  onEnd() { process.stdout.write(JSON.stringify({ tests: this.tests, errors: this.errors })); }
}
