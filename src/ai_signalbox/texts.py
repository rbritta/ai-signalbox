"""The wording signalbox puts in front of a session or a person. Every key can be overridden in the config (`[texts]`)
or by a plugin (`api.text`), so a deployment speaks its own language and names its own rules."""

DEFAULT_TEXTS = {
    # hook registration (settings installer)
    'hook.vigil_message': 'Message from the person for this session:',
    'hook.vigil_summary': 'signalbox: message',
    'hook.wake_message': 'signalbox: a message or a thread waits for this session:',
    'hook.wake_summary': 'signalbox: message or thread',

    # delivery of a message into a session
    'delivery.first': '[{source} -> {workspace}, {time}, {about}, message {id}{opens}]\n{text}',
    'delivery.turn': '[{source} -> {workspace}, {time}, thread {thread}, turn {turn}{closed}]\n{text}',
    'delivery.about_ask': 'about your ask #{n}{ask_text}',
    'delivery.about_none': 'no ask referenced',
    'delivery.opens': ' (opens thread {thread})',
    'delivery.closed': ', already closed',
    'delivery.footer_thread': ('Thread: questions, progress and the result of what was asked go in it with '
                               '`signalbox thread <id> --text-file <file>` until it is closed.'),
    'delivery.footer_first': '',
    'delivery.footer_person': ('This is the person speaking: it counts as their request. Say in one line what arrived '
                               'and go on. A resolved ask closes with `signalbox ask --close N`.'),
    'delivery.footer_notice': 'Say in one line what arrived and go on.',

    # session start
    'start.title': '=== signalbox ===',
    'start.you': 'You are {workspace}{session}.',
    'start.session': ', session {name} (the name other sessions use to message you)',
    'start.peers': 'Live peers: {peers}',
    'start.leases': 'Leases: {leases}',
    'start.priority': ('Delivery priority: {order}{position}. It only settles an impasse (a resource, an order, a '
                       'conflicting change): ask the person and, with no answer in {minutes} min, the lower number goes '
                       'on and the other yields and says so.'),
    'start.inbox': 'The inbox of {workspace} has notes: run `signalbox inbox`.',
    'start.reminders': ('Before taking a shared resource: `signalbox hold <resource>`. Full state: `signalbox board`. '
                        'What waits for the person becomes an ask (`signalbox ask`).'),
    'start.threads_open': ('Open threads in {workspace} (reply with `signalbox thread <id> --text-file <file>`, reread '
                           'with `signalbox thread <id>`): {threads}'),
    'start.threads_owed': ('A reply is owed by this session (the last turn is the person\'s, and the thread is yours, '
                           'has no owner, or its session is gone or does not listen): read it with `signalbox thread '
                           '<id>` and answer there before anything else, saying in one line what it was: {ids}'),
    'wake.owed': ('On connecting: a reply is owed by this session in {ids}. Read it with `signalbox thread <id>` and '
                  'answer there now with `signalbox thread <id> --text-file <file>`; say in one line what was pending '
                  'and what you answered.'),
    'prompt.lease_update': ('{resource} is being updated by {holder} since {since}: this session\'s server for it stays '
                            'off until the release notice.'),

    # compaction
    'compact.title': '=== signalbox: this session in {workspace} was just compacted{when} ===',
    'compact.no_card': 'signalbox has no status for this session: run `signalbox note` before going on.',
    'compact.asked': 'The person asked ({at}): {text}',
    'compact.footer': ('Before going on: check with `signalbox peek {workspace}` that the status, tasks and links match, '
                       'and go on from where you stopped; do not redo what is done.'),

    # priority
    'priority.notice': ('[{by}->{workspace}] NOTICE: {reason}delivery priority is now {order}; {workspace} is '
                        '{position}{was}. It only settles an impasse: ask the person and, with no answer in {minutes} '
                        'min, the lower number goes on and the other yields and says so. No interruption without an '
                        'impasse.'),
    'priority.impasse': ('priority: {mine} {a}, {other} {b}. Impasse: ask the person (signalbox ask); with no answer in '
                         '{minutes} min, {goes} goes on and {yields} yields and says so, citing the priority.'),

    # closing
    'close.request': ('[{sender}->{workspace}] REQUEST: CLOSE, asked by the person. Report what you did, clean up and '
                      'close: `signalbox close --done-file <file>` with one line of what this session did; the tab and '
                      'the window close after. Send the reply with the report before the tab closes.'),

    # guard
    'guard.ask': ('{reason} No dialog (it freezes the session): ask the person and keep going: write a file holding '
                  '`{tag} <what and why>` and run `signalbox ask --type {type} --text-file <file>`; with their yes, '
                  'the same command passes for {minutes} min.'),
    'guard.cli_text': ('Free text for signalbox never goes through the shell (a quoted argument, echo with a pipe, a '
                       'heredoc): the host\'s own safety check can open a dialog even in bypass mode and freeze the '
                       'session. Write the text to a file and pass `--text-file <file>`.'),

    # notes and jobs
    'note.only_text': ('the phase is already {phase} and only the text would change: a note goes out on a transition '
                       '(a new phase, a new task, --waiting, --done when a stretch closes). Progress inside a phase is a '
                       'job step; waiting for the person is --waiting with no --phase.'),
    'note.rereview': ('a new {phase} needs a change since the last one ({needs} in between). Waiting for the next step '
                      'is --waiting on the current phase; if the person asked for another one, --requested "<their '
                      'words>".'),
    'note.off_guide': ('off the guide {version}: {source} -> {phase} ({reason}). It does not block: the status shows '
                       '"off guide". If the person asked for it, repeat with --requested "<their words>".'),
    'job.done_missing': ('warning: step {step} closed without --done, so its line shows only the time. A build says '
                         'what compiled, with errors and warnings; a test run says the scope and the counts. It does '
                         'not block; the done line goes on the command that closes the step.'),

    # the workflow check (the reason of an off-guide passage)
    'flow.resume_first': '{resume} goes through {next} first',
    'flow.missing': 'missing {phases}',
    'flow.and': ' and ',
    'flow.return_only': 'the return to {phase} only holds {condition}',
    'flow.outside': 'passage outside the guide',

    # note
    'note.item_needs_text': ('--item {item} takes the text, which titles the item: note --item {item} --phase <phase> '
                             '"<title>"'),
    'note.bad_task': '--task takes the id of the task (or - to clear it)',
    'note.requested_needs_phase': '--requested goes with the --phase the person asked for and their words',
    'note.bad_target': '--target takes the name of the target (a branch), or - to clear it',
    'note.renamed': 'warning: phase {old} is now called {new} (guide {version}); recorded {new}',
    'note.unknown_phase': 'warning: phase outside the vocabulary ({phases})',
    'note.nothing_changed': 'Nothing changed on the status.',
    'note.target_only_in_lane': ('warning: --target only holds in the phases of a lane ({phases}); {phase} belongs to '
                                 'the main flow and the status left the target'),
    'note.lane_without_target': ('warning: a lane phase with no target: name it with --target <name>, so the app shows '
                                 'one track per target. Recorded with no target.'),
    'note.ok_needs_task': "phase {ok} belongs to a task only: --task <id>, or an active task on the status",
    'note.bad_pr': '--pr takes the number of the pull request under review (and --item, the item it delivers)',
    'note.priority_hint': ('you hold {resource} waiting for the person and {peers} has a higher priority in the queue: '
                           'with no answer in {minutes} min, `signalbox release {resource}` and tell them, citing the '
                           'priority'),

    # the status against the guide
    'ask.ev_closed': 'closed #{n}: {text}{note}',
    'ask.ev_note': ' ({note})',
    'lease.no_reason': 'no reason',
    'lease.mode_shared': 'shared',
    'lease.mode_exclusive': 'exclusive',
    'lease.ev_waiting': 'in the queue of {res} ({mode}), waiting for {holder}: {why}',
    'lease.ev_took': 'took {res}{in_mode}{until}: {why}',
    'lease.ev_in_mode': ' in {mode} mode',
    'lease.ev_until': ', until ~{time}',
    'lease.ev_uses': 'uses {res}: {why}',
    'lease.ev_queue': 'in the queue of {res}{mode} (position {pos}, held by {holder}): {why}',
    'lease.ev_queue_mode': ' ({mode})',
    'lease.free_holder': 'nobody (the lease is free)',
    'lease.ev_stopped': 'stopped using {res}',
    'lease.ev_released': 'released {res}{result}{next}',
    'lease.ev_result': ' ({result})',
    'lease.ev_next': '; next {next}',
    'job.what_toggle': '{name} {state}',
    'job.what_services': 'services {services}',
    'job.what_waits_person': 'waits for the person',
    'job.what_waits': 'waits',
    'job.end_ok': 'ok',
    'job.end_failed': 'failed',
    'status.part_pr': 'PR {pr}',
    'status.part_task': 'task {task}',
    'status.part_target': 'target {target}',
    'status.part_waiting': 'waiting {waiting}',
    'status.requested': 'at their request',
    'status.requested_long': 'at their request: {text}',
    'status.off_guide': 'off guide',
    'status.off_guide_long': 'off guide: {source} -> {phase}',

    # leases
    'lease.users_notice': 'in use now; send a notice before changing it: {users}',
    'lease.tell_next': ('Tell them: SendMessage to="{name}" message="[{sender}->{workspace}] FREE: {resource} released, '
                        'your turn"'),

    # peek
    'peek.header': '== {label}  {status}  branch {branch}',
    'peek.priority': '{position} ({order}, by {by} at {time})',
    'peek.card': 'status: item {item} · task {task} · phase {phase} · waiting {waiting} · {text} ({time})',
    'peek.guide': 'guide {guide}{mark}',
    'peek.guide_mark': ' · {mark}',
    'peek.tasks': 'tasks: {tasks}',
    'peek.task': '{task} {phase} ({time})',
    'peek.asks': 'asks: {asks}',
    'peek.offers_open': 'offers it may claim (signalbox claim <id>, or claim --next):',
    'peek.offers_claimed': 'offers it claimed:',
    'peek.last_prompt': 'last prompt of the person ({at}): {text}',
    'peek.words': '--- {at}\n{text}',
    'peek.no_session': '(no session)',
    'peek.not_found': 'no session nor workspace "{target}". Live: {live}',

    # inbox and log
    'inbox.note_line': '- {date} from {who}: {text}\n',
    'inbox.log_line': '- {time} **{who} -> inbox {workspace}**: {text}\n',
    'inbox.ev_post': '-> inbox {workspace}: {text}',
    'inbox.posted': 'left in the inbox of {workspace}; its next session sees it on start',
    'inbox.empty_note': 'empty note',
    'inbox.no_workspace': 'no workspace here: give one',
    'inbox.empty': 'inbox empty',
    'log.none': 'no message today',

    # priority
    'priority.ev': '{order} (by {by}{reason})',
    'priority.ev_reason': ': {reason}',
    'priority.log_line': '- {time} **{by} -> priority**: {order}{reason}{told}\n',
    'priority.log_reason': ' ({reason})',
    'priority.log_told': '; notice to {told}',
    'priority.outside': 'outside the priority: {outside}. Valid: {valid}',
    'priority.repeated': 'repeated workspace: {repeated}',
    'priority.bad_by': ('--by {person} or a workspace ({valid}); without --by, the calling session\'s workspace, and '
                        'none was found'),
    'priority.person_set': ('the previous order was set by the {person}: it changes only by agreement of the workspaces '
                            'that moved ({changed})'),
    'priority.position': 'priority {n} of {total}',
    'priority.of': '{n} of {total}',
    'priority.out': 'out of the order',
    'priority.was': ' (was {n})',
    'priority.delivered_reason': '{workspace} delivered and clean; the free workspaces go back to their number',
    'priority.delivered_held': ' ({workspace} is {place}: place {home} is taken)',
    'start.priority_you': ' (you are {n} of {total})',
    'start.priority_out': ' ({workspace} is out of the order)',

    # jobs closed with the session
    'job.closed_waiting_person': 'stopped waiting for the person',
    'job.closed_session': 'session closed',

    # the calling session
    'cli.no_session': 'no calling session found',
    'cli.no_session_here': 'no calling session found (run it from inside a session in a workspace)',
    'cli.not_registered': 'session {sid} is not in the registry',

    # note: what it prints (`status` is the card, as the status_render hook shows it; JSON by default)
    'note.printed': 'status of {label}: {status}',

    # jobs: states, lines and refusals
    'job.st_running': 'running',
    'job.st_ok': 'ok',
    'job.st_failed': 'failed',
    'job.st_todo': 'todo',
    'job.st_skipped': 'skipped',
    'job.steps_waits_person': ' · waits for the person',
    'job.steps_waits_session': ' · waits for the session',
    'job.who_person': 'the person',
    'job.who_session': 'the session',
    'job.printed': 'job {name} of {label}: {st} · {steps}',
    'job.list_line': '{id} · {item} · {st} since {since} · {steps}',
    'job.none_today': 'no job today',
    'job.last_job_opened': 'the job opened',
    'job.last_job_closed': 'the job closed',
    'job.last_step_opened': 'step {step} opened',
    'job.last_step_closed': 'step {step} closed',
    'job.bad_services': '--services takes a comma-separated list, each name or name:port: "web:8000,api:8001"',
    'job.duration_overlap': ('--duration {duration} would start step {step} at {time}, before the end of the one before '
                             '({before})'),
    'job.zero_step': ('step {step} would close with 0 s (opened at {time}): open the step when its command starts, use '
                      '--at with the real time or --duration <seconds> with the real length'),
    'job.bad_at': '--at takes an ISO time: 2026-01-12T14:45:58',
    'job.at_future': '--at {at} is in the future',
    'job.no_name': "job with no name only lists today's: a step takes the job's name (job <name> --step S)",
    'job.bad_name': 'job <name>: letters, digits, dot, hyphen or underscore, up to 60',
    'job.one_action': ('job <name> takes one of: --steps "a,b,c" (open), --step <name> (advance), --wait [person] '
                       '(pause between steps) or --end ok|failed; --toggle and --services go with any of them or alone'),
    'job.bad_toggle': '--toggle takes name=state: "email=on"',
    'job.services_on_open': ('--services goes with the command that closes the step they came up or went down in '
                             '(--step, --wait, --end), or alone, on the running step: opening the job closes no step'),
    'job.fields_alone': '--toggle and --services alone only record those; the rest goes with a step of the job',
    'job.note_needs_end': 'the note goes only with --end',
    'job.item_on_open': '--item and --from go with --steps, when the job opens',
    'job.pid_needs_step': '--pid goes with the step that runs in a process: --steps or --step',
    'job.bad_pid': '--pid takes the process number',
    'job.log_needs_step': '--log goes with the step that runs: --steps or --step',
    'job.bad_log': '--log takes the path of the output file (.log, .txt or .out)',
    'job.bad_wait': '--wait alone waits for the session; --wait person waits for the person',
    'job.done_on_open': '--done "<line>" goes with the command that closes a step: --step, --wait or --end',
    'job.bad_end': '--end takes ok or failed',
    'job.bad_duration': ('--duration <seconds> (1 to 86400) goes with the command that closes a step: --step, --wait '
                         'or --end'),
    'job.at_before_last': ('--at {at} comes before the last step of the job: {what} at {time}. Steps go in order, each '
                           'with its real time; stop the sequence at this refusal, or the done line of the next step '
                           'lands on the wrong one. Nothing changed in the job'),
    'job.no_job': 'there is no job {name} today: open it with --steps',
    'job.no_job_fields': 'there is no job {name} today: open it with --steps',
    'job.services_no_running': ('--services alone goes on the running step, and job {name} has none: put it on the '
                                'command that closes the step they came up or went down in'),
    'job.bad_steps': '--steps takes a comma-separated list: "export,import,check"',
    'job.already_running': 'job {name} is already running: --step <name> to advance or --end ok|failed to close',
    'job.already_today': ('there was already a job {name} today ({st} at {time}): use another name; to run a step of a '
                          'failed job again, --step <name>'),
    'job.from_not_found': "--from {origin}: no job with that name on another session's card",
    'job.already_closed': 'job {name} already closed ({st} at {time}): open another with --steps',
    'job.already_closed_failed': '; to run one of its steps again, --step <name>',
    'job.duration_no_running': '--duration: job {name} has no running step to close',
    'job.services_none_closed': '--services: job {name} has not closed any step yet',
    'job.done_none_closed': '--done: job {name} has not closed any step yet',
    'job.done_foreign': ('--done belongs to {label}, but this command closes step {step}: the line of a step goes on the '
                         'command that closes that step, in order and with its real time. Nothing changed in the job'),
    'job.bad_step': '--step takes the name of the step that starts',
    'job.already_waits': 'job {name} already waits for {who}',

    # leases: what hold, want and release print and refuse with
    'lease.unknown': 'unknown resource: {res}. Use one of: {resources}',
    'lease.none_configured': '(none configured)',
    'lease.no_shared': '{res} has no shared mode; only {modal}',
    'lease.none_shared': 'none',
    'lease.bad_pid': 'invalid pid: {pid}',
    'lease.no_pid': 'pid {pid} does not exist',
    'lease.no_session': 'no calling session found (run it from inside a session in a workspace, or pass --pid)',
    'lease.inherited': ('{res} is already held by the process that called this one ({holder}, pid {pid}): this one '
                        'works under it'),
    'lease.the_queue': 'the queue ({waiting})',
    'lease.busy': 'busy: {res} is held by {holder} since {since} ({why}). Join the queue: signalbox want {res} --why "..."',
    'lease.locked': '{res} locked by {holder}',
    'lease.lock_timeout': 'the leases are locked by another command for over 10 s: nothing changed, try again',
    'lease.queued': '{res} busy with {holder}; in the queue, waiting up to {wait} s',
    'lease.gave_up': 'gave up on {res} after {wait} s: still with {holder}',
    'lease.check': '{reason}',
    'lease.stale_replaced': 'old lease of {holder} (ended) replaced',
    'lease.in_mode': ' in {mode} mode',
    'lease.yours': '{res}{in_mode} is yours ({holder}{until}). Queue behind: {queue}',
    'lease.nobody': 'nobody',
    'lease.use_word': 'use',
    'lease.held_exclusive': ('{res} held exclusively by {holder} since {since} ({why}). Wait for the release: signalbox '
                             'want {res} --shared'),
    'lease.in_use': '{res} in use by you ({holder}), along with: {others}. When done: signalbox release {res} --shared',
    'lease.already_yours': '{res} is already yours',
    'lease.queue_position': 'in the queue of {res}, position {pos}. Held by: {holder}. On release, the holder tells you.',
    'lease.use_ended': '{res}: your use ended; still using it: {others}',
    'lease.not_a_user': '{res}: you were not in the list of users',
    'lease.stays_with': '{res} stays with {holder}, which called this process',
    'lease.already_free': '{res} was already free',
    'lease.not_yours': '{res} belongs to {holder}; only the holder releases it',
    'lease.released': '{res} released',
    'lease.next': 'next: {holder} ({why})',
    'lease.next_waits': ': it waits in the queue and takes it by itself',
    'ports.busy': 'port really in use: {ports}. Talk to whoever is on it or join the queue.',
    'ports.busy_one': '{port} with {owner} (pid {pid})',
    'ports.no_workspace': 'a process outside the workspaces',

    # asks
    'ask.bad_close': '--close takes the number of the ask',
    'ask.none_word': 'none',
    'ask.not_open': 'there is no open ask #{n}. Open: {open}',
    'ask.closed': 'ask #{n} closed; open: {count}',
    'ask.list_line': '#{n} {type}{red} {time}: {text}',
    'ask.list_red': ' (red)',
    'ask.none_open': 'no open ask',
    'ask.bad_type': 'unknown type: {type}. Use one of: {types}',
    'ask.opened': 'ask #{n} ({type}{red}) on the card of {label}. Resolved: signalbox ask --close {n}',
    'ask.opened_red': ', red',

    # offers: work put up for a session to claim
    'offer.anyone': 'any workspace',
    'offer.bad_id': 'invalid offer id (YYYYMMDD-HHMMSS-xxxxxx): {id}',
    'offer.bad_workspace': 'invalid workspace: {workspace}',
    'offer.empty': 'empty offer: give the work with --text-file <file>',
    'offer.too_long': 'offer with {size} characters (cap {cap}): put the long part in a file and offer the summary with '
                      'its path',
    'offer.ev_open': 'for {to}: {title}',
    'offer.ev_withdrawn': 'withdrawn: {title}',
    'offer.ev_expired': 'expired: {title}',
    'offer.ev_reopened': 'open again: {title} ({workspace} {name} ended without answering)',
    'offer.ev_claimed': 'claimed: {title}',
    'offer.log_open': '- {time} **{who} offers to {to}**\n  > {title}\n',
    'offer.log_claimed': '- {time} **{workspace} {name} claims the offer of {who}**\n  > {title}\n',
    'offer.list_line': '{id}  for {to}, since {day}{time}{until}{by}{item}: {title}',
    'offer.list_by': ', from {who}',
    'offer.list_until': ', until {time}',
    'offer.list_item': ', item {item}',
    'offer.list_all_header': 'no calling session here, so every open offer (as `signalbox offers --all`):',
    'offer.none_open': 'no open offer for this session',
    'offer.none_done': 'no claimed or withdrawn offer',
    'offer.history_claimed': '{id}  claimed {when} by {workspace} {name}{answered}: {title}',
    'offer.history_unanswered': ' (not answered yet)',
    'offer.history_withdrawn': '{id}  withdrawn {when}{expired}: {title}',
    'offer.history_expired': ' (expired)',
    'offer.claimed_read': ('claimed: message {message} was already taken out of the box; read it with '
                           '`signalbox thread {message}`'),
    'offer.claimed_arrives': 'claimed: it arrives as message {message}, with the thread of the same id',
    'offer.card_unchanged': 'the card stays as it was (item {item}): {why}',
    'offer.unknown_workspace': 'note: no workspace named {workspace} was found ({known}); the offer waits for one',
    'offer.taken': 'offer {id} was already claimed by {workspace} {name} at {time}',
    'offer.withdrawn': 'offer {id} was withdrawn at {time}',
    'offer.expired': 'offer {id} expired at {time}',
    'offer.racing': 'offer {id} is being claimed by another session right now: run `signalbox offers` again',
    'offer.unknown': 'offer {id} does not exist',
    'offer.none_to_claim': 'no open offer for this session to claim',
    'offer.bad_minutes': '--minutes takes a positive number of minutes, not {minutes}',
    'offer.notice': ('[offer {id} for {to}{by}] {title}\nTake it with `signalbox claim {id}` (or `signalbox claim '
                     '--next`); `signalbox offers` lists the open ones.'),
    'start.offers': 'Offers you may claim (`signalbox claim <id>`, or `claim --next`): {offers}',
    'offer.not_yours': 'offer {id} is for {workspace}, not for {mine}',

    # compaction: the status given back
    'compact.item': 'Item {item}, phase {phase}{task}{waiting}.',
    'compact.item_task': ', task {task}',
    'compact.item_waiting': ', waiting for {waiting}',
    'compact.doing': 'Doing: {text}',
    'compact.tasks': 'Tasks: {tasks}',
    'compact.done': 'Last done ({at}): {text}',
    'compact.asks': 'Open asks with the person: {asks}',
    'compact.prs': 'Pull requests: {prs}',
}


class _Lenient(dict):
    def __missing__(self, key):
        return '{' + key + '}'


def render(template: str, **values) -> str:
    return template.format_map(_Lenient(values))
