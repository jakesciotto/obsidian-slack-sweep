# Configuration

The config is `~/.config/obsidian-slack-sweep/config.toml`. `config.example.toml` shows every key with its default. Only `[vault] path` is required. The sweep rejects unknown keys, so a typo fails loudly.

Top level: `notify` (default `true`) shows a macOS notification after a sweep that changes tasks.

## `[vault]`

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

## `[slack]`

| Key | Default | Meaning |
|---|---|---|
| `user_id` | `""` | Your Slack member id, such as `U012AB3CD`. If empty, the agent reads it from the Slack tool. |
| `sources` | all five | Any of `mentions`, `dms`, `threads`, `channels`, `keywords`. |
| `channels` | `[]` | Watched channels by name, such as `["support", "acme-shared"]`. |
| `channels_command` | `""` | A shell command that prints more channel names, one per line. |
| `keywords` | `[]` | Words searched in every channel, such as your account names. A keyword with spaces becomes a quoted phrase. |
| `keywords_command` | `""` | A shell command that prints more keywords, one per line. |
| `team_email_domains` | `[]` | In watched channels, a reply from these email domains counts as handled. |
| `read_tools` | claude.ai Slack read tools | The only tools the agent may call. |
| `write_tools` | claude.ai Slack write tools and `slack_read_canvas` | Tools that the run names as denied. |

## `[agent]`

| Key | Default | Meaning |
|---|---|---|
| `claude` | `"claude"` | Path to the Claude Code CLI. |
| `model` | `"claude-sonnet-5-5"` | The model for the sweep. |
| `effort` | `""` | Empty for the model default, or `low`, `medium`, `high`, `xhigh`, `max`. |
| `max_budget_usd` | `1.0` | Claude Code stops a run at this cost. |
| `timeout_minutes` | `15` | The sweep stops a run after this time. |
| `isolate` | `true` | Deny every tool except the Slack read tools, and turn off hooks and plugins. See [Safety](../README.md#safety). |
| `extra_args` | `[]` | More flags for `claude -p`. |
| `prompt_file` | `""` | Your own prompt template. Empty means `slack_sweep/prompt.md`. |
| `style` | see example | How the agent words a task. |
| `instructions` | `""` | What matters to you, in plain words. The agent reads it on every run. |

## `[schedule]`

| Key | Default | Meaning |
|---|---|---|
| `every_hours` | `2` | Time between sweeps. |
| `check_minutes` | `15` | How often launchd starts the job to check. |
| `idle_minutes` | `10` | Skip when the keyboard and the mouse were idle longer than this. |
| `retry_minutes` | `30` | Wait after a failed sweep. |
| `first_lookback_hours` | `24` | The window of the first sweep. |
| `max_lookback_hours` | `72` | The longest window, after a long absence. |
| `label` | `"obsidian-slack-sweep"` | The launchd label. |
