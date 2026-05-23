# 🤝 Private Contribution Guidelines

Welcome to the internal development branch of Strat. 

As a pre-requisite to writing code in this repository, you **must** have a signed Non-Disclosure Agreement (NDA) and an Intellectual Property (IP) Assignment Agreement on file with human resources.

---

## 🔒 Confidentiality & Security Directives

### 1. Leak Prevention Protocol
*   **API Credentials**: Under no circumstances should Kite API keys, Kite session tokens, or model endpoints be hardcoded or checked into Git. Use the Tauri `Security Vault` or local, untracked `.env` files.
*   **Public Exposure**: Forking this repository to personal accounts or creating public mirrors is strictly forbidden and constitutes a breach of your employment/consulting contract.

### 2. Standard Branching Strategy
All work must follow our strict feature-branch workflow:
1.  Pull the latest updates from the master branch:
    ```powershell
    git pull origin master
    ```
2.  Create a branch using the naming convention:
    *   `feature/quant-[brief-description]` for indicator, algorithm, and LLM changes.
    *   `feature/ui-[brief-description]` for terminal frontend panels.
    *   `fix/pipeline-[brief-description]` for database, API, and connection errors.

---

## 🛠️ Code Standards & Mathematical Rigor

*   **Documentation Integrity**: All quantitative indicator computations (such as VWEPR curvature fits, ATR, VWAP regressions) must be fully commented and annotated with mathematical formulas in standard LaTeX markdown if applicable.
*   **Memory Management**: In Rust modules (`/ingestion`, `/aggregator`, `/frontend/src-tauri`), ensure strict bounds checking, prevent memory leaks, and guard against division-by-zero errors in volume calculations.

---

## 🧪 Pre-Merge Quality Verification

Before submitting a Pull Request for peer review, you **must** run the local verification checklist. PRs with failing pipeline steps or compilation errors will be automatically rejected.

### 1. Execute RUST API Contract Tests
```powershell
cd frontend/src-tauri
cargo test --test api_tests
```

### 2. Verify Frontend Telemetry Build
Ensure Next.js compiles without any TypeScript or Tailwind lints:
```powershell
cd ../frontend
npm run build
```
