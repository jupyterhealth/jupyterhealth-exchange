import jhe_mcp


def test_package_importable():
    assert hasattr(jhe_mcp, "__version__")


def test_version_is_string():
    assert isinstance(jhe_mcp.__version__, str)
    assert len(jhe_mcp.__version__) > 0
