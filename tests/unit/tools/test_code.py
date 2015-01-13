import unittest
import mock

import os
import uuid

import seisflows.tools.code as tools


class TestToolsCode(unittest.TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_divides(self):
        divisible = [(1, 1), (2, 1), (100, 1), (50, 2), (12, 3)]
        not_divisible = [(1, 0), (5, 0), (5, 4), (13, 2), (3, 6)]
        for (n, m) in divisible:
            self.assertTrue(tools.divides(n, m))
        for (n, m) in not_divisible:
            self.assertFalse(tools.divides(n, m))

    def test_exists(self):
        existent = os.listdir(os.curdir)
        non_existent = [str(uuid.uuid4().get_hex().upper()[0:6])
                        for i in range(10)]
        not_names = [None, 0, 1, False, True]
        for name in existent:
            self.assertTrue(tools.exists(name))
        for name in non_existent:
            self.assertFalse(tools.exists(name))
        for name in not_names:
            self.assertFalse(tools.exists(name))


if __name__ == '__main__':
    unittest.main()