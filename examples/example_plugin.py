"""An example site plugin: everything a deployment adds lives in one module like this, outside the library.

Load it with `plugins = ["example_plugin"]` and `plugin_paths = ["<its folder>"]` in the config.
"""
import re


def register(api):
    # a resource that only exists here
    api.resource('staging', 'the shared staging server: one load run at a time')

    # a pre-take check: refused with the reason
    def staging_after_merge(app, res, mine, current):
        if not mine.get('item'):
            return 'staging only for an item whose pull request is merged: say the item with --item'
        return None
    api.lease_check(staging_after_merge, resources=['staging'])

    # a link for `note --pr N`
    api.pr_info(lambda n: {'repo': 'web', 'url': f'https://git.example.test/web/pulls/{n}'})

    # a warning on the text of a note (never blocks)
    def ticket_tag(*texts):
        bare = [m.group(0) for t in texts for m in re.finditer(r'(?<![#\w])\d{5}\b', t or '')]
        return f'warning: ticket number without #: {", ".join(bare)}' if bare else None
    api.text_check(ticket_tag)

    # a guard rule the person lifts: refused until their yes to an ask tagged [release deploy]
    api.guard_rule('deploy', lambda command, unquoted, cwd: re.search(r'\bdeploy\.sh\b', unquoted),
                   'person', 'a deploy is the person\'s call.', key='deploy')

    # one more line in every session's start context
    api.start_lines(lambda app, who, sessions: ['Ship nothing on Fridays.'])

    # a command of its own: `signalbox hello <who>`
    api.command('hello', lambda app, args: print(f'hello, {args.who}') or 0,
                configure=lambda p: p.add_argument('who'), help='say hello')
