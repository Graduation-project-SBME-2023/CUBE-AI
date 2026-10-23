"""Agent packs (merged plan, Plan 3).

Every agent is a directory ``<pack>/<agent_type>/`` with an ``agent.md``
(front matter + system prompt) and, when it needs code, a ``hooks.py``; see
``toolkit/agents/loader.py`` for both formats. ``catalog.build()`` finds the
files, so there is no central list: a new agent is picked up by being here.

- ``core/``: the control agents (``main_agent``, ``composer``, ``recovery_agent``);
- ``research/``: the execution agents and their internal agents.

Hooks import the toolkit and the tool packs' helpers through
``agentpacks._runtime``, never by an absolute name (see that module for why).
"""
