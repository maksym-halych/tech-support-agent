"""Tools exposed to the agent.

One module per tool: each owns its own input schema and any request and
response models of the services it calls, so a change to one contract touches
exactly one file. The tool set itself lives in ``registry``.
"""
