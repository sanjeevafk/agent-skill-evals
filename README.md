# Agent Skill Evals (`agent-skill-evals`)

[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

A quantitative, multi-arm evaluation harness and pre-activation quality gate for AI agent skills (`SKILL.md`). Built on the research principles of **Sergii Makarevych (2026)** and the **Poteto Verification Playbooks**.

---

## 🎯 The Problem

Over a million agent skills exist in public registries, but the vast majority are **"vibe-written"**: authored by intuition, untested on empirical tasks, and never benchmarked against a control group. 

Published studies reveal:
* Unchecked skills increase prompt tokens by **72% to 451%** with zero gain in task success rate.
* An unrelated block of random text of the same length often moves benchmark results as much as the skill itself (the prompt-length placebo effect).
* A skill that provides **+33 points** on one model family can yield **-22 points** on another.

`agent-skill-evals` provides the tooling to measure whether an agent skill actually helps before it enters your prompt.

---

## 🔬 The 3-Arm Evaluation Protocol

```
                        ┌──────────────┐
                        │ Organic Task │
                        └──────┬───────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         ▼                     ▼                     ▼
   [Arm A: Base]        [Arm B: Candidate]     [Arm C: Control]
   No skill active      Candidate SKILL.md     Random filler of
                                               IDENTICAL length
         │                     │                     │
         └─────────────────────┼─────────────────────┘
                               ▼
                    [Blinded Evaluation]
                   Adherence vs. Effect
                               │
                               ▼
                    [Decision Matrix Verdict]
```

### 1. The Arms
* **Arm A (Baseline):** The organic task executed with zero skill injected. Measures raw foundation model capability.
* **Arm B (Candidate):** The task executed with the candidate `SKILL.md` in context.
* **Arm C (Length Control):** The task executed with an unrelated block of text with the **exact identical character and token length**. Isolates whether improvements stem from the instructions or merely from steering attention via context expansion.

### 2. Dual-Metric Decision Matrix

```
                     ┌───────────────────────┐
                     │ Adherence >= 70% ?    │
                     └──────────┬────────────┘
                                │
               ┌────────────────┴────────────────┐
              YES                                NO
               │                                 │
     ┌───────────────────┐              ┌────────────────┐
     │ Arm B > Arm C ?   │              │  REWRITE TEXT  │
     └─────────┬─────────┘              │(Ignored Prompt)│
               │                        └────────────────┘
        ┌──────┴──────┐
       YES            NO
        │             │
┌──────────────┐ ┌────────────────┐
│  SHIP & PIN  │ │   DROP IDEA    │
│(Causal Lift) │ │(Placebo/Slop)  │
└──────────────┘ └────────────────┘
```

---

## 🚀 Quickstart

### 1. Installation
Clone the repository and install locally:
```bash
git clone https://github.com/sanjeevafk/agent-skill-evals.git
cd agent-skill-evals
pip install -e .
```

### 2. Linting a Skill (Stage 0 Quality Gate)
Before spending compute running evals, run the linter to verify formatting, directive density, and security:
```bash
skill-lint path/to/SKILL.md
```

Checks performed:
* **Spec Conformance:** Valid YAML frontmatter with `name` and actionable `description`.
* **Directive Density:** Requires $\ge 1.5\text{ directives/KB}$. Flags conversational narrative fluff as rejected.
* **Budget Limits:** Flags files $> 20\text{ KB}$ to prevent stealth prompt bloat.
* **Security Screening:** Checks for unvetted `curl|sh`, plain-text credentials, and destructive file operations.

### 3. Running a 3-Arm Evaluation
```bash
skill-eval <skill_name_or_path> --prompt "Implement a thread-safe sliding window rate limiter in Python"
```

Output:
```
============================================================
📊 EVALUATION SCORECARD: poteto-mode
============================================================
Skill Size:      19,303 chars (~4,825 tokens)
Directives:      72 extracted test points
------------------------------------------------------------
RESULTS BY ARM:
  Arm A (Baseline):       Score = 0.65
  Arm B (Candidate):      Score = 0.92  |  Adherence = 88.0%
  Arm C (Length Control): Score = 0.68
------------------------------------------------------------
VERDICT:  SHIP & PIN
ANALYSIS: Skill delivered genuine causal lift: Arm B (0.92) > Arm C (0.68) > Baseline (0.65).
============================================================
```

### 4. Bulk Audit an Entire Active Suite
Scan an entire active skills directory:
```bash
skill-audit
```

---

## 🛠️ CLI Reference

| Command | Script | Description |
| :--- | :--- | :--- |
| `skill-lint <target>` | `src/lint_skill.py` | Validates frontmatter, directive density, and security constraints. |
| `skill-eval <target>` | `src/eval_skill.py` | Scaffolds and grades 3-arm sandboxes against an organic task. |
| `skill-audit` | `src/audit_suite.py` | Bulk audits active skills in `~/.gemini/config/skills`. |

---

## 📜 References
* Makarevych, S. (2026). *How to write and test agent skills with evals*.
* Poteto Mode Playbooks (`eval.md`, `shipping.md`).
* Agent Skills Specification: `https://agentskills.io/specification`
