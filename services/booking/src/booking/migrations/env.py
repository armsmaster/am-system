from am_common.migrations import run_env
from booking.db import metadata

run_env(metadata)
