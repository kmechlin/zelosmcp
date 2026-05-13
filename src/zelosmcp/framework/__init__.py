"""Bifrost-style framework layer for zelosMCP.

Sub-packages mirror Bifrost's ``framework/`` layout:

- :mod:`zelosmcp.framework.assetstore` — generic asset store (rules, extensions,
  agents, hooks) backed by SQLite today; Protocol-based for future backends.
- :mod:`zelosmcp.framework.authstore` — encrypted token + device-session store
  (formerly :mod:`zelosmcp.auth.store`).
- :mod:`zelosmcp.framework.savingsstore` — token-savings + call-events store
  (formerly :mod:`zelosmcp.savings_db`).
"""
