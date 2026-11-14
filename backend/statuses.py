"""Job status groups shared by the queue, routes and statistics.

The frontend mirrors these in frontend/src/app/utils/job-status.ts.
"""

WAITING = frozenset({"queued", "queued_replace"})
RUNNING = frozenset({"preparing", "downloading", "encoding", "replacing"})
IN_QUEUE = WAITING | RUNNING
# A job in any of these states must not be enqueued again.
UNSETTLED = IN_QUEUE | {"review"}
# Replacing is irreversible once started, so it cannot be cancelled.
CANCELLABLE = frozenset({"queued", "preparing", "downloading", "encoding"})
SUCCEEDED = frozenset({"done", "downloaded", "encoded"})
FINISHED = SUCCEEDED | {"skipped", "error", "cancelled", "discarded"}
# The job ended in review and keeps its related-asset claims until resolved.
HOLDS_CLAIMS = frozenset({"review", "queued_replace"})
