from django.test import SimpleTestCase
from django.utils.translation import override

from economy.templatetags.economy_tags import currency_unit, themed_currency_unit


class CurrencyUnitTests(SimpleTestCase):
    # The family edition shows AUD everywhere; the upstream themed units are
    # kept in themed_currency_unit and still tested here.
    def test_family_edition_uses_aud_for_every_theme(self):
        for language in ("en", "lt"):
            with override(language):
                self.assertEqual(currency_unit(1, "neutral"), "AUD")
                self.assertEqual(currency_unit(5, "magic_academy"), "AUD")

    def test_lithuanian_point_forms(self):
        expected = {
            0: "taškų", 1: "taškas", 5: "taškai", 10: "taškų",
            11: "taškų", 20: "taškų", 21: "taškas", 25: "taškai",
            100: "taškų", -1: "taškas", -5: "taškai",
        }
        with override("lt"):
            for value, unit in expected.items():
                with self.subTest(value=value):
                    self.assertEqual(themed_currency_unit(value, "neutral"), unit)

    def test_lithuanian_theme_currency_forms(self):
        with override("lt"):
            self.assertEqual(themed_currency_unit(1, "block_world"), "smaragdas")
            self.assertEqual(themed_currency_unit(5, "block_world"), "smaragdai")
            self.assertEqual(themed_currency_unit(0, "block_world"), "smaragdų")
            self.assertEqual(themed_currency_unit(1, "magic_academy"), "galeonas")
            self.assertEqual(themed_currency_unit(5, "magic_academy"), "galeonai")
            self.assertEqual(themed_currency_unit(0, "magic_academy"), "galeonų")

    def test_english_theme_currency_forms(self):
        with override("en"):
            self.assertEqual(themed_currency_unit(1, "neutral"), "point")
            self.assertEqual(themed_currency_unit(2, "neutral"), "points")
            self.assertEqual(themed_currency_unit(1, "block_world"), "emerald")
            self.assertEqual(themed_currency_unit(2, "block_world"), "emeralds")
            self.assertEqual(themed_currency_unit(1, "magic_academy"), "galleon")
            self.assertEqual(themed_currency_unit(2, "magic_academy"), "galleons")
