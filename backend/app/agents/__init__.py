"""Agents: a model that reads the ledger and answers a hard question about it.

Everything else in this app answers a question somebody already knew to ask.
The dashboard shows what was spent, the Budget tab what a month costs, Explore
whatever query you can assemble. An agent is for the questions a person does
not know how to phrase - "am I actually in trouble in March?", "which of these
subscriptions is quietly costing me the most?", "if I put 2 lakh somewhere,
where does it do the most good?" - and it works by being handed the ledger and
a job, not by being handed an answer to narrate.

Three pieces:

  toolbelt   the read-only tools an agent may call. Every one is a
             whitelisted, deterministic computation over the user's own rows,
             most of them the same functions the tabs are built from.
  catalogue  the agents themselves: a question, a brief, and the tools that
             question needs.
  runner     the loop. Ask, execute the tool calls, feed the results back,
             stop when the model answers or the step budget runs out.

Two boundaries hold everywhere in here:

  * Numbers come from tools, never from the model. The model chooses what to
    look at and what it means; the arithmetic is done in Python over Decimals
    and handed back exact. Every run keeps its full transcript, so any figure
    in an answer can be traced to the tool call that produced it.

  * The advice line from llm.narrative applies unchanged. An agent may say
    "your EMIs are 43% of take-home, which is above the 40% lenders treat as
    stretched" and may lay out the mechanics of an option. It may not tell
    somebody what to buy, sell or prioritise with their money.
"""
