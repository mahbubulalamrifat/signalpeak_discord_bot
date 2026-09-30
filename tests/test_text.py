import unittest

from app.services.ids import parse_snowflake
from app.services.text import split_discord_content


class TextTests(unittest.TestCase):
    def test_blank_text_is_not_sent(self) -> None:
        self.assertEqual(split_discord_content("   \n"), [])

    def test_short_text_stays_one_message(self) -> None:
        self.assertEqual(split_discord_content(" signal "), ["signal"])

    def test_long_text_splits_on_a_newline(self) -> None:
        first = "a" * 20
        second = "b" * 20
        parts = split_discord_content(f"{first}\n{second}", limit=25)
        self.assertEqual(parts, [first, second])

    def test_long_text_without_newline_splits_at_the_limit(self) -> None:
        parts = split_discord_content("x" * 10, limit=4)
        self.assertEqual(parts, ["xxxx", "xxxx", "xx"])


class IdTests(unittest.TestCase):
    def test_numeric_id(self) -> None:
        self.assertEqual(parse_snowflake("123456789012345678"), 123456789012345678)

    def test_rejects_placeholder(self) -> None:
        with self.assertRaises(ValueError):
            parse_snowflake("REPLACE_WITH_SOURCE_CHANNEL_ID")


if __name__ == "__main__":
    unittest.main()
