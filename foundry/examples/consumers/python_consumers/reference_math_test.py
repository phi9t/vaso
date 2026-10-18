#!/usr/bin/env python3
"""Consumer-side tests for the pure-Python SE3 projection reference."""

from __future__ import annotations

import unittest

from reference import project_se3_pinhole_python


class ReferenceMathTest(unittest.TestCase):
    def test_project_se3_pinhole_python_matches_hand_computed_fixture(self) -> None:
        points = [
            [
                [1.0, 2.0, 4.0],
                [-2.0, 1.0, 2.0],
                [5.0, 6.0, -2.0],
                [7.0, 8.0, 0.0],
            ],
            [
                [2.0, 4.0, 1.0],
                [0.0, 0.0, -4.0],
            ],
        ]
        poses = [
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            [
                [1.0, 0.0, 0.0, 1.0],
                [0.0, 1.0, 0.0, -2.0],
                [0.0, 0.0, 1.0, 3.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
        ]
        intrinsics = [
            [
                [100.0, 0.0, 10.0],
                [0.0, 200.0, 20.0],
                [0.0, 0.0, 1.0],
            ],
            [
                [10.0, 0.0, 1.0],
                [0.0, 20.0, 2.0],
                [0.0, 0.0, 1.0],
            ],
        ]

        pixels, depth, valid = project_se3_pinhole_python(points, poses, intrinsics)

        self.assertEqual(
            pixels,
            [
                [[35.0, 120.0], [-90.0, 120.0], [0.0, 0.0], [0.0, 0.0]],
                [[8.5, 12.0], [0.0, 0.0]],
            ],
        )
        self.assertEqual(depth, [[4.0, 2.0, -2.0, 0.0], [4.0, -1.0]])
        self.assertEqual(valid, [[True, True, False, False], [True, False]])


if __name__ == "__main__":
    unittest.main()
