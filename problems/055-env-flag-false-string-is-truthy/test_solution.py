import unittest

from solution import FlagSpec, InvalidFlagError, load_flags, naive_flag, parse_bool, strict_flag

RECOMMENDATIONS = FlagSpec("RECOMMENDATIONS_ENABLED", default=True)
NEW_CHECKOUT = FlagSpec("NEW_CHECKOUT_ENABLED", default=False)


class ReproduceProblem(unittest.TestCase):
    def test_kill_switch_set_to_false_stays_on(self):
        environ = {"RECOMMENDATIONS_ENABLED": "false"}
        self.assertTrue(naive_flag(environ, RECOMMENDATIONS))

    def test_zero_and_off_also_read_as_on(self):
        for value in ["0", "off", "no", "False"]:
            with self.subTest(value=value):
                self.assertTrue(naive_flag({"RECOMMENDATIONS_ENABLED": value}, RECOMMENDATIONS))


class FixStrictParsing(unittest.TestCase):
    def test_false_turns_the_switch_off(self):
        self.assertFalse(strict_flag({"RECOMMENDATIONS_ENABLED": "false"}, RECOMMENDATIONS))

    def test_every_false_spelling_is_off(self):
        for value in ["0", "false", "no", "off", "FALSE", " Off "]:
            with self.subTest(value=value):
                self.assertFalse(parse_bool("FLAG", value))

    def test_every_true_spelling_is_on(self):
        for value in ["1", "true", "yes", "on", "TRUE", "\tYes\n"]:
            with self.subTest(value=value):
                self.assertTrue(parse_bool("FLAG", value))

    def test_missing_variable_uses_the_default(self):
        self.assertTrue(strict_flag({}, RECOMMENDATIONS))
        self.assertFalse(strict_flag({}, NEW_CHECKOUT))

    def test_unknown_value_is_rejected_with_the_variable_name(self):
        with self.assertRaises(InvalidFlagError) as raised:
            strict_flag({"RECOMMENDATIONS_ENABLED": "disabled"}, RECOMMENDATIONS)
        self.assertIn("RECOMMENDATIONS_ENABLED='disabled'", str(raised.exception))

    def test_empty_value_is_rejected_instead_of_guessed(self):
        with self.assertRaises(InvalidFlagError):
            strict_flag({"RECOMMENDATIONS_ENABLED": ""}, RECOMMENDATIONS)


class FixStartupValidation(unittest.TestCase):
    def test_valid_environment_loads_all_flags(self):
        environ = {"RECOMMENDATIONS_ENABLED": "off", "NEW_CHECKOUT_ENABLED": "1"}
        flags = load_flags(environ, [RECOMMENDATIONS, NEW_CHECKOUT])
        self.assertEqual(flags, {"RECOMMENDATIONS_ENABLED": False, "NEW_CHECKOUT_ENABLED": True})

    def test_all_bad_values_are_reported_in_one_error(self):
        environ = {"RECOMMENDATIONS_ENABLED": "nope", "NEW_CHECKOUT_ENABLED": "2"}
        with self.assertRaises(InvalidFlagError) as raised:
            load_flags(environ, [RECOMMENDATIONS, NEW_CHECKOUT])
        message = str(raised.exception)
        self.assertIn("RECOMMENDATIONS_ENABLED='nope'", message)
        self.assertIn("NEW_CHECKOUT_ENABLED='2'", message)


if __name__ == "__main__":
    unittest.main()
