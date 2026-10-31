# Bug Resolution Demo Runbook

## Current Verification Status

Phase 6 is not complete and end-to-end readiness is not established. Live model/Telegram/GitHub rehearsal is blocked by missing operator configuration; the last preflight stopped at `BUG_DEMO_REPO` and `BUG_DEMO_REPO_DIR`, so later credentials were not verified. Browser execution is blocked by an authorized external Clerk test session. Frontend production build previously failed resolving the existing Roboto/Turbopack Google-font module.

CRITICAL: Docker candidate verification is deliberately disabled and returns `blocked`. Review reproduced fabricated test reports on test-controlled stdout and temporary source/harness mutations restored before digest checks. Neither vulnerability is resolved. The runtime refuses successful Docker verification bundles, including prior evidence, so a real approved fix cannot proceed to publication. There is no configuration bypass. Restart any existing worker to load the guard; do not publish using earlier verification evidence. The rehearsal instructions below describe the intended workflow, not currently achievable readiness. Test intake, investigation, clarification, Telegram discussion/rejection/approval, and the truthful blocked outcome instead; keep `BUG_PUBLICATION_AUTHORIZED=false`.

The final shared-worktree offline suite passed 333 tests (18 deselected), with Ruff import checks and formatting checks passing for all 86 Python files. Other quick checks passed 26 preflight/rehearsal/lifecycle tests, frontend TypeScript, discovery of six desktop/mobile Playwright tests, and `git diff --check`. The two real-server/Docker scenario tests previously passed with deterministic investigation/team doubles and plan rejection, not successful live fixes. Earlier successful candidate-verification Docker tests predate the safety lockdown and are not evidence that the integrity defects are fixed.

## Scope And Safety

This is a local, single-active-case demo, not a production support service. The existing support assistant remains unchanged. The public `bug_demo` graph passes classified reports to a private loopback worker; only clarification and a neutral handoff acknowledgement reach the client. Private investigation, Telegram discussion, approval, patches, evidence, PR and CI results stay outside public state/history/SSE.

Use only the operator-selected demo repository and private Telegram chat. Do not expose or tunnel Agent Server. Clerk protects the frontend, but this demo does not add authenticated ownership checks to the loopback Agent Server API. Never put service credentials in `NEXT_PUBLIC_*`, test fixtures, screenshots, or version control. There is no merge, deployment, automatic approval, persistent restart recovery, or automatic remote-resource deletion.

## Configure Before Launch

Install dependencies with `make install`. Use Python 3.14, Docker with a local Unix-socket daemon, Git, GitHub CLI, Node and pnpm. Build the trusted sandbox image using the README's Phase 2 instructions. Prepare the separate repository with both independent `demo/continue` and `demo/both` branches and the exact trusted harness/workflow. Both local refs must match their remote branch SHAs; do not reuse a fixed branch as a new base.

Set server-only values in `agent/.env` or the process environment:

| Configuration | Purpose |
| --- | --- |
| `BUG_WORKER_TOKEN`, `BUG_WORKER_URL` | Same random token in public server and worker; URL must be `http://127.0.0.1:<port>` (default 2025). |
| `BUG_DEMO_REPO`, `BUG_DEMO_REPO_DIR` | Confirmed `owner/name` and absolute external local repository directory. No repository is guessed or created. |
| `BUG_SANDBOX_IMAGE` | Explicitly prepared trusted image. |
| `BUG_MODEL`, `BUG_MODEL_API_KEY`, `BUG_MODEL_BASE_URL` | Operator-confirmed OpenRouter model ID and credentials; endpoint is `https://openrouter.ai/api/v1`. No fallback model. |
| `BUG_TELEGRAM_BOT_TOKEN`, `BUG_TELEGRAM_USER_ID`, `BUG_TELEGRAM_CHAT_ID` | Authorized private user/chat; open the bot conversation first. |
| `BUG_ARTIFACTS_DIR` | Existing absolute regular directory with no symlink ancestors; restrict directory access to the operator. Evidence is retained here. |
| `BUG_GITHUB_TOKEN`, `BUG_GITHUB_WORKFLOW_ID` | Explicit host-only token and numeric ID of `.github/workflows/verify.yml` in the selected repository. |
| `BUG_PUBLICATION_AUTHORIZED` | Defaults false. Set true only after authorizing branch pushes, draft PR creation/update and ready/draft changes in that demo repository. |
| `BUG_CI_POLL_SECONDS` | CI polling interval, default 10 seconds. |

