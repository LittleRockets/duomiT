import unittest

from duomit.desktop_design import DesktopTokens, status_palette


class DesktopDesignTests(unittest.TestCase):
    def test_semantic_statuses_resolve_to_distinct_token_pairs(self):
        self.assertEqual(status_palette("valid"), (DesktopTokens.success, DesktopTokens.success_soft))
        self.assertEqual(status_palette("invalid"), (DesktopTokens.danger, DesktopTokens.danger_soft))
        self.assertEqual(status_palette("attention"), (DesktopTokens.attention, DesktopTokens.attention_soft))

    def test_design_tokens_are_centralized_and_not_dark_dashboard_values(self):
        self.assertEqual(DesktopTokens.canvas, "#F7F8F6")
        self.assertEqual(DesktopTokens.primary, "#0F766E")
        self.assertEqual(DesktopTokens.surface, "#FFFFFF")


if __name__ == "__main__":
    unittest.main()
