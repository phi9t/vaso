import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


def _load_provenance_module():
    path = Path(__file__).with_name("spack_recipe_provenance.py")
    spec = importlib.util.spec_from_file_location("spack_recipe_provenance", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


provenance = _load_provenance_module()


def temporary_directory():
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or str(Path.cwd())
    return tempfile.TemporaryDirectory(dir=base)


class SpackRecipeProvenanceTest(unittest.TestCase):
    def test_scans_recipe_root_and_marks_requested_absence(self):
        with temporary_directory() as td:
            root = Path(td)
            py_torch = root / "py_torch"
            py_torch.mkdir()
            (py_torch / "package.py").write_text(
                """
from spack_repo.builtin.build_systems.python import PythonPackage
from spack_repo.builtin.build_systems.cuda import CudaPackage

from spack.package import *

class PyTorch(PythonPackage, CudaPackage):
    homepage = "https://pytorch.org/"
    git = "https://github.com/pytorch/pytorch.git"
    version("2.12.0", tag="v2.12.0", commit="abc")
    variant("cuda", default=True, description="Use CUDA")
    depends_on("cuda@12.1:", when="@2.9:")
    depends_on("py-triton", type=("build", "run"))
""",
                encoding="utf-8",
            )
            py_triton = root / "py_triton"
            py_triton.mkdir()
            (py_triton / "package.py").write_text(
                """
from spack_repo.builtin.build_systems.python import PythonPackage
from spack.package import *
class PyTriton(PythonPackage):
    homepage = "https://github.com/triton-lang/triton"
    version("3.4.0", sha256="abc")
""",
                encoding="utf-8",
            )

            report = provenance.inspect_recipe_root(
                root,
                present=["py-torch", "py-triton"],
                absent=["vendor-libtorch", "libtorch"],
                search_terms=["torch", "triton"],
            )

        self.assertEqual(report["package_root"], str(root))
        self.assertTrue(report["packages"]["py-torch"]["present"])
        self.assertEqual(report["packages"]["py-torch"]["class"], "PyTorch")
        self.assertEqual(
            report["packages"]["py-torch"]["build_systems"],
            ["cuda", "python"],
        )
        self.assertIn("2.12.0", report["packages"]["py-torch"]["versions"])
        self.assertIn("cuda", report["packages"]["py-torch"]["variants"])
        self.assertIn("cuda@12.1:", report["packages"]["py-torch"]["dependencies"])
        self.assertFalse(report["packages"]["vendor-libtorch"]["present"])
        self.assertIn("py-torch", report["search"]["torch"])
        self.assertIn("py-triton", report["search"]["triton"])

    def test_cli_requires_expected_presence_and_absence(self):
        with temporary_directory() as td:
            root = Path(td)
            pkg = root / "py_jax"
            pkg.mkdir()
            (pkg / "package.py").write_text("class PyJax(PythonPackage):\n    pass\n")
            out = root / "report.json"

            rc = provenance.main(
                [
                    "--package-root",
                    str(root),
                    "--out",
                    str(out),
                    "--require-present",
                    "py-jax",
                    "--require-absent",
                    "vendor-libtorch",
                    "--query",
                    "jax",
                ]
            )

            self.assertEqual(rc, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(data["packages"]["py-jax"]["present"])
            self.assertFalse(data["packages"]["vendor-libtorch"]["present"])

    def test_overlay_root_can_supply_exact_python_protobuf_version(self):
        with temporary_directory() as td:
            root = Path(td)
            builtin = root / "builtin" / "packages"
            overlay = root / "overlay" / "packages"
            builtin_pkg = builtin / "py_protobuf"
            overlay_pkg = overlay / "py_protobuf"
            builtin_pkg.mkdir(parents=True)
            overlay_pkg.mkdir(parents=True)
            (builtin_pkg / "package.py").write_text(
                """
from spack_repo.builtin.build_systems.python import PythonPackage
from spack.package import *
class PyProtobuf(PythonPackage):
    version("4.21.9", sha256="builtin")
""",
                encoding="utf-8",
            )
            (overlay_pkg / "package.py").write_text(
                """
from spack_repo.builtin.build_systems.python import PythonPackage
from spack.package import *
class PyProtobuf(PythonPackage):
    version("4.21.12", sha256="overlay")
""",
                encoding="utf-8",
            )
            out = root / "report.json"

            rc = provenance.main(
                [
                    "--package-root",
                    str(overlay),
                    "--package-root",
                    str(builtin),
                    "--out",
                    str(out),
                    "--require-present",
                    "py-protobuf",
                    "--require-version",
                    "py-protobuf@4.21.12",
                    "--query",
                    "protobuf",
                ]
            )

            self.assertEqual(rc, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            pkg = data["packages"]["py-protobuf"]
            self.assertTrue(pkg["present"])
            self.assertIn("4.21.12", pkg["versions"])
            self.assertNotIn("4.21.9", pkg["versions"])
            self.assertEqual(pkg["recipe"], str(overlay_pkg / "package.py"))
            self.assertEqual(data["package_roots"], [str(overlay), str(builtin)])

    def test_overlay_subclass_keeps_inherited_recipe_metadata(self):
        with temporary_directory() as td:
            root = Path(td)
            builtin = root / "builtin" / "packages"
            overlay = root / "overlay" / "packages"
            builtin_pkg = builtin / "py_torch"
            overlay_pkg = overlay / "py_torch"
            builtin_pkg.mkdir(parents=True)
            overlay_pkg.mkdir(parents=True)
            (builtin_pkg / "package.py").write_text(
                """
from spack_repo.builtin.build_systems.python import PythonPackage
from spack_repo.builtin.build_systems.cuda import CudaPackage
from spack.package import *
class PyTorch(PythonPackage, CudaPackage):
    version("2.12.0", tag="v2.12.0", commit="builtin")
    variant("cuda", default=True, description="Use CUDA")
    depends_on("cuda@12.1:", when="@2.9:")
""",
                encoding="utf-8",
            )
            (overlay_pkg / "package.py").write_text(
                """
from spack_repo.builtin.packages.py_torch.package import PyTorch as BuiltinPyTorch
from spack.package import *
class PyTorch(BuiltinPyTorch):
    version("2.14.0", tag="v2.14.0", commit="overlay")
    depends_on("protobuf@21.12", when="@2.14.0")
""",
                encoding="utf-8",
            )

            report = provenance.inspect_recipe_roots(
                [overlay, builtin],
                present=["py-torch"],
                absent=[],
                search_terms=[],
            )

        pkg = report["packages"]["py-torch"]
        self.assertEqual(pkg["recipe"], str(overlay_pkg / "package.py"))
        self.assertEqual(
            pkg["recipes"],
            [str(overlay_pkg / "package.py"), str(builtin_pkg / "package.py")],
        )
        self.assertEqual(pkg["build_systems"], ["cuda", "python"])
        self.assertIn("2.14.0", pkg["versions"])
        self.assertIn("2.12.0", pkg["versions"])
        self.assertIn("cuda@12.1:", pkg["dependencies"])
        self.assertIn("protobuf@21.12", pkg["dependencies"])


if __name__ == "__main__":
    unittest.main()
