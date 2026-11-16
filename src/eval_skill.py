#!/usr/bin/env python3
"""
eval_skill.py - 3-Arm Evaluation Harness for AI Agent Skills
Based on Sergii Makarevych's Eval Framework (2026) and poteto-mode eval playbook,
extended with 4-bucket dataset coverage, activation verification, pass@k,
multi-skill interaction scoring, and secret redaction.

Arms:
  Arm A: Baseline (No skill)
  Arm B: Candidate Skill
  Arm C: Length Control (Unrelated text of exact identical character/token length)

Metrics:
  1. Activation: Did the agent actually invoke/follow the candidate skill?
  2. Adherence: Did the agent follow the specific extracted directives?
  3. Effect: Did the output improve beyond length control (Arm B > Arm C > Arm A)?
"""

import argparse
import json
import math
import os
import random
import re
import shutil
import string
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add script directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lint_skill import extract_directives, extract_frontmatter, lint_skill

# Unrelated filler corpora for Arm C (Length Control)
LOREM_CORPUS = (
    "The astrophysical dynamics of binary pulsars demonstrate relativistic frame dragging "
    "under extreme gravitational curvature. Observational data collected from radio interferometry "
    "arrays indicate periodic modulation consistent with post-Newtonian approximations. "
    "High-energy plasma ejections from magnetar flares exhibit non-thermal synchrotron emission "
    "profiles that correlate with crustal rupture events. Thermodynamics of accretion disks around "
    "intermediate-mass black holes reveal turbulent magneto-rotational instability driving angular "
    "momentum transport across event horizon boundaries."
)

VALID_BUCKETS = ("explicit", "implicit", "negative", "edge")

_SECRET_PATTERNS = [
    (re.compile(r"\b(sk-[A-Za-z0-9]{16,}|nvapi-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16})\b"), "<redacted-key>"),
    (re.compile(r"(Authorization:\s*(?:Bearer|Basic|Token)\s+)[^\s\"']+", re.IGNORECASE), r"\1<redacted>"),
]


def redact_secrets(text: str) -> str:
    """Mask common API keys, tokens, and Authorization header values in saved artifacts."""
    masked = text
    for pattern, replacement in _SECRET_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


def compute_pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k estimator: 1 - comb(n - c, k) / comb(n, k)."""
    if n <= 0 or k <= 0:
        return 0.0
    k = min(k, n)
    if n - c < k:
        return 1.0
    return round(1.0 - math.comb(n - c, k) / math.comb(n, k), 4)


def compute_skill_interaction(p_empty: float, p_a: float, p_b: float, p_ab: float) -> float:
    """
    Compute two-skill interaction: I(A, B) = P(A, B) - P(A) - P(B) + P(empty).
    Positive = synergistic, negative = interfering, zero = additive.
    """
    return round(p_ab - p_a - p_b + p_empty, 4)


def generate_length_control(target_length: int) -> str:
    """Generate unrelated text matching the exact character length of the skill."""
    words = LOREM_CORPUS.split()
    generated = []
    current_len = 0
    while current_len < target_length:
        w = random.choice(words)
        if current_len + len(w) + 1 <= target_length:
            generated.append(w)
            current_len += len(w) + 1
        else:
            padding = string.ascii_letters
            remainder = target_length - current_len
            if remainder > 0:
                generated.append("".join(random.choices(padding, k=remainder)))
            break
    return " ".join(generated)[:target_length]


def parse_skill(skill_path: Path) -> Dict[str, Any]:
    """Extract frontmatter, directives, and token/character length from SKILL.md."""
    if not skill_path.exists():
        raise FileNotFoundError(f"Skill file not found at: {skill_path}")

    text = skill_path.read_text(encoding="utf-8")
    _, fm_name, desc = extract_frontmatter(text)
    name = fm_name or skill_path.parent.name
    directives = extract_directives(text)

    char_count = len(text)
    est_tokens = int(char_count / 4)

    return {
        "path": str(skill_path),
        "name": name,
        "description": desc,
        "raw_text": text,
        "char_count": char_count,
        "est_tokens": est_tokens,
        "directives": directives,
    }


def load_eval_dataset(dataset_path: Path) -> List[Dict[str, Any]]:
    """Load eval cases from JSON (list of cases or {'cases': [...]}) and normalize fields."""
    raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = raw["cases"] if isinstance(raw, dict) and "cases" in raw else raw
    if not isinstance(cases, list):
        raise ValueError(f"Expected list of eval cases in {dataset_path}")

    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(cases, start=1):
        bucket = item.get("bucket", "implicit")
        if bucket not in VALID_BUCKETS:
            bucket = "implicit"
        should_trigger = item.get("should_trigger")
        if should_trigger is None:
            should_trigger = bucket != "negative"
        normalized.append({
            "id": str(item.get("id", f"case-{idx:03d}")),
            "bucket": bucket,
            "prompt": item["prompt"],
            "expected_output": item.get("expected_output", ""),
            "assertions": item.get("assertions", []),
            "should_trigger": bool(should_trigger),
        })
    return normalized


def summarize_dataset_coverage(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Count cases per bucket and report missing coverage buckets."""
    counts = {b: 0 for b in VALID_BUCKETS}
    for c in cases:
        counts[c["bucket"]] = counts.get(c["bucket"], 0) + 1
    gaps = [b for b, n in counts.items() if n == 0]
    return {"total_cases": len(cases), "buckets": counts, "coverage_gaps": gaps}


