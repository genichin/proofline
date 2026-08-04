"""Safe YAML loading with duplicate mapping keys rejected."""

from __future__ import annotations

import yaml


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader variant which rejects duplicate keys recursively."""

    def construct_mapping(self, node: yaml.nodes.MappingNode, deep: bool = False):
        if not isinstance(node, yaml.nodes.MappingNode):
            raise yaml.constructor.ConstructorError(
                None, None, "expected a mapping node", node.start_mark
            )
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    "found an unhashable key", key_node.start_mark,
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    f"found duplicate key ({key!r})", key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    UniqueKeyLoader.construct_mapping,
)


def safe_load_unique(payload: str):
    return yaml.load(payload, Loader=UniqueKeyLoader)
