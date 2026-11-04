from am_common.migrations import run_env
from limits.db import metadata

run_env(metadata)
