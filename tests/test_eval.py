import sys
import unittest
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lint_skill import lint_skill
from eval_skill import generate_length_control, grade_and_decide

class TestSkillEvals(unittest.TestCase):
    def test_length_control_exact_length(self):
        target = 250
        control = generate_length_control(target)
        self.assertEqual(len(control), target)

    def test_decision_matrix_ship_and_pin(self):
        verdict, _ = grade_and_decide(arm_a_score=0.6, arm_b_score=0.9, arm_c_score=0.65, adherence_rate=0.85)
        self.assertEqual(verdict, "SHIP & PIN")

    def test_decision_matrix_drop_idea(self):
        verdict, _ = grade_and_decide(arm_a_score=0.6, arm_b_score=0.68, arm_c_score=0.70, adherence_rate=0.90)
        self.assertEqual(verdict, "DROP IDEA (FOLKLORE)")

    def test_decision_matrix_low_adherence(self):
        verdict, _ = grade_and_decide(arm_a_score=0.6, arm_b_score=0.9, arm_c_score=0.65, adherence_rate=0.50)
        self.assertEqual(verdict, "REWRITE TEXT")

    def test_lint_missing_frontmatter(self):
        tmp_file = Path("/tmp/test_bad_skill.md")
        tmp_file.write_text("No frontmatter at all here", encoding="utf-8")
        passed, errors, _ = lint_skill(tmp_file)
        self.assertFalse(passed)
        self.assertTrue(any("Missing YAML frontmatter" in e for e in errors))

if __name__ == "__main__":
    unittest.main()
