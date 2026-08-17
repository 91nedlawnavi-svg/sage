# Sage engineering contract

## Ownership

Elliot owns product direction, unresolved decisions, secrets, approval for outward-facing or destructive actions, and felt tests.

Engineering owns tracing behavior end to end, smallest correct implementation, deterministic checks, separate review for non-trivial changes, and honest reporting.

Surface uncertainty immediately. Do not invent product decisions.

## Build rules

- Keep Sage local-first and single-user.
- Route Sage inference through free-tier local router aliases only. Never use paid Claude API.
- Gracefully degrade on chat and background paths: safe no-op or visible safe error, never an uncaught path failure.
- `~/sage_data/` is lived memory, separate from code and identity. Never commit it. Back up before any live-memory write.
- Memory records timestamped events, not frozen facts. Do not add locks, promotion queues, fact/current-state tables, or reconciliation that stores a defended present state.
- Compute present meaning at recall. Keep contradictory events.
- Keep relational world data and Sage interior data physically separate.
- Held-close material must never return through casual recall or background provider re-shipping.
- Store timestamps as UTC-aware ISO 8601. Display user-facing time in WIB.
- `directive.txt` is deferred. Once introduced, prompt assembly must place it first and use it verbatim.
- Sage may reach only through one revisable waiting message in app. No push notifications.
- Sage beliefs change through argument and evidence. Do not create a routine external belief-edit path.

## Working loop

1. State one behavior-sized outcome and its acceptance checks.
2. Trace affected code and callers before editing.
3. Make smallest root-cause change.
4. Run relevant deterministic check; quote real output.
5. Review non-trivial diff separately.
6. Report changed behavior, changed files, checks run, and unverified edges.

## Approval boundaries

Engineering may commit and push Sage work after relevant checks and review. Require Elliot's explicit approval before tags, GitHub actions, service restarts, `.env` changes, live-memory writes, deletions, or migration/cutover actions. Passing checks is not approval.

## Task workbench

- Use `~/sage/workbench/` for task-specific source archives, audit outputs, and temporary working files.
- Keep each task's material in its own subdirectory.
- Treat workbench copies as disposable. Never use it for lived memory.

## Documentation authority

- `docs/NORTH_STAR.md`: purpose and non-goals.
- `docs/INVARIANTS.md`: non-negotiable behavior.
- `docs/DECISIONS.md`: append-only settled decisions.
- `docs/BLUEPRINT.md`: authoritative long-range product map and sequencing.
- `docs/MILESTONE.md`: sole current scope and acceptance evidence.
- `README.md`: public description of current reality.

Precedence:

- North Star and Invariants constrain everything else.
- Decisions records settled choices.
- Blueprint guides future sequencing.
- Milestone selects active work and acceptance evidence.
- README reports present reality.

Do not create duplicate authorities. Update documents only when reality or a settled decision changes.

## Model-calling guides

Claude Code runs through `https://api.xkiro.com/v1/messages` using Anthropic protocol. Delegate through nested Claude Code CLI calls; Agent tool model overrides do not accept provider aliases.

- Fable: `cc-fable`
- Opus: `cc-opus`
- Sonnet: `cc-sonnet`
- Haiku: `cc-haiku`

Use `claude -p --model <alias>` to delegate. Choose model tier without asking: Haiku for mechanical inventory, Sonnet for subsystem analysis, Fable for hard architecture or adversarial review. Provider aliases must be probed through this CLI path, not Agent model overrides.

IF any of the models above acts up, PAUSE IMMEDIATELY and ask Elliot to fix it first.
