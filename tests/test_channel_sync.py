import unittest

from app.services.channel_sync import ChannelSlot, channel_match_key, pair_channels


class ChannelPairingTests(unittest.TestCase):
    def test_same_name_in_the_same_category_is_a_pair(self) -> None:
        source = [ChannelSlot(1, "signals", "Alerts")]
        destination = [ChannelSlot(2, "signals", "Alerts")]
        pairs, missing_source, missing_destination = pair_channels(source, destination)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].destination.channel_id, 2)
        self.assertEqual(missing_source, [])
        self.assertEqual(missing_destination, [])

    def test_same_name_in_different_categories_is_not_a_pair(self) -> None:
        source = [ChannelSlot(1, "signals", "Alerts")]
        destination = [ChannelSlot(2, "signals", "Archive")]
        pairs, missing_source, missing_destination = pair_channels(source, destination)
        self.assertEqual(pairs, [])
        self.assertEqual(len(missing_source), 1)
        self.assertEqual(len(missing_destination), 1)

    def test_names_match_without_case_or_extra_space(self) -> None:
        self.assertEqual(channel_match_key(" Alerts ", " Signals "), channel_match_key("alerts", "signals"))

    def test_channels_only_on_one_server_are_left_out(self) -> None:
        source = [ChannelSlot(1, "kept", None), ChannelSlot(2, "source-only", None)]
        destination = [ChannelSlot(3, "kept", None), ChannelSlot(4, "destination-only", None)]
        pairs, missing_source, missing_destination = pair_channels(source, destination)
        self.assertEqual([pair.source.channel_id for pair in pairs], [1])
        self.assertEqual([slot.channel_name for slot in missing_source], ["source-only"])
        self.assertEqual([slot.channel_name for slot in missing_destination], ["destination-only"])


if __name__ == "__main__":
    unittest.main()
