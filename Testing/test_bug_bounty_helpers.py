import unittest
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "server"))

from bug_bounty import (
    normalize_severity,
    get_reward_for_severity,
    validate_bug_report_payload,
    build_dedupe_key,
)


class BugBountyHelperTests(unittest.TestCase):
    def test_normalize_severity_defaults_to_medium(self):
        self.assertEqual(normalize_severity(""), "medium")
        self.assertEqual(normalize_severity("UNKNOWN"), "medium")

    def test_reward_mapping(self):
        self.assertEqual(get_reward_for_severity("low")["resume_bonus"], 1)
        self.assertEqual(get_reward_for_severity("critical")["job_apply_bonus"], 5)

    def test_bug_report_payload_validation(self):
        valid_payload = {
            "title": "Auto apply crashes on invalid URL",
            "summary": "This is a reproducible issue that blocks application submission.",
            "steps_to_reproduce": "1. Open app. 2. Paste malformed URL. 3. Click Apply. 4. Observe crash.",
            "expected_behavior": "Input should be validated with a clear error message.",
            "actual_behavior": "The app crashes with no user-facing guidance.",
            "environment": "Windows 11, CLI v0.2.41, Chrome 134",
        }
        is_valid, error = validate_bug_report_payload(valid_payload)
        self.assertTrue(is_valid)
        self.assertEqual(error, "")

    def test_bug_report_payload_validation_missing_required_field(self):
        invalid_payload = {
            "title": "short",
            "summary": "",
            "steps_to_reproduce": "",
            "expected_behavior": "",
            "actual_behavior": "",
            "environment": "",
        }
        is_valid, error = validate_bug_report_payload(invalid_payload)
        self.assertFalse(is_valid)
        self.assertTrue(error)

    def test_dedupe_key_is_stable(self):
        key1 = build_dedupe_key("u1", "Title", "step 1  step2", "actual behavior")
        key2 = build_dedupe_key("u1", "title", "step 1 step2", "actual behavior")
        self.assertEqual(key1, key2)


if __name__ == "__main__":
    unittest.main()
