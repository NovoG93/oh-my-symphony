from __future__ import annotations

import yaml
import pytest

from symphony.pm import yaml_safe


def test_unique_loader_rejects_duplicate_keys_and_unsafe_tags() -> None:
    with pytest.raises(yaml.YAMLError):
        yaml_safe.load_unique_yaml("value: 1\nvalue: 2\n")
    with pytest.raises(yaml.YAMLError):
        yaml_safe.load_unique_yaml(
            "value: !!python/object/apply:os.system ['echo unsafe']\n"
        )
    with pytest.raises(yaml.YAMLError, match="unhashable key"):
        yaml_safe.load_unique_yaml("? [a, b]\n: value\n")


def test_unique_loader_preserves_safe_aliases() -> None:
    loaded = yaml_safe.load_unique_yaml(
        "base: &base\n  value: safe\nalias: *base\n"
    )
    assert loaded == {"base": {"value": "safe"}, "alias": {"value": "safe"}}


def test_unique_loader_uses_c_loader_and_safe_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    yaml_safe._LOADERS.clear()
    accelerated = yaml_safe._unique_key_loader()
    if hasattr(yaml, "CSafeLoader"):
        assert yaml.CSafeLoader in accelerated.__mro__

    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    yaml_safe._LOADERS.clear()
    fallback = yaml_safe._unique_key_loader()
    assert yaml.SafeLoader in fallback.__mro__
    assert yaml_safe.load_unique_yaml("value: safe\n") == {"value": "safe"}
    with pytest.raises(yaml.YAMLError, match="unhashable key"):
        yaml_safe.load_unique_yaml("? [a, b]\n: value\n")
