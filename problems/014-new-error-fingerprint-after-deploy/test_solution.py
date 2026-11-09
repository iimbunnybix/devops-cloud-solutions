import random
import string
import unittest
import uuid

from solution import ErrorEvent, compare_windows, error_rate, fingerprint, template

KNOWN = [
    ("checkout", "TimeoutError", "payment gateway timeout after {ms}ms for order {oid}",
     "checkout/gateway.py:charge:88"),
    ("checkout", "ValidationError", "invalid postcode '{pc}' for user {email}",
     "checkout/address.py:validate:41"),
    ("catalog", "CacheMiss", "redis {ip}:6379 miss for key sku:{sku}", "catalog/cache.py:get:12"),
]
NEW = ("checkout", "KeyError", "coupon {code!r} has no 'discount_type' (order {oid})",
       "checkout/pricing.py:apply_coupon:203")


def render(tpl, rng):
    return tpl.format(ms=rng.randint(3000, 9000), oid=rng.randint(10**6, 10**7),
                      pc=f"{rng.randint(10000, 99999)}X", email=f"u{rng.randint(1, 10**6)}@example.com",
                      ip=f"10.0.{rng.randint(0, 255)}.{rng.randint(0, 255)}",
                      sku=rng.randint(1, 50_000), code=uuid.UUID(int=rng.getrandbits(128)).hex[:8].upper())


def window(rng, requests, known_rate=0.02, new_rate=0.0, line_shift=0):
    events = []
    for _ in range(requests):
        r = rng.random()
        if r < known_rate:
            svc, exc, tpl, frame = rng.choice(KNOWN)
            path, func, line = frame.rsplit(":", 2)
            events.append(ErrorEvent(svc, exc, render(tpl, rng),
                                     f"{path}:{func}:{int(line) + line_shift}"))
        elif r < known_rate + new_rate:
            svc, exc, tpl, frame = NEW
            events.append(ErrorEvent(svc, exc, render(tpl, rng), frame))
    return events


class ReproduceHiddenError(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(42)
        cls.base = window(rng, 100_000)
        cls.after = window(rng, 100_000, new_rate=0.002, line_shift=7)

    def test_total_error_rate_barely_moves(self):
        before, after = error_rate(self.base, 100_000), error_rate(self.after, 100_000)
        self.assertLess(after / before, 1.15)            # a 2x alert never fires
        self.assertEqual(round(after / before, 2), 1.08)

    def test_grouping_by_raw_message_is_useless(self):
        groups = {e.message for e in self.base}
        self.assertGreater(len(groups), 0.95 * len(self.base))

    def test_fingerprints_collapse_to_kinds(self):
        self.assertEqual(len({fingerprint(e) for e in self.base}), 3)

    def test_new_error_reported_and_known_ones_not(self):
        findings = compare_windows(self.base, 100_000, self.after, 100_000)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.kind, "new")
        self.assertIn("KeyError", f.example)
        self.assertIn("coupon <str>", f.example)
        self.assertEqual(f.count, 201)


class Surges(unittest.TestCase):
    def test_existing_error_surge_detected_rate_normalised(self):
        rng = random.Random(1)
        base = window(rng, 50_000, known_rate=0.01)
        after = window(rng, 50_000, known_rate=0.01)
        timeout = KNOWN[0]
        after += [ErrorEvent(timeout[0], timeout[1], render(timeout[2], rng), timeout[3])
                  for _ in range(600)]
        findings = compare_windows(base, 50_000, after, 50_000)
        self.assertEqual([f.kind for f in findings], ["surging"])
        self.assertIn("TimeoutError", findings[0].example)

    def test_traffic_growth_is_not_a_surge(self):
        rng = random.Random(2)
        base = window(rng, 20_000)
        after = window(rng, 60_000)                       # 3x traffic, same rate
        self.assertEqual(compare_windows(base, 20_000, after, 60_000), [])

    def test_min_count_suppresses_one_offs(self):
        rng = random.Random(3)
        base = window(rng, 10_000)
        after = window(rng, 10_000) + [ErrorEvent("x", "OSError", "disk hiccup", "")] * 4
        self.assertEqual(compare_windows(base, 10_000, after, 10_000), [])

    def test_bad_request_counts(self):
        with self.assertRaises(ValueError):
            compare_windows([], 0, [], 10)


def fake_charge_id(rng):
    """Stripe-style id built at runtime: 'ch_' + 14 letters and digits."""
    alphabet = string.ascii_letters + string.digits
    body = "".join(rng.choice(alphabet) for _ in range(13))
    return "ch_" + str(rng.randint(0, 9)) + body       # always has a digit