The GitHub token needs repository contents write, pull requests write, and Actions read access, plus any organization-required authorization. Repository permission metadata alone cannot prove every fine-grained token scope or branch rule. Do not grant broader access just to make a test pass.

## Preflight

```bash
make bug-preflight
# Separate diagnostics:
uv run --python 3.14 --directory agent python -m tech_support_agent.bug_resolution.preflight --stage investigation
uv run --python 3.14 --directory agent python -m tech_support_agent.bug_resolution.preflight --stage publication
```

Investigation preflight captures both independent local bases, verifies actual Docker browser/screenshot operations, checks configured model tool calling and structured output, and reads Telegram identity/chat information. Model requests may incur provider charges. It does not send a team message or simulate approval. Publication preflight uses only Git `ls-remote` and GitHub GET requests: repository identity/archive/push metadata, local/remote base equality, active exact workflow identity, trusted workflow bytes at both bases, and readable workflow runs. It does not trial-push, create a PR, or prove CI passed. The existing CLI wrapper's internal authorization flag is used for these read-only probes; that is not runtime publication authorization.

An absent input is a blocker, not a skipped successful check. Preflight reports field names without dumping credential values. Passing preflight is not a successful rehearsal.

## Real Rehearsal

Start the public bridge with `make bug-demo-agent`. Start the normal Clerk-authenticated frontend with `make frontend` and sign in as an authorized test user. **Do not start `make bug-worker` alongside rehearsal**: rehearsal owns the private worker port and Telegram poller. Stop any other poller using the same bot. Keep public/frontend services on loopback.

```bash
# After explicitly setting BUG_PUBLICATION_AUTHORIZED=true:
make bug-rehearsal SCENARIO=continue LIVE=--run-live
# Finish or stop the first run, then use a separate fresh thread/base:
make bug-rehearsal SCENARIO=both LIVE=--run-live
```

For different local ports or a time budget, run the module directly with `--public-url http://127.0.0.1:2024`, `--frontend-url http://127.0.0.1:3000`, and `--timeout 2400`. The timeout includes waiting for the human and CI; it never authorizes work. The explicit `--run-live` flag and publication setting are both required. The runner rechecks preflight, creates a fresh public thread, starts actual runtime services and logs its UI URL. It never submits the report, client answers, Telegram events, patches, or a canned successful result.

### Continue

1. Open the logged thread URL and report that selecting a grade leaves Continue disabled. Answer a clarification only if genuinely asked.
2. Inspect the real Telegram reproduction screenshot and proposed source scope. Confirm that the plan both enables Continue for a selected grade and verifies an actual next-step transition.
3. Click the current plan's approval button only if acceptable. Discuss or reject instead when needed; free-text approval is not permission.
4. Wait for actual regression-red-on-base, green candidate checks, independent browser evidence/review, draft publication and exact-head GitHub CI. Inspect the final Telegram result and actual PR; a handoff message in the client is not success.

### Both

1. Use the separate fresh Both run and report that choosing Both loses Reading in the final summary. Its base must allow Continue normally.
2. In Telegram, make a genuine product choice between a combined Math+Reading plan and separate subject plans. Reply to the current bot message; no scenario preset chooses for you.
3. Inspect the revised proposal and separately approve its current buttons. A discussion reply does not grant write authority. Try an old button only as a negative check; it must not change the current plan.
4. Inspect actual before/after evidence, regression behavior for the chosen product interpretation, candidate/head identity and current GitHub `tests`/`build` jobs.

The three-attempt limit is global to the case, including CI retries and reapproval. Pending CI and uncertain transport are not new fix attempts. Changed approaches require a new approval. Failure or exhaustion must produce an honest manual handoff, not readiness.

## Inspect Evidence And Stop Safely

