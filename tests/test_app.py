import unittest

from orbital_wifi.app import SavedProfile, WifiNetwork, parse_nmcli_fields, scan_networks


class ParseNmcliFieldsTest(unittest.TestCase):
    def test_parses_escaped_colons(self) -> None:
        line = r"*:Alpha:64\:9D\:F3\:23\:01\:08:96:WPA2:▂▄▆█"
        self.assertEqual(
            parse_nmcli_fields(line),
            ["*", "Alpha", "64:9D:F3:23:01:08", "96", "WPA2", "▂▄▆█"],
        )

    def test_preserves_blank_fields(self) -> None:
        self.assertEqual(parse_nmcli_fields(" :Cafe::78::▂___"), [" ", "Cafe", "", "78", "", "▂___"])


class DataModelTest(unittest.TestCase):
    def test_hidden_network_display_name(self) -> None:
        network = WifiNetwork(active=False, ssid="", bssid="aa", signal=10, security="", bars="____")
        self.assertEqual(network.display_name, "<hidden network>")
        self.assertEqual(network.security_label, "Open")

    def test_saved_profile_active_property(self) -> None:
        self.assertTrue(SavedProfile(name="Alpha", uuid="123", device="wlan0").active)
        self.assertFalse(SavedProfile(name="Alpha", uuid="123", device="").active)


if __name__ == "__main__":
    unittest.main()
