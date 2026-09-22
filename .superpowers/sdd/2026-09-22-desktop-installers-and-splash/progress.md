# SDD ledger - plan: docs/superpowers/plans/2026-09-22-desktop-installers-and-splash.md

Pre-flight: project is not a Git repository; commits and worktrees are intentionally skipped.

Task 1: complete
Verification: `uv run python -m unittest tests.test_runtime_logic tests.test_api_lifecycle -v` passed, 45 tests.

Task 2: complete
Verification: `uv run python -m unittest tests.test_hdw_model_runtime -v` passed, 2 tests. Model script now selects CUDA, CoreML, then CPU from actual runtime capabilities and retries CPU after accelerator session failure.

Task 3: complete
Verification: `npm run test:splash`, `npm run test:config`, `npm run test:workflow`, `cargo check --manifest-path src-tauri/Cargo.toml`, and `npm run build` passed. Added the real capability-driven Splash and Tauri dual-window flow.
