from am_common.migrations import run_env
from identity.db import metadata

run_env(metadata)
