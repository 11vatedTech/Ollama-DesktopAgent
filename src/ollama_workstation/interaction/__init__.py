"""Agent interaction engine: an isolated browser and a computer-use broker.

Both services are reached only through :class:`policy.InteractionBroker`,
which authorizes every action from host-side facts (the action, its target's
role and label, the agent's grants) and never from model-generated text, and
which appends every decision to a hash-chained audit trail.
"""
