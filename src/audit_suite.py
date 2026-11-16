#!/usr/bin/env python3
"""
audit_suite.py - Full Skill Suite Audit & Overlap Detection
Implements Makarevych Eval Criteria + Tier 2-style lexical overlap detection
across the active Antigravity skills directory.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lint_skill import lint_skill_detailed

STOPWORDS: Set[str] = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "if",
    "in", "into", "is", "it", "of", "on", "or", "that", "the", "this",
    "to", "use", "used", "using", "when", "with", "you", "your",
}


def _tokenize_description(text: str) -> Set[str]:
    words = re.findall(r"[a-z0-9]{3,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


def find_description_overlaps(
    skill_records: List[Dict[str, Any]],
    threshold: float = 0.45,
) -> List[Dict[str, Any]]:
    """Detect pairs of skills whose descriptions have high Jaccard token overlap."""
    tokenized = []
    for rec in skill_records:
        desc = rec.get("description", "")
        if not desc:
            continue
        tokens = _tokenize_description(desc)
        if len(tokens) >= 4:
            tokenized.append((rec["name"], tokens))

    overlaps: List[Dict[str, Any]] = []
    for i in range(len(tokenized)):
        name_a, tok_a = tokenized[i]
        for j in range(i + 1, len(tokenized)):
            name_b, tok_b = tokenized[j]
            union = tok_a | tok_b
            if not union:
                continue
            jaccard = len(tok_a & tok_b) / len(union)
            if jaccard >= threshold:
                overlaps.append({
                    "skill_a": name_a,
                    "skill_b": name_b,
                    "similarity": round(jaccard, 3),
                    "shared_terms": sorted(tok_a & tok_b)[:10],
                })

    overlaps.sort(key=lambda x: x["similarity"], reverse=True)
    return overlaps


def audit_directory(skills_dir: Path, overlap_threshold: float = 0.45) -> Dict[str, Any]:
    """Audit all skill folders in skills_dir and detect description overlaps."""
    if not skills_dir.exists():
        return {"total": 0, "avg_score": 0.0, "summary": {}, "overlaps": [], "skills": []}

    skills = sorted([d for d in skills_dir.iterdir() if d.is_dir() or d.is_symlink()])
    results: List[Dict[str, Any]] = []

    for s in skills:
        skill_file = s / "SKILL.md"
        if not skill_file.exists():
            results.append({
                "name": s.name,
                "status": "FAIL_MISSING",
                "score": 0.0,
                "grade": "F",
                "kb": 0.0,
                "est_tokens": 0,
                "directives_count": 0,
                "density": 0.0,
                "sec_issues": ["Missing SKILL.md"],
                "description": "",
            })
            continue

        detail = lint_skill_detailed(skill_file)
        kb = detail["kb"]
        directives_count = detail["directives_count"]

        if any("Missing YAML frontmatter" in e or "Missing required frontmatter" in e for e in detail["errors"]):
            rating = "INVALID_SPEC"
        elif detail["security_issues"]:
            rating = "SECURITY_WARN"
        elif kb > 100:
            rating = "SUPER_HEAVY"
        elif kb > 12 and directives_count < 5:
            rating = "VIBE_SLOP"
        elif not detail["passed"]:
            rating = "FAIL_LINT"
        else:
            rating = "PASS_OPTIMAL"

        results.append({
            "name": s.name,
            "status": rating,
            "score": detail["score"],
            "grade": detail["grade"],
            "kb": kb,
            "est_tokens": detail["est_tokens"],
            "lines": len(skill_file.read_text(encoding="utf-8", errors="ignore").splitlines()),
            "directives_count": directives_count,
            "density": detail["density"],
            "sec_issues": detail["security_issues"],
            "warnings_count": len(detail["warnings"]),
            "description": detail["description"],
        })

    status_counts: Dict[str, int] = {}
    for r in results:
        st = r["status"]
        status_counts[st] = status_counts.get(st, 0) + 1

    avg_score = round(sum(r["score"] for r in results) / len(results), 1) if results else 0.0
    overlaps = find_description_overlaps(results, threshold=overlap_threshold)

    # Omit raw description from per-skill output to keep JSON compact
    for r in results:
        r.pop("description", None)

    return {
        "total": len(results),
        "avg_score": avg_score,
        "summary": status_counts,
        "overlaps": overlaps,
        "skills": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Audit an active skills directory")
    parser.add_argument(
        "--dir",
        default=os.path.expanduser("~/.gemini/config/skills"),
        help="Path to skills directory (default: ~/.gemini/config/skills)",
    )
    parser.add_argument(
        "--overlap-threshold",
        type=float,
        default=0.45,
        help="Jaccard similarity threshold for flagging overlapping skill descriptions (default: 0.45)",
    )
    args = parser.parse_args()

    report = audit_directory(Path(args.dir), overlap_threshold=args.overlap_threshold)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
