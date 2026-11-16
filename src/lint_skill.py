#!/usr/bin/env python3
"""
lint_skill.py - No-Vibe Skill Linter & Pre-Activation Quality Gate
Enforces Makarevych's 2026 Eval Standards + Tier 1 static checks before skills enter active runtime.

Checks:
  1. Spec Integrity: Valid YAML frontmatter with non-empty name and description.
  2. Trigger Clarity: Description states when/how to use the skill and is sufficiently detailed.
  3. Directive Density: Minimum 1.5 actionable directives per KB (eliminates conversational fluff).
  4. Prompt Budget: Flags files > 20 KB to prevent stealth context bloat.
  5. Security Gate: Unvetted curl|sh, wget|sh, destructive rm, prompt-injection/stealth phrases,
     and sensitive path or hardcoded secret patterns.
  6. Structural Hygiene: Nested SKILL.md discovery hazards and broken relative file links.
  7. Quality Score (0-100): Deterministic score & letter grade for ranking and CI gates.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

DENSITY_THRESHOLD = 1.5  # directives per KB
MAX_KB_WARNING = 20.0    # KB budget

DANGER_PATTERNS = [
    (r"curl\s+-[sS]*\s+.*?\s*\|\s*(ba)?sh", "Unvetted curl-to-bash execution"),
    (r"wget\s+.*?\s*\|\s*(ba)?sh", "Unvetted wget-to-bash execution"),
    (r"rm\s+-rf\s+[/|\$]", "Destructive recursive removal pattern"),
    (r"(do\s+not|never)\s+(mention|tell|disclose|reveal)\s+(this\s+)?to\s+the\s+user", "Stealth instruction / hidden behavior directive"),
    (r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", "Prompt-injection override pattern"),
    (r"(~|/home/[^/\s]+|root)/\.(ssh|aws|gnupg|netrc)", "Sensitive credential directory reference"),
    (r"\b(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9_-]{20,})\b", "Potential hardcoded API key or token"),
]

TRIGGER_PHRASES = (
    "use when",
    "when ",
    "whenever",
    "use this",
    "trigger",
    "if the user",
    "useful for",
    "designed for",
)


def extract_frontmatter(text: str) -> Tuple[bool, str, str]:
    """Extract (has_frontmatter, name, description) from SKILL.md text."""
    fm_match = re.search(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not fm_match:
        return False, "", ""

    fm = fm_match.group(1)
    name_match = re.search(r"^name:\s*(.+)", fm, re.MULTILINE)
    desc_match = re.search(r"^description:\s*(.+)", fm, re.MULTILINE)
    name = name_match.group(1).strip().strip("\"'") if name_match else ""
    desc = desc_match.group(1).strip().strip("\"'") if desc_match else ""
    return True, name, desc


def extract_directives(text: str) -> List[str]:
    """Extract actionable bullet/numbered directives from SKILL.md."""
    directives = []
    for line in text.splitlines():
        line_clean = line.strip()
        if re.match(r"^[-*]\s+\*\*.*?\*\*", line_clean) or re.match(r"^\d+\.\s+\*\*.*?\*\*", line_clean):
            directives.append(line_clean)
        elif line_clean.startswith("- ") or line_clean.startswith("* ") or re.match(r"^\d+\.\s+", line_clean):
            if len(line_clean) > 20 and not line_clean.startswith("#"):
                directives.append(line_clean)
    return directives


def _find_broken_relative_links(text: str, skill_dir: Path) -> List[str]:
    """Return relative markdown link targets that do not exist under skill_dir."""
    broken = []
    # Strip fenced code blocks before scanning links
    stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    for match in re.finditer(r"\[[^\]]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", stripped):
        target = match.group(1).strip()
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target_path_str = target.split("#", 1)[0].split("?", 1)[0]
        if not target_path_str:
            continue
        resolved = (skill_dir / target_path_str).resolve()
        if not resolved.exists():
            broken.append(target_path_str)
    return sorted(set(broken))


def _find_nested_skills(skill_dir: Path) -> List[str]:
    """Detect accidental nested SKILL.md files that agents may load as duplicate skills."""
    nested = []
    if not skill_dir.is_dir():
        return nested
    for candidate in skill_dir.rglob("SKILL.md"):
        if candidate.parent == skill_dir:
            continue
        rel = candidate.relative_to(skill_dir)
        if any(part in {".git", "evals", "node_modules", "__pycache__"} for part in rel.parts):
            continue
        nested.append(str(rel))
    return sorted(nested)


def _letter_grade(score: float) -> str:
    if score >= 90.0:
        return "A"
    if score >= 80.0:
        return "B"
    if score >= 70.0:
        return "C"
    if score >= 60.0:
        return "D"
    return "F"


def lint_skill_detailed(skill_path: Path) -> Dict[str, Any]:
    """Run all static quality, structural, and security checks and compute a 0-100 score."""
    errors: List[str] = []
    warnings: List[str] = []
    score = 100.0

    if skill_path.is_dir():
        skill_path = skill_path / "SKILL.md"

    if not skill_path.exists():
        return {
            "passed": False,
            "score": 0.0,
            "grade": "F",
            "errors": [f"File not found: {skill_path}"],
            "warnings": [],
            "name": skill_path.parent.name,
            "description": "",
            "kb": 0.0,
            "est_tokens": 0,
            "directives_count": 0,
            "density": 0.0,
            "security_issues": [],
        }

    text = skill_path.read_text(encoding="utf-8", errors="ignore")
    char_len = len(text)
    kb = char_len / 1024.0
    est_tokens = int(char_len / 4)
    skill_dir = skill_path.parent

    # 1. Frontmatter & Trigger Check
    has_fm, name, desc = extract_frontmatter(text)
    if not has_fm:
        errors.append("Missing YAML frontmatter block (--- ... ---)")
        score -= 25.0
    else:
        if not name:
            errors.append("Missing required frontmatter field: 'name'")
            score -= 15.0
        if not desc:
            errors.append("Missing required frontmatter field: 'description'")
            score -= 15.0
        else:
            if len(desc) < 25:
                warnings.append(f"Description is very brief ({len(desc)} chars). State a concrete trigger and objective.")
                score -= 8.0
            desc_lower = desc.lower()
            if not any(phrase in desc_lower for phrase in TRIGGER_PHRASES):
                warnings.append("Description lacks explicit 'when to use' trigger phrasing (helps agent discoverability).")
                score -= 5.0

    # 2. Extract Directives & Calculate Density
    directives = extract_directives(text)
    density = len(directives) / kb if kb > 0 else 0.0
    if kb > 4.0 and density < DENSITY_THRESHOLD:
        errors.append(
            f"Low directive density: {density:.2f}/KB (minimum required: {DENSITY_THRESHOLD}/KB). "
            "File is conversational fluff/slop."
        )
        score -= 20.0

    # 3. Budget Check
    if kb > MAX_KB_WARNING:
        warnings.append(f"Skill exceeds budget ({kb:.1f} KB > {MAX_KB_WARNING:.1f} KB). Consider trimming or storing in cold warehouse.")
        score -= 5.0

    # 4. Security Scan
    security_issues: List[str] = []
    for pat, desc_danger in DANGER_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            errors.append(f"Security hazard detected: {desc_danger}")
            security_issues.append(desc_danger)
            score -= 30.0

    # 5. Structural Hygiene
    nested = _find_nested_skills(skill_dir)
    if nested:
        warnings.append(f"Nested SKILL.md detected ({', '.join(nested)}); some agents load nested skills separately.")
        score -= 5.0

    broken_links = _find_broken_relative_links(text, skill_dir)
    if broken_links:
        warnings.append(f"Broken relative link(s) in SKILL.md: {', '.join(broken_links)}")
        score -= min(12.0, 3.0 * len(broken_links))

    score = max(0.0, min(100.0, round(score, 1)))
    passed = len(errors) == 0

    return {
        "passed": passed,
        "score": score,
        "grade": _letter_grade(score),
        "errors": errors,
        "warnings": warnings,
        "name": name or skill_dir.name,
        "description": desc,
        "kb": round(kb, 1),
        "est_tokens": est_tokens,
        "directives_count": len(directives),
        "density": round(density, 2),
        "security_issues": security_issues,
    }


def lint_skill(skill_path: Path) -> Tuple[bool, List[str], List[str]]:
    """Backward-compatible wrapper returning (passed, errors, warnings)."""
    result = lint_skill_detailed(skill_path)
    return result["passed"], result["errors"], result["warnings"]


def run_optional_deep_scanners(skill_dir: Path) -> List[str]:
    """Run optional external scanners (bandit, gitleaks) if installed on PATH."""
    notes: List[str] = []
    if shutil.which("bandit") and any(skill_dir.rglob("*.py")):
        proc = subprocess.run(
            ["bandit", "-r", str(skill_dir), "-q", "-f", "txt"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0 and proc.stdout.strip():
            notes.append(f"[bandit] Findings detected:\n{proc.stdout.strip()[:800]}")
        else:
            notes.append("[bandit] Clean (0 issues).")

    if shutil.which("gitleaks"):
        proc = subprocess.run(
            ["gitleaks", "detect", "--no-git", "--source", str(skill_dir), "--no-banner"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            notes.append(f"[gitleaks] Potential secrets found (exit {proc.returncode}).")
        else:
            notes.append("[gitleaks] Clean (0 leaks).")

    if not notes:
        notes.append("No optional scanners (bandit/gitleaks) found on PATH or no applicable files.")
    return notes


def main():
    parser = argparse.ArgumentParser(description="No-Vibe Skill Linter & Pre-Activation Quality Gate")
    parser.add_argument("target", help="Path to SKILL.md or skill directory")
    parser.add_argument("--deep", action="store_true", help="Also run optional external scanners (bandit, gitleaks) if installed")
    args = parser.parse_args()

    target = Path(args.target)
    if target.is_dir():
        target = target / "SKILL.md"

    result = lint_skill_detailed(target)
    passed = result["passed"]
    errors = result["errors"]
    warnings = result["warnings"]

    status_str = "\033[1;32m[PASS]\033[0m" if passed else "\033[1;31m[FAIL]\033[0m"
    print(f"\n{status_str} Linter Verdict for: {result['name']}  |  Quality Score: {result['score']}/100 ({result['grade']})")
    print(f"       Size: {result['kb']} KB (~{result['est_tokens']} tokens)  |  Directives: {result['directives_count']} ({result['density']}/KB)")

    if warnings:
        print("\n⚠️  Warnings:")
        for w in warnings:
            print(f"   - {w}")

    if errors:
        print("\n❌ Errors:")
        for e in errors:
            print(f"   - {e}")

    if args.deep and target.exists():
        print("\n🔎 Deep Scan (Optional External Scanners):")
        for note in run_optional_deep_scanners(target.parent):
            print(f"   - {note}")

    if not passed:
        print("\nGate: Rejected. Do not promote to active runtime.\n")
        sys.exit(1)
    else:
        print("\nGate: Approved for active runtime.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
