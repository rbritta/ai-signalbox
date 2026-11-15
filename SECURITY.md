# Security

## Supported versions

Only the latest minor release gets fixes.

| Version | Supported |
|---|---|
| 0.1.x | yes |
| older | no |

## Trust model

- **The state root is trusted input.** Status cards, threads, the person's messages and the event log are plain files
  under one folder (`~/.signalbox` by default). Whoever can write there can put words in front of a session, so keep
  it in your user profile, never in a shared or synced folder.
- **Hooks never fail the host.** An error in a hook is one line on stderr and exit 0; the guard only denies, it never
  approves a tool call or answers a permission prompt.
- **`serve` listens on 127.0.0.1 with a token** by default. Exposing it beyond the machine is your decision; put it
  behind something that authenticates.
- **No network, no telemetry.** The core opens no connection other than `serve`'s listener, and plugins run with your
  privileges: only load the ones you trust.

## Reporting a vulnerability

Please report it privately through
[GitHub private vulnerability reporting](https://github.com/rbritta/ai-signalbox/security/advisories/new), not in a
public issue. You can expect a first answer within a few days.
