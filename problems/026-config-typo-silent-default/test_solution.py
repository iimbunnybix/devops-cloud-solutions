import unittest

from solution import ConfigError, load_config, naive_load


def base_environment(**overrides):
    environ = {"PATH": "/usr/bin", "HOME": "/home/app", "CHECKOUT_WORKERS": "8"}
    environ.update(overrides)
    return environ


def errors_for(environ):
    try:
        load_config(environ)
    except ConfigError as error:
        return error.errors
    return []


class ReproduceProblem(unittest.TestCase):
    def test_misspelled_limit_is_ignored_and_default_used(self):
        environ = base_environment(CHECKOUT_MAX_DB_CONNECTION="20")
        self.assertEqual(naive_load(environ)["CHECKOUT_MAX_DB_CONNECTIONS"], 200)


class FixRejectsUnknownSettings(unittest.TestCase):
    def test_misspelled_setting_fails_with_suggestion(self):
        errors = errors_for(base_environment(CHECKOUT_MAX_DB_CONNECTION="20"))
        self.assertEqual(errors, ["unknown setting CHECKOUT_MAX_DB_CONNECTION "
                                  "(did you mean CHECKOUT_MAX_DB_CONNECTIONS?)"])

    def test_unrelated_unknown_setting_fails_without_suggestion(self):
        errors = errors_for(base_environment(CHECKOUT_COLOR="blue"))
        self.assertEqual(errors, ["unknown setting CHECKOUT_COLOR"])

    def test_variables_without_the_app_prefix_are_ignored(self):
        self.assertEqual(errors_for(base_environment(PAYMENTS_MAX_DB_CONNECTION="5")), [])


class FixValidatesValues(unittest.TestCase):
    def test_correct_spelling_is_applied(self):
        config = load_config(base_environment(CHECKOUT_MAX_DB_CONNECTIONS="20"))
        self.assertEqual(config["CHECKOUT_MAX_DB_CONNECTIONS"], 20)

    def test_defaults_fill_in_missing_optional_settings(self):
        self.assertEqual(load_config(base_environment())["CHECKOUT_REQUEST_TIMEOUT_MS"], 3000)

    def test_missing_required_setting_fails(self):
        environ = base_environment()
        del environ["CHECKOUT_WORKERS"]
        self.assertEqual(errors_for(environ), ["CHECKOUT_WORKERS is required"])

    def test_non_numeric_value_fails(self):
        errors = errors_for(base_environment(CHECKOUT_REQUEST_TIMEOUT_MS="3s"))
        self.assertEqual(errors, ["CHECKOUT_REQUEST_TIMEOUT_MS='3s' is not a whole number"])

    def test_out_of_range_value_fails(self):
        errors = errors_for(base_environment(CHECKOUT_MAX_DB_CONNECTIONS="0"))
        self.assertEqual(errors, ["CHECKOUT_MAX_DB_CONNECTIONS=0 is outside 1..500"])

    def test_all_problems_are_reported_together(self):
        environ = base_environment(CHECKOUT_MAX_DB_CONNECTION="20", CHECKOUT_WORKERS="999")
        self.assertEqual(len(errors_for(environ)), 2)


if __name__ == "__main__":
    unittest.main()
