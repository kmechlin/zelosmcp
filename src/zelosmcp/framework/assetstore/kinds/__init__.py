"""Per-kind asset handlers.

Importing this package triggers the registration of all built-in kinds.
"""
# Import order determines registration order — keep alphabetical.
from zelosmcp.framework.assetstore.kinds import agent as agent  # noqa: F401
from zelosmcp.framework.assetstore.kinds import extension as extension  # noqa: F401
from zelosmcp.framework.assetstore.kinds import hook as hook  # noqa: F401
from zelosmcp.framework.assetstore.kinds import rule as rule  # noqa: F401