def fake_tenant_name(rng):
    """A letters-only variable part that no mask can recognise."""
    return "".join(rng.choice(string.ascii_lowercase) for _ in range(10))


class IdsMixingLettersAndDigits(unittest.TestCase):
    """Issue #21: ids like ch_3MqLiJ2eZvKYlo2C gave every event its own fingerprint."""

    @classmethod
    def setUpClass(cls):
        rng = random.Random(42)
        cls.base = window(rng, 100_000)
        cls.after = window(rng, 100_000, new_rate=0.002, line_shift=7)
        cls.rng = random.Random(7)

    def card_errors(self, count, message):
        return [ErrorEvent("checkout", "CardError", message(), "checkout/gateway.py:charge:90")
                for _ in range(count)]

    def test_stripe_style_charge_id_is_masked(self):
        self.assertEqual(template("card declined for charge " + fake_charge_id(self.rng)),
                         "card declined for charge <id>")

    def test_short_hex_container_id_is_masked(self):
        self.assertEqual(template("container 3f4e1a9c2b7d exited"), "container <id> exited")

    def test_number_with_unit_and_short_words_are_not_ids(self):
        self.assertEqual(template("utf8 decode took 9000ms"), "utf8 decode took <num>")

    def test_new_error_with_charge_ids_is_reported(self):
        new = self.card_errors(300, lambda: "card declined for charge " + fake_charge_id(self.rng))
        findings = compare_windows(self.base, 100_000, self.after + new, 100_000)
        card = [f for f in findings if "CardError" in f.example]
        self.assertEqual(len(card), 1)
        self.assertEqual((card[0].kind, card[0].count, card[0].grouped), ("new", 300, False))

    def test_unmaskable_variable_part_is_still_reported_by_pooling(self):
        new = self.card_errors(300, lambda: "card declined for tenant " + fake_tenant_name(self.rng))
        findings = compare_windows(self.base, 100_000, self.after + new, 100_000)
        card = [f for f in findings if "CardError" in f.example]
        self.assertEqual(len(card), 1)
        self.assertEqual((card[0].kind, card[0].count, card[0].grouped), ("new", 300, True))

    def test_unmasked_leftovers_of_a_reported_error_are_not_reported_twice(self):
        new = self.card_errors(300, lambda: "card declined for charge " + fake_charge_id(self.rng))
        new += self.card_errors(20, lambda: "card declined for charge ch_" + fake_tenant_name(self.rng))
        findings = compare_windows(self.base, 100_000, self.after + new, 100_000)
        card = [f for f in findings if "CardError" in f.example]
        self.assertEqual([(f.count, f.grouped) for f in card], [(300, False)])

    def test_pooled_known_rare_errors_at_steady_rate_are_not_reported(self):
        base = self.card_errors(40, lambda: "declined for tenant " + fake_tenant_name(self.rng))
        after = self.card_errors(40, lambda: "declined for tenant " + fake_tenant_name(self.rng))
        self.assertEqual(compare_windows(base, 10_000, after, 10_000), [])


class Templates(unittest.TestCase):
    def test_masks(self):
        u = str(uuid.UUID(int=12345))
        self.assertEqual(template(f"user {u} from 192.168.1.20:443 took 532ms"),
                         "user <uuid> from <ip> took <num>")
        self.assertEqual(template("mail a.b+c@example.co.uk failed"), "mail <email> failed")
        self.assertEqual(template("GET https://api.example.com/x?id=1 -> 500"),
                         "GET <url> -> <num>")
        self.assertEqual(template('bad value "abc" at 0x7ffe12'), "bad value <str> at <hex>")
        self.assertEqual(template("  many   spaces "), "many spaces")

    def test_line_number_change_keeps_fingerprint(self):
        a = ErrorEvent("s", "E", "boom 1", "a.py:f:10")
        b = ErrorEvent("s", "E", "boom 2", "a.py:f:99")
        self.assertEqual(fingerprint(a), fingerprint(b))

    def test_different_type_or_frame_differs(self):
        a = ErrorEvent("s", "E", "boom", "a.py:f:1")
        self.assertNotEqual(fingerprint(a), fingerprint(ErrorEvent("s", "F", "boom", "a.py:f:1")))
        self.assertNotEqual(fingerprint(a), fingerprint(ErrorEvent("s", "E", "boom", "a.py:g:1")))


if __name__ == "__main__":
    unittest.main()
