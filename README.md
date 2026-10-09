# obsidian-slack-sweep

A scheduled agent that reads Slack for messages that need you, writes them as tasks into an Obsidian note, and checks them off when the thread shows that you handled them.

It runs on macOS through launchd, and only while you use the computer. It uses Claude Code in headless mode (`claude -p`) with the Slack connector of your claude.ai account. The agent can only read Slack. Python, not the model, writes to your vault.

## What it does

launchd wakes the job every 15 minutes. The job runs a sweep only when all of these are true:

1. You used the keyboard or the mouse in the last 10 minutes.
2. The last sweep was 2 or more hours ago (30 minutes after a failed sweep).
3. No other sweep is running.

A sweep does these steps:

1. It reads the open tasks in your vault that contain a Slack link.
2. It asks Claude to search Slack from the start of the last sweep until now, and to check the sweep's open tasks against their threads.
3. It adds a task for each new item at the end of a heading in your inbox note.
4. It marks a task done when its thread shows clear evidence, in the note where the task is now.

A new task:

```
- [ ] (acme) answer dana on the export limit before friday ([thread](https://acme.slack.com/archives/C0123ABCD/p1791574935737669))
```

The same task after the sweep finds your reply:

```
- [x] (acme) answer dana on the export limit before friday ([thread](https://acme.slack.com/archives/C0123ABCD/p1791574935737669)) [completion:: 2026-10-09]
```

### Sources

| Source | Slack search | A message needs action when |
|---|---|---|
| `mentions` | `<@you>` | it asks you for something |
| `dms` | `is:dm` in DMs and group DMs | the newest message is not yours and asks for something |
| `threads` | `with:<@you> is:thread` | someone posts in a thread you took part in |
| `channels` | `in:#a in:#b ...` | someone outside your team posts and nobody from the team replies |

The agent skips FYI posts, social chat, bot alerts, and messages that you or a teammate already answered. Use `instructions` in the config to tell it what matters to you.

### Duplicates and completion

- The identity of a task is its Slack thread: the channel plus the timestamp of the thread's first message.
- The sweep does not add a task for a thread that already has an open task, from you or from the sweep.
- The state file remembers every thread that got a task. If you delete a task, the sweep does not add it again. A newer message in the same thread can add a new task when no task for that thread is open.
- With `complete = "created"`, the sweep checks off only tasks that it created. With `complete = "linked"`, it also checks your own tasks that contain a Slack link.
- The agent reports a task done only on evidence: you replied and the reply covers the ask, or the person who asked closed it. Silence is not evidence.
- If you move a task to another note, the sweep still finds it, as long as the note is under a `scan` path.

## Requirements

- macOS for the scheduler. The sweep itself (`run --now`) works on any system with Python and Claude Code.
- Python 3.11 or newer.
- Xcode Command Line Tools (`xcode-select --install`) to build the launcher.
- Claude Code, and a claude.ai account with the Slack connector connected.
- An Obsidian vault on disk.

## Install

1. Clone the repo into a folder that only you can write, outside `~/Documents`, `~/Desktop`, `~/Downloads` and iCloud Drive:

   ```
   git clone https://github.com/jakesciotto/obsidian-slack-sweep ~/src/obsidian-slack-sweep
   cd ~/src/obsidian-slack-sweep
   ```

2. Copy the example config and edit it. Only `[vault] path` is required:

   ```
   mkdir -p ~/.config/obsidian-slack-sweep
   cp config.example.toml ~/.config/obsidian-slack-sweep/config.toml
   ```

3. Run one sweep that prints its changes and writes nothing:

   ```
   ./bin/obsidian-slack-sweep run --now --dry-run
   ```

4. Install the launchd job:

   ```
   ./bin/obsidian-slack-sweep install
   ```

5. If the vault is in `~/Documents`, `~/Desktop`, `~/Downloads` or iCloud Drive, give the launcher Full Disk Access. `install` prints the path and the steps.

6. Check the result:

   ```
   ./bin/obsidian-slack-sweep status
   ```

## Configuration

The config is `~/.config/obsidian-slack-sweep/config.toml`. `config.example.toml` shows every key with its default. Only `[vault] path` is required. The sweep rejects unknown keys, so a typo fails loudly.

Top level: `notify` (default `true`) shows a macOS notification after a sweep that changes tasks.

### `[vault]`

