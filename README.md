# obsidian-slack-sweep

Sweeps Slack for messages that need you, adds them as tasks to an Obsidian note, and checks them off when the thread shows that you handled them. It runs on macOS every 2 hours while you use the computer, with Claude Code (`claude -p`) and the Slack connector of your claude.ai account.

```
- [ ] (acme) answer dana on the export limit before friday ([thread](https://acme.slack.com/archives/C0123ABCD/p1791574935737669))
```

## What it reads

- `mentions`: @mentions of your Slack user ID, in every channel and DM. A teammate with your first name never matches.
- `dms`: DMs and group DMs where the newest message is not yours.
- `threads`: new replies in threads that you posted in.
- `channels`: channels that you list, for messages that nobody on your team answered.
- `keywords`: words that you list, such as account names, in every channel.

The sweep marks a task done only when the thread shows that you replied or that the asker closed it. It never adds a second task for a thread that already has an open task.

## Install

You need macOS, Python 3.11 or newer, Xcode Command Line Tools, and Claude Code logged in with a claude.ai account that has the Slack connector.

```
git clone https://github.com/jakesciotto/obsidian-slack-sweep ~/src/obsidian-slack-sweep
cd ~/src/obsidian-slack-sweep
mkdir -p ~/.config/obsidian-slack-sweep
cp config.example.toml ~/.config/obsidian-slack-sweep/config.toml
```

Set `[vault] path` in the config. [docs/configuration.md](docs/configuration.md) lists every key. Then run one sweep that writes nothing, and install the job:

```
./bin/obsidian-slack-sweep run --now --dry-run
./bin/obsidian-slack-sweep install
```

macOS blocks launchd jobs from `~/Documents`, `~/Desktop`, `~/Downloads` and iCloud Drive. If your vault is there, give Full Disk Access to `~/.local/share/obsidian-slack-sweep/obsidian-slack-sweep` in System Settings > Privacy & Security.

Commands: `run` (with `--now` or `--dry-run`), `install`, `uninstall`, `status`. State and logs are in `~/.local/state/obsidian-slack-sweep/`.

## Safety

- The agent gets only the Slack read tools. A preflight run lists every MCP server, the real run denies all of them except Slack, and hooks and plugins are off. The run stops if any tool has no deny rule.
- The agent never writes to the vault. Python checks its JSON, removes links, tags and fields from the task text, and reads the note again before each write.
- The launcher that holds Full Disk Access runs one fixed command and removes `DYLD_*` and `PYTHON*` variables first.

## Limits

- The agent decides what needs action. Tune it with `instructions` in the config.
- Hooks and plugins from managed (organization) settings still apply.
- Only macOS has a scheduler. On another system, run `obsidian-slack-sweep run` from your own scheduler.

## License

MIT
