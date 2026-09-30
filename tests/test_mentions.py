import unittest

from app.services.replacements import rewrite_channel_mentions


class ChannelMentionTests(unittest.TestCase):
    def test_source_channel_mention_becomes_the_destination_channel(self) -> None:
        text = 'Replied to <#111>'
        rewritten = rewrite_channel_mentions(text, {111: 222})
        self.assertEqual(rewritten, 'Replied to <#222>')

    def test_unknown_channel_mention_is_left_unchanged(self) -> None:
        text = "see <#999>"
        self.assertEqual(rewrite_channel_mentions(text, {111: 222}), text)

    def test_several_mentions_are_rewritten(self) -> None:
        text = "<#111> and <#333>"
        self.assertEqual(rewrite_channel_mentions(text, {111: 222, 333: 444}), "<#222> and <#444>")


if __name__ == "__main__":
    unittest.main()
