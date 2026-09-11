"""Does the copilot actually answer correctly?

The unit tests prove the machinery works - the loop runs, tools are called,
figures are verified. None of them can tell you whether the ANSWER is
right, because that depends on a model and on the ledger it is reading.

This is the thing that can. A fixed set of questions whose correct answer
is computed from the database at run time, asked of the real agent against
the real ledger, and scored.

Ground truth comes from SQL, never from the agent's own tools. An eval
built on `ledger_query` cannot catch a bug in `ledger_query` - and there
was one: a date range with no `preset` was silently discarded, so every
dated question was answered with all-time totals. The harness has to be
able to disagree with the tool.
"""

from .harness import CaseResult, EvalReport, run_evals          # noqa: F401
from .cases import CASES                                        # noqa: F401
