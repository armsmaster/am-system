from am_common.migrations import run_env
from reconciliation.db import metadata

run_env(metadata)
