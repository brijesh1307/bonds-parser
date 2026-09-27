## What and why

<!-- Task ID from docs/14_task_breakdown.md (e.g. T2.3) and a short description. -->
Task:

## Checklist (docs/13_development_plan.md §4.3)

- [ ] Task ID in the title; linked issue
- [ ] Tests added/updated; `pytest` green locally
- [ ] Golden outputs unchanged, or the change is intentional and explained below
- [ ] No floats for money/rates; dates ISO; errors RFC 7807
- [ ] State change writes an audit row in the same transaction
- [ ] New endpoint has tag, summary, examples and error responses in Swagger
- [ ] New setting is a `BONDS_*` env var in `.env.example`
- [ ] Baseline / docs updated if a contract changed
- [ ] No secrets, no real slip data

## Notes for the reviewer
