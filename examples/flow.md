# Development flow

The flow block below is what `signalbox note --phase` checks a passage against, and what `signalbox flow --json`
gives a person-facing app.

```flow
version  2026-01-01.1
stage    analysis  init analysis plan
stage    dev       implement verify resume
stage    review    validate review
stage    ship      pr-create pr pr-review merge post closeout handoff
stage    backport  target proof rep-pr rep-merge
chain    init > analysis > plan > implement > verify > validate|review
free     validate <> review
chain    validate+review > pr-create > pr
parallel pr > pr-review > merge
parallel pr > post > closeout > handoff
lane     pr-review > target > proof > rep-pr > rep-merge   # one track per target: note --target <branch>
loose    cleanup     # zeroes the workspace, outside any item
loose    blocked     # waits for a third party
return   blocked > resume
return   verify|validate|review|pr-review > implement
return   * > analysis  (before the pr)
person   review merge rep-merge
gate     pr-create pr pr-review closeout handoff cleanup
```

## Changes

| Version | Date | What changed | Asked by |
|---|---|---|---|
| 2026-01-01.1 | 2026-01-01 | First flow | the team |
