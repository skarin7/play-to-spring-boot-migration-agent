"""Node/router implementations for the migration-engine LangGraph state graph.

Split out of what was a single ~980-line agent/graph.py (see that module's
top-of-file docstring for the graph topology). Each submodule here owns one
phase's nodes/routers; agent/graph.py itself is left with only the
topology/wiring (StateGraph construction) plus RuntimeCtx / default_ctx /
recursion_limit.
"""
