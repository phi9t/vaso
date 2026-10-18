"""Package metadata lives in pyproject.toml; this only keeps tests out of the wheel.

Tests sit beside the code they cover (x_test.py, conftest.py). setuptools would
otherwise ship every .py file in a package, so the build skips those modules.
"""

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPyWithoutTests(build_py):
    def find_package_modules(self, package, package_dir):
        return [
            module
            for module in super().find_package_modules(package, package_dir)
            if not (module[1].endswith("_test") or module[1].startswith("test_") or module[1] == "conftest")
        ]


setup(cmdclass={"build_py": BuildPyWithoutTests})
