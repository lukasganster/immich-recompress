import unittest

from backend.config import parse_duration


class ParseDurationTest(unittest.TestCase):
    def test_immich_milliseconds(self):
        self.assertAlmostEqual(parse_duration(4086), 4.086)
        self.assertAlmostEqual(parse_duration("109886"), 109.886)

    def test_immich_clock_duration(self):
        self.assertAlmostEqual(parse_duration("0:00:30.31200"), 30.312)
        self.assertAlmostEqual(parse_duration("1:02:03.5"), 3723.5)

if __name__ == "__main__":
    unittest.main()
