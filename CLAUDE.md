# CLAUDE.md

## Code style

- Favor simple, explicit code over short or clever code, even if it's longer.
- Avoid complex syntax: nested comprehensions, walrus operator, chained ternaries, non-trivial lambdas, custom decorators, metaclasses.
- Break long pandas/DuckDB chains into named intermediate steps.
- Don't add new abstractions (classes, inheritance, patterns) without asking first.
- Keep functions small and single-purpose (~30 lines max, ≤2 nesting levels; use early returns).
- Give every function a NumPy-style docstring: summary, Parameters, Returns, Raises.
- Comment non-obvious code: domain logic, assumptions, workarounds, magic numbers.
- Comments explain *why*, not *what*. Never restate the code.
