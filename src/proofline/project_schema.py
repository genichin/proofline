"""Shared schema-v1 project scaffold contract."""

CONFIG_BYTES = b"schema_version: 1\nartifact_root: .proofline\n"
RESOURCE_NAMES = ("proofline.yaml", "lines.gitkeep", "criteria.gitkeep")
SCAFFOLD_PATHS = (
    "proofline.yaml",
    ".proofline/lines/.gitkeep",
    ".proofline/criteria/.gitkeep",
)
REQUIRED_DIRECTORIES = (
    ".proofline",
    ".proofline/lines",
    ".proofline/criteria",
)
SUPPORT_MARKERS = frozenset(
    {
        ".proofline/lines/.gitkeep",
        ".proofline/criteria/.gitkeep",
    }
)
