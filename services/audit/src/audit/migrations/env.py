from am_common.migrations import run_env
from audit.db import metadata

run_env(metadata)