| Key | Default | Meaning |
|---|---|---|
| `path` | none | The vault folder. Required. |
| `inbox` | `"Inbox.md"` | The note that gets new tasks, relative to the vault. |
| `section` | `"## Inbox"` | New tasks go at the end of this heading. The sweep adds the heading if it is missing. |
| `scan` | `["."]` | Files or folders to search for sweep tasks, so a task that you move still gets checked. |
| `exclude` | `[]` | Folders to skip, such as `["Archive", "Templates"]`. Hidden folders are always skipped. |
| `task_template` | `"- [ ] ({tag}) {text} ([thread]({link}))"` | The new task line. Fields: `{tag}`, `{text}`, `{link}`, `{date}`. `{link}` is required. |
| `completion_template` | `"[completion:: {date}]"` | Text added to a task that the sweep marks done. |
| `complete` | `"created"` | `"created"`: check only tasks the sweep made. `"linked"`: also check your tasks with a Slack link. |

### `[slack]`

| Key | Default | Meaning |
|---|---|---|
| `user_id` | `""` | Your Slack member id, such as `U012AB3CD`. If empty, the agent reads it from the Slack tool. |
| `sources` | all four | Any of `mentions`, `dms`, `threads`, `channels`. |
| `channels` | `[]` | Watched channels by name, such as `["support", "acme-shared"]`. |
| `channels_command` | `""` | A shell command that prints more channel names, one per line. |
| `team_email_domains` | `[]` | In watched channels, a reply from these email domains counts as handled. |
| `read_tools` | claude.ai Slack read tools | The only tools the agent may call. |
| `write_tools` | claude.ai Slack write tools | Tools that the run names as denied. |

### `[agent]`