The runner writes a new mode-0600 `rehearsal-<run_id>.json` in `BUG_ARTIFACTS_DIR`, without overwriting previous runs. It records scenario/base, thread/run/case, observed plan revision and attempt count, human product choice, available candidate digest, PR/head/CI identity, actual local image hashes/sizes, outcome and cleanup confirmation. A terminal acknowledgement alone cannot make the manifest ready: the integrated runtime must have matching verified export, publication and passed exact-head CI receipts. Exit zero means ready with confirmed cleanup; rejection, timeout, interruption, blocking and failure exit nonzero.

Review the manifest alongside retained verifier logs, before/after PNGs, PR evidence report, exact head SHA and actual Actions run. Local fixture receipts are not live evidence. A missing manifest after startup failure is not success. If a run is interrupted, private checkpoint progress may be incomplete in the manifest; inspect retained evidence and remote state before doing anything else.

Ctrl-C/SIGTERM asks rehearsal to cancel polling and close coordinator/runtime/container resources. It does not close, delete or merge a PR, delete a remote branch, or remove evidence. Public Agent Server and frontend are operator-owned and must be stopped separately. Check `docker ps --all --filter name=bug-sandbox-` for unexpected survivors; investigate and remove only resources you own. Cleanup failure blocks further admission. After a worker restart, use a fresh thread: old callback capabilities are invalid and the in-memory run is not resumable. Reconcile existing PR/head state manually before restarting work; never force-push as recovery.

## Automated Coverage

```bash
uv run --python 3.14 --directory agent python -m pytest tests -m 'not server and not docker and not live' -q
uv run --python 3.14 --directory agent python -m pytest tests/bug_resolution/test_preflight.py tests/bug_resolution/test_rehearsal.py tests/bug_resolution/test_worker_lifecycle.py -q
uv run --python 3.14 --directory agent python -m pytest tests/bug_resolution/integration/test_public_server.py --run-server -q
uv run --python 3.14 --directory agent python -m pytest tests/bug_resolution/integration/test_end_to_end.py --run-server --run-docker -q
```

The Phase 6 end-to-end test runs a real Agent Server, HTTP worker, private graph and Docker browser against each independent fixture base. Its investigator actions and team transport are deterministic test doubles; it rejects the plan after testing callback authorization, exclusive ownership, real reproduction evidence, cleanup and public privacy. It does **not** claim a live model-generated fix, remote PR, GitHub CI or human approval. Separate workflow/publication suites exercise those boundaries with clearly labeled doubles/local Git. Only the real operator rehearsal can establish live demo completion.

### Authenticated Frontend Harness

Use a separate Clerk **test** instance with its `pk_test_` publishable key and `sk_test_` server key. Sign in an authorized test user at `http://127.0.0.1:3100` and export that browser's Playwright storage state to an absolute path outside this repository. Set `BUG_E2E_STORAGE_STATE` to that file; it is a credential, so restrict permissions and never commit it. The suite validates these prerequisites before starting servers. It provides no production middleware change, bypass, synthetic session, or substitute sign-in.

```bash
pnpm -C frontend exec playwright install chromium
pnpm -C frontend test:e2e --list
# Requires external authorized storage state plus Clerk test keys:
pnpm -C frontend test:e2e
```

The harness owns ports 3100 (Next.js), 2028 (private test worker), 2027 (test control), and a dynamically chosen real Agent Server port. Its test-only control endpoints use a fixed non-production token and are never imported by production entrypoints. Run sequentially on desktop Chrome and Pixel 7: submit, reload, edit without ID, regenerate, clarification, public-state privacy, concurrent-thread busy and unavailable worker. Private services are doubles here; browser success is not a live Telegram/GitHub rehearsal. Tracing is off; failure screenshots go to an external output directory (`BUG_E2E_OUTPUT_DIR`, default `/tmp/bug-handoff-playwright-results`). Keep screenshots private and remove expired sessions after use.

If no authorized Clerk session is available, report browser verification blocked. Test discovery, TypeScript/lint checks and Python transport tests do not replace actual browser assertions. Do not mark the full demo complete until both real scenarios, current CI, cleanup, and required human verification have passed.
