"""Run isolated public tests without importing an installed N.E.K.O host."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for path in (ROOT, ROOT / "tests"):
    sys.path.insert(0, str(path))

if __name__ == "__main__":
    import importlib.abc
    import importlib.util
    import types

    class OfflineHostBoundary(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "plugin" or fullname.startswith("plugin."):
                raise ModuleNotFoundError("Host SDK intentionally excluded from offline tests")
            return None

    sys.meta_path.insert(0, OfflineHostBoundary())
    # Pytest may collect root __init__.py under a non-identifier checkout name.
    # Tests load the actual plugin entry under their own explicit package name.
    package = types.ModuleType("yui_npc_controller")
    package.__path__ = [str(ROOT)]
    package.__file__ = str(ROOT / "__init__.py")
    package.__spec__ = importlib.util.spec_from_file_location(
        "yui_npc_controller", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    sys.modules["yui_npc_controller"] = package
    sys.modules["__init__"] = package
    import pytest
    sys.exit(pytest.main(sys.argv[1:]))
