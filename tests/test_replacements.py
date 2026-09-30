import unittest

from app.services.replacements import ReplacementRule, apply_replacements


class ReplacementTests(unittest.TestCase):
    def test_blank_value_removes_the_match(self) -> None:
        result = apply_replacements(
            "hello unwanted text world",
            [ReplacementRule(search_key="unwanted text", replace_value="")],
        )
        self.assertEqual(result.text, "hello  world")
        self.assertEqual(result.changes[0].matches, 1)

    def test_value_replaces_the_match(self) -> None:
        result = apply_replacements(
            "VIP SIGNAL",
            [ReplacementRule(search_key="VIP", replace_value="PRO")],
        )
        self.assertEqual(result.text, "PRO SIGNAL")

    def test_rules_run_in_order(self) -> None:
        result = apply_replacements(
            "aaa",
            [
                ReplacementRule(search_key="a", replace_value="b"),
                ReplacementRule(search_key="b", replace_value="c"),
            ],
        )
        self.assertEqual(result.text, "ccc")

    def test_search_key_is_exact_text(self) -> None:
        result = apply_replacements(
            "a.b axb",
            [ReplacementRule(search_key="a.b", replace_value="Z")],
        )
        self.assertEqual(result.text, "Z axb")


if __name__ == "__main__":
    unittest.main()