def _write_arm_bundle(
    target_dir: Path,
    skill_info: Dict[str, Any],
    prompt: str,
    control_text: str,
) -> Dict[str, Path]:
    arm_dirs = {
        "arm_a_baseline": target_dir / "arm_a_baseline",
        "arm_b_candidate": target_dir / "arm_b_candidate",
        "arm_c_control": target_dir / "arm_c_control",
    }
    for p in arm_dirs.values():
        if p.exists():
            shutil.rmtree(p)
        os.makedirs(p, exist_ok=True)
        (p / "PROMPT.txt").write_text(redact_secrets(prompt), encoding="utf-8")

    (arm_dirs["arm_a_baseline"] / "SKILL_INJECTED.txt").write_text("", encoding="utf-8")
    (arm_dirs["arm_b_candidate"] / "SKILL_INJECTED.txt").write_text(skill_info["raw_text"], encoding="utf-8")
    (arm_dirs["arm_c_control"] / "SKILL_INJECTED.txt").write_text(control_text, encoding="utf-8")
    return arm_dirs


def scaffold_eval_workspace(
    skill_info: Dict[str, Any],
    target_dir: Path,
    organic_prompt: str,
    cases: Optional[List[Dict[str, Any]]] = None,
    n_attempts: int = 1,
) -> Dict[str, Any]:
    """Scaffold blinded 3-arm test directories (single prompt or multi-case dataset)."""
    os.makedirs(target_dir, exist_ok=True)
    control_text = generate_length_control(skill_info["char_count"])

    if cases:
        case_arms: Dict[str, Dict[str, str]] = {}
        for case in cases:
            cid = case["id"]
            arms = _write_arm_bundle(target_dir / cid, skill_info, case["prompt"], control_text)
            case_arms[cid] = {k: str(v) for k, v in arms.items()}
        dataset_summary = summarize_dataset_coverage(cases)
        arms_manifest: Any = case_arms
        return_arms: Any = case_arms
    else:
        arms = _write_arm_bundle(target_dir, skill_info, organic_prompt, control_text)
        dataset_summary = None
        arms_manifest = {k: str(v) for k, v in arms.items()}
        return_arms = arms

    manifest = {
        "skill_name": skill_info["name"],
        "char_count": skill_info["char_count"],
        "est_tokens": skill_info["est_tokens"],
        "n_attempts": n_attempts,
        "directives_count": len(skill_info["directives"]),
        "directives": skill_info["directives"],
        "dataset_summary": dataset_summary,
        "arms": arms_manifest,
    }
    (target_dir / "eval_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return return_arms


def grade_and_decide(
    arm_a_score: float,
    arm_b_score: float,
    arm_c_score: float,
    adherence_rate: float,
    activated: bool = True,
) -> Tuple[str, str]:
    """
    Apply Makarevych's Dual-Metric Decision Matrix + Activation Gate:
    - Never Activated: Inconclusive (do not credit Arm B)
    - Low Adherence (<70%): Rewrite text
    - High Adherence + Flat Effect (B <= C): Drop idea
    - High Adherence + Genuine Lift (B > C > A): Ship & Pin
    """
    if not activated:
        verdict = "INCONCLUSIVE (NEVER ACTIVATED)"
        reason = "Skill was never activated/invoked in Arm B positive trials; cannot attribute lift."
    elif adherence_rate < 0.70:
        verdict = "REWRITE TEXT"
        reason = f"Adherence is only {adherence_rate * 100:.1f}%. The agent failed to reliably follow the skill directives."
    elif arm_b_score <= arm_c_score:
        verdict = "DROP IDEA (FOLKLORE)"
        reason = f"Skill effect ({arm_b_score:.2f}) did not beat length control ({arm_c_score:.2f}). Gains were placebo/length-driven."
    elif arm_b_score > arm_c_score and arm_b_score > arm_a_score:
        verdict = "SHIP & PIN"
        reason = f"Skill delivered genuine causal lift: Arm B ({arm_b_score:.2f}) > Arm C ({arm_c_score:.2f}) > Baseline ({arm_a_score:.2f})."
    else:
        verdict = "INCONCLUSIVE"
        reason = "Scores did not show statistically separable separation. Re-run with more trials."

    return verdict, reason


def print_scorecard(skill_info: Dict[str, Any], results: Dict[str, Any]):
    print("\n" + "=" * 60)
    print(f"📊 EVALUATION SCORECARD: {skill_info['name']}")
    print("=" * 60)
    print(f"Skill Size:      {skill_info['char_count']} chars (~{skill_info['est_tokens']} tokens)")
    print(f"Directives:      {len(skill_info['directives'])} extracted test points")
    print("-" * 60)
    print("RESULTS BY ARM:")
    print(f"  Arm A (Baseline):       Score = {results['arm_a_score']:.2f}")
    print(f"  Arm B (Candidate):      Score = {results['arm_b_score']:.2f}  |  Adherence = {results['adherence_rate'] * 100:.1f}%")
    print(f"  Arm C (Length Control): Score = {results['arm_c_score']:.2f}")
    if "pass_at_k" in results:
        print(f"  Reliability (pass@k):   {results['pass_at_k']:.2%}")
    print("-" * 60)
    verdict, reason = grade_and_decide(
        results["arm_a_score"],
        results["arm_b_score"],
        results["arm_c_score"],
        results["adherence_rate"],
        activated=results.get("activated", True),
    )
    print(f"VERDICT:  \033[1;32m{verdict}\033[0m" if verdict == "SHIP & PIN" else f"VERDICT:  \033[1;31m{verdict}\033[0m")
    print(f"ANALYSIS: {reason}")
    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="3-Arm Skill Eval Harness")
    parser.add_argument("skill", help="Path to SKILL.md or skill name")
    parser.add_argument("--prompt", default="Implement a high-performance LRU cache in Python with O(1) ops and thread safety.", help="Organic test task prompt")
    parser.add_argument("--dataset", default=None, help="Path to 4-bucket evals.json dataset")
    parser.add_argument("--n-attempts", type=int, default=1, help="Number of trials per arm (for pass@k reliability)")
    parser.add_argument("--outdir", default="/tmp/skill_eval_workspace", help="Workspace for isolated trial sandboxes")
    parser.add_argument("--scaffold-only", action="store_true", help="Only scaffold the 3-arm sandboxes and manifest")
    parser.add_argument("--mock-run", action="store_true", help="Run simulated grading pipeline for dry-run verification")

    args = parser.parse_args()

    skill_path = Path(args.skill)
    if not skill_path.exists():
        candidates = [
            Path(os.path.expanduser("~/.gemini/config/skills")) / args.skill / "SKILL.md",
            Path(os.path.expanduser("~/.agents/skills")) / args.skill / "SKILL.md",
            Path(os.path.expanduser("~/Downloads/agent-skills/canonical")) / args.skill / "SKILL.md",
        ]
        found = False
        for c in candidates:
            if c.exists():
                skill_path = c
                found = True
                break
        if not found:
            print(f"Error: Could not locate skill '{args.skill}'", file=sys.stderr)
            sys.exit(1)
    elif skill_path.is_dir():
        skill_path = skill_path / "SKILL.md"

    # Stage 0: Pre-Eval Quality Gate (Makarevych Standard)
    passed, errors, warnings = lint_skill(skill_path)
    if not passed:
        print(f"\n❌ [Stage 0 Gate Failed] Cannot evaluate '{skill_path.parent.name}' due to quality errors:")
        for e in errors:
            print(f"   - {e}")
        print("\nResolve linting errors before spending compute on 3-arm evals.\n")
        sys.exit(1)

    skill_info = parse_skill(skill_path)
    outdir = Path(args.outdir)

    cases: Optional[List[Dict[str, Any]]] = None
    dataset_path = Path(args.dataset) if args.dataset else (skill_path.parent / "evals" / "evals.json")
    if dataset_path.exists():
        cases = load_eval_dataset(dataset_path)

    print(f"\n🔍 Parsing Skill: {skill_info['name']}")
    print(f"   Path:        {skill_info['path']}")
    print(f"   Footprint:   {skill_info['char_count']:,} chars (~{skill_info['est_tokens']:,} tokens)")
    print(f"   Directives:  {len(skill_info['directives'])} key points extracted")
    if cases is not None:
        cov = summarize_dataset_coverage(cases)
        print(f"   Dataset:     {cov['total_cases']} cases {cov['buckets']} (gaps: {cov['coverage_gaps'] or 'none'})")

    arm_dirs = scaffold_eval_workspace(skill_info, outdir, args.prompt, cases=cases, n_attempts=args.n_attempts)
    print(f"\n📁 Scaffolded 3-arm trial sandboxes in: {outdir}")
    if not cases:
        print(f"   [Arm A] Baseline:       {arm_dirs['arm_a_baseline']}")
        print(f"   [Arm B] Candidate:      {arm_dirs['arm_b_candidate']}")
        print(f"   [Arm C] Length Control: {arm_dirs['arm_c_control']}")
    else:
        print(f"   Cases scaffolded:       {len(arm_dirs)} ({args.n_attempts} attempt(s) configured)")

    if args.scaffold_only:
        print("\nScaffolding complete. Manifest written to eval_manifest.json.")
        sys.exit(0)

    if args.mock_run:
        results = {
            "arm_a_score": 0.65,
            "arm_b_score": 0.92,
            "arm_c_score": 0.68,
            "adherence_rate": 0.88,
            "activated": True,
            "pass_at_k": compute_pass_at_k(max(3, args.n_attempts), max(2, args.n_attempts), 1),
        }
        print_scorecard(skill_info, results)


if __name__ == "__main__":
    main()