| Key | Default | Meaning |
|---|---|---|
| `claude` | `"claude"` | Path to the Claude Code CLI. |
| `model` | `"claude-sonnet-5-5"` | The model for the sweep. |
| `effort` | `""` | Empty for the model default, or `low`, `medium`, `high`, `xhigh`, `max`. |
| `max_budget_usd` | `1.0` | Claude Code stops a run at this cost. |
| `timeout_minutes` | `15` | The sweep stops a run after this time. |
| `isolate` | `true` | Deny every tool except the Slack read tools, and turn off hooks and plugins. See [Isolation](#isolation). |
| `extra_args` | `[]` | More flags for `claude -p`. |
| `prompt_file` | `""` | Your own prompt template. Empty means `slack_sweep/prompt.md`. |
| `style` | see example | How the agent words a task. |
| `instructions` | `""` | What matters to you, in plain words. The agent reads it on every run. |

### `[schedule]`

| Key | Default | Meaning |
|---|---|---|
| `every_hours` | `2` | Time between sweeps. |
| `check_minutes` | `15` | How often launchd starts the job to check. |
| `idle_minutes` | `10` | Skip when the keyboard and the mouse were idle longer than this. |
| `retry_minutes` | `30` | Wait after a failed sweep. |
| `first_lookback_hours` | `24` | The window of the first sweep. |
| `max_lookback_hours` | `72` | The longest window, after a long absence. |
| `label` | `"obsidian-slack-sweep"` | The launchd label. |

## Commands

| Command | What it does |
|---|---|
| `run` | Sweeps once if you are active and a sweep is due. launchd calls this. |
| `run --now` | Sweeps once and skips the activity and interval checks. |
| `run --dry-run` | Prints the planned changes. It writes nothing to the vault or the state. |
| `install` | Builds the launcher and loads the launchd job. Run it again after a config change to the schedule. |
| `uninstall` | Unloads the job and removes the launcher. It keeps the config and the state. |
| `status` | Shows the vault access, the login, the last sweep and the open tasks. |

## Isolation

The agent reads untrusted text: any Slack message can contain instructions. So the agent must have no tool except the Slack read tools.

Your normal Claude Code setup can give it more. A permission allow rule for another MCP server applies to every `claude -p` run. For example, an allow rule for a note-writing MCP tool would let a Slack message write to your vault. Hooks and plugins in `~/.claude` also run, and they see the Slack text.

The claude.ai connectors load only with your user settings, so the sweep cannot simply skip them. In Claude Code 2.1.296, `--setting-sources ""`, `--restricted`, `--strict-mcp-config` and a separate `CLAUDE_CONFIG_DIR` all drop the connectors. So with `isolate = true` (the default), a sweep does this:

1. A preflight starts `claude -p`, reads the session's init event, and stops it before the model runs. The init event names every MCP server, including servers that are still connecting.
2. The sweep writes a deny rule for every MCP server except Slack, and for every Slack tool except the read tools. A deny rule overrides an allow rule.
3. The real run gets those deny rules through `--settings`, with `disableAllHooks` and every plugin in your user settings turned off.
4. At the real run's init event, the sweep checks every tool and server again. If one has no deny rule, the sweep stops the run before the model starts.

The run also uses these flags and variables:

- `--permission-mode dontAsk`: every tool that is not allowed is denied, with no prompt.
- `--tools ""`: no built-in tools, so no shell, no file access and no web access.
- `--allowedTools` lists only the Slack read tools. `--disallowedTools` lists the Slack write tools.
- `--max-budget-usd` stops a run at a cost limit.
- `--no-session-persistence`: Claude Code keeps no transcript of the run.
- `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` keep your CLAUDE.md files and memory out of the prompt.
- `MCP_TIMEOUT=15000` stops a slow MCP server from holding the run.

Limits: hooks and plugins from managed (organization) settings still apply, because a user cannot turn them off. Every sweep starts your MCP servers twice, once for the preflight and once for the run.

## Full Disk Access and the launcher

macOS privacy protection blocks a launchd job from reading `~/Documents`, `~/Desktop`, `~/Downloads` and iCloud Drive. Without access, the sweep logs `cannot read the vault`. Your terminal can read these folders only because you gave the terminal access.

`install` compiles a small launcher (`slack_sweep/launcher.c`) and signs it ad hoc with the hardened runtime. You give Full Disk Access to that one binary. The processes that it starts inherit the access. The launcher limits what it can start:

- It runs one command only: the Python interpreter and the entry script that were fixed when it was built.
- It removes every `DYLD_*` and `PYTHON*` variable from the environment before it starts Python.
- It passes on only its own arguments, such as `run`.

The trade-off: the code in this repo runs with Full Disk Access. Keep the clone in a folder that only you can write.

macOS ties the grant to the signature of the binary. `install` rebuilds the launcher only when the launcher source, the Python path or the repo path changes. After a rebuild, give access again. If your vault is outside the protected folders, you do not need Full Disk Access.

## Writes to the vault

- The agent returns JSON. Python checks every field before it writes.
- A permalink must be a `https://<workspace>.slack.com/archives/...` link to a message.
- Python removes URLs, wikilinks, tags, `[field:: value]` syntax and HTML from the task text.
- The agent can complete only the tasks that the sweep told it to check. A key that it invents matches no task.
- The writer reads the note, makes the change, and reads the note again before it replaces the file. If Obsidian saved the note in between, the writer starts again with the new text.

## Files

| Path | Content |
|---|---|
| `~/.config/obsidian-slack-sweep/config.toml` | Your config |
| `~/.local/state/obsidian-slack-sweep/state.json` | Threads with tasks, and the time of the last sweep |
| `~/.local/state/obsidian-slack-sweep/sweep.log` | One line per sweep |
| `~/.local/state/obsidian-slack-sweep/runs/` | One JSON record per sweep: agent output, cost, skipped items |
| `~/.local/share/obsidian-slack-sweep/obsidian-slack-sweep` | The launcher |
| `~/Library/LaunchAgents/<label>.plist` | The launchd job |

The run records contain summaries of Slack messages. They stay on your machine. The sweep keeps the last 300.

## Another Slack MCP server

The default prompt and tool names target the claude.ai Slack connector. For another server, set `read_tools` and `write_tools` to its tool names. Give a `prompt_file` that matches its search syntax. With `isolate = true`, the sweep keeps only the servers whose tools are in `read_tools`, so the other server's tools stay allowed and everything else stays denied.

## Limits

- The agent decides what needs action. It can miss an item or add noise. Use `instructions` to tune it.
- Slack search indexes a message a few seconds after it is posted. Each window starts 10 minutes before the last sweep started, to catch late messages. The thread key removes the duplicates.
- After an absence longer than `max_lookback_hours` (72 by default), the first sweep reads only the last 72 hours.
- The scheduler is macOS only. On Linux, run `obsidian-slack-sweep run` from a systemd user timer. The activity check is skipped there.

## Development

```
python3 -m unittest discover -s tests -v
```

## License

MIT
