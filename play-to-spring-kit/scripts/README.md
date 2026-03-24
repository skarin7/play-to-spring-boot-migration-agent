# Migration orchestrator (`migration_orchestrator.py`)

Python **stdlib-only** driver for the Play → Spring pipeline: `dev-toolkit` **`migrate-app`** per **migration unit** (folder slices under `app/` via **`--path-prefix`**, discovered automatically) or, in legacy mode, per **semantic layer** (`--layer`), then **`mvn compile`**, optional **`cursor-agent`** for compile fixes, **`source_inventory`** / **`migration_verification`** counts.

**State:** a single **`migration-status.json`** (no `pipeline_state.json`). Default path is **`<spring-repo>/migration-status.json`**, where **`spring-repo`** is resolved after **workspace preparation** (see below). Override with `--status-file` or `MIGRATION_STATUS_FILE`.

**First steps (automatic):** each run, unless you pass **`--skip-build-toolkit`**:

1. Runs **`mvn package -DskipTests`** in **`java-dev-toolkit/`** (default: directory **next to** **`play-to-spring-kit/`** in the monorepo; override with **`JAVA_DEV_TOOLKIT_ROOT`** or **`--toolkit-root`**).
2. Copies the shaded **`dev-toolkit-*.jar`** into **`play-to-spring-kit/lib/`**.
3. Runs **`setup.sh`**, which **prepares the workspace** for **`--play-repo`**: Spring project directories, **`workspace.yaml`**, Cursor skills and kit files under **`<play-repo>/.cursor/`**, and copies the JAR from **`lib/`** into the play repo. This step is **idempotent**.

With **`--skip-build-toolkit`**, you must already have a **`*.jar`** in **`play-to-spring-kit/lib/`** (for example after a manual **`mvn package`**). Use that flag on **re-runs** when you have **not** changed **`java-dev-toolkit`**—the default is to **`mvn package`** every orchestrator invocation so a single command always stays self-contained.

**Where to run from:** use the kit directory as your shell cwd, e.g. **`cd …/play-to-spring-kit`**, then **`python3 scripts/migration_orchestrator.py --play-repo ../your-play-app`**. The kit root (bootstrap script, **`lib/`**) is found via the script file path (**`__file__`**), not cwd. Relative **`--play-repo`** / **`--workspace`** values are resolved against **cwd**, so paths like **`../cms-content-service`** are correct when you launch from the kit directory.

**Recommended from the monorepo root:** **`./start_upgrade.sh --play-repo …`** — uses **`play-to-spring-kit/.venv`** and **`scripts/requirements-venv.txt`** so optional deps (e.g. **`pyhocon`**) install without touching system Python. **`MIGRATION_SKIP_VENV_SYNC=1`** skips **`pip install`** on each run.

## Requirements

- Python **3.10+** (stdlib is enough for the orchestrator itself)
- **`java`**, **`mvn`** on **`PATH`** (Maven required for the default **build-toolkit** step; omit if you use **`--skip-build-toolkit`** and keep a JAR in **`lib/`**)
- **`dev-toolkit-*.jar`** ends up in **`play-to-spring-kit/lib/`** via the automatic build, or place it there yourself when using **`--skip-build-toolkit`**
- **`cursor-agent`** on `PATH` if using LLM fixes (see Cursor docs for install)
- **`pyhocon`** when using **`--export-play-conf`** or **`conf_to_application_properties.py`** — supplied automatically if you use **`start_upgrade.sh`** / **`requirements-venv.txt`** in **`play-to-spring-kit/.venv`**

## Play `application.conf` → Spring `application.properties`

Play uses HOCON (`conf/application.conf`, often with `include`). Spring expects `src/main/resources/application.properties` (flat keys). The kit ships **`scripts/conf_to_application_properties.py`**, which resolves includes, flattens nested objects to dot keys, and escapes values for `.properties`.

Prefer the kit venv (created by repo **`start_upgrade.sh`**, or manually):

```bash
cd /path/to/play-to-spring-kit
python3 -m venv .venv
.venv/bin/python3 -m pip install -r scripts/requirements-venv.txt
```

On system Python only (PEP 668 may require **`--break-system-packages`**):

```bash
cd /path/to/play-to-spring-kit
python3 -m pip install -r scripts/requirements-conf.txt --user --break-system-packages
```

Standalone:

```bash
python3 scripts/conf_to_application_properties.py \
  -i /path/to/play-project/conf/application.conf \
  -o /path/to/spring-project/src/main/resources/application.properties \
  --strip-prefix akka.
```

**Orchestrator:** pass **`--export-play-conf`** (or **`MIGRATION_EXPORT_PLAY_CONF=1`**) to run the same conversion after `setup.sh` when **`spring_repo`** is known. **`akka.`** keys are stripped by default; add more prefixes with **`--conf-strip-prefix play.`** (repeatable). If `pyhocon` is missing, the step logs a warning and continues.

You still need to **rename/map** keys for Spring (for example `mongodb.uri` → `spring.data.mongodb.uri`).

## Cursor / model

1. Create a **Cursor User API Key** (dashboard → Integrations → User API Keys, `sk_...`).
2. `export CURSOR_API_KEY="sk_..."`

**Model** (`cursor-agent --model`; see [Cursor CLI parameters](https://cursor.com/docs/cli/reference/parameters)):

| Precedence | Source |
|------------|--------|
| 1 (highest) | `--cursor-model` |
| 2 | `CURSOR_MODEL` or `MIGRATION_CURSOR_MODEL` |
| 3 | `migration-status.json` → `autonomous.cursor_model` |
| 4 (default) | **`composer-2`** |

Confirm slugs with `cursor-agent --help` for your CLI version.

**Headless agent** (what the script runs):

```bash
cursor-agent -p --output-format json --api-key "$CURSOR_API_KEY" --model composer-2 "<prompt>"
```

Optional: `CURSOR_AGENT_TIMEOUT_SEC` (default **1800**) per agent invocation.

## Logging & real-time `cursor-agent` progress

All orchestrator logs go to **stderr** with timestamps (`HH:MM:SS LEVEL [migration] …`).

- **`cursor-agent` stdout** → logged as **`INFO [cursor-agent stdout] …`** (line by line as it arrives).
- **`cursor-agent` stderr** → logged as **`WARNING [cursor-agent stderr] …`**.
- If the agent runs longer than **30s** without exiting, an **`INFO`** heartbeat explains it is still running (pid + elapsed time).

Use **`-v`** / **`--verbose`** or **`MIGRATION_VERBOSE=1`** for **DEBUG** (includes redacted argv dump).

```bash
MIGRATION_VERBOSE=1 python3 scripts/migration_orchestrator.py --play-repo ../my-play-app
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `JAVA_DEV_TOOLKIT_ROOT` | Path to the **`java-dev-toolkit`** Maven project when not using the default sibling of **`play-to-spring-kit/`** |
| `PLAY_REPO` | Default `--play-repo` (Play project root) |
| `SPRING_REPO` | Optional explicit Spring root (skips `workspace.yaml` / `spring-<basename>` resolution) |
| `MIGRATION_STATUS_FILE` | Explicit status file path if set (only when `--status-file` is omitted) |
| `CURSOR_API_KEY` | Required for `cursor-agent` (omit with `--no-cursor`) |
| `MIGRATION_VERBOSE` | Set to `1` / `true` / `yes` for DEBUG logging (same idea as `-v`) |
| `CURSOR_MODEL` / `MIGRATION_CURSOR_MODEL` | Model slug |
| `MAX_RETRIES_PER_LAYER` | Default 5 |
| `MAX_TOTAL_LLM_CALLS` | Default 50 (whole run) |
| `MAX_ERRORS_TO_SEND_LLM` | Default 10 (per prompt) |
| `MAX_FILES_PER_FIX` | Default 3 (prompt instruction only) |
| `TIMEOUT_PER_LAYER_MINS` | Default 30 (wall clock per layer / per migration slice) |
| `MAX_FILES_PER_CURSOR_SESSION` | Default 10 (batch errors by file) |
| `MIGRATION_USE_SEMANTIC_LAYERS` | Set to `1` / `true` / `yes` for legacy **six-layer** `migrate-app --layer` loop (same as CLI `--use-semantic-layers`) |
| `MIGRATION_EXPORT_PLAY_CONF` | Set to `1` / `true` / `yes` to run HOCON → `application.properties` export (same as `--export-play-conf`) |
| `MIGRATION_SKIP_VENV_SYNC` | Set to `1` when using **`./start_upgrade.sh`**: do not run **`pip install`** (faster re-runs if **`play-to-spring-kit/.venv`** is already provisioned) |

## Paths (after workspace preparation)

Preparation writes **`workspace.yaml`** under the **workspace** directory (default: **parent of the play repo**) with absolute **`spring_repo`** and **`play_repo`**.

Optional key (read by the orchestrator, not required for `setup.sh` to write it): **`migration_unit_root`** — path relative to Play **`app/`** that forces the **split parent** when auto-discovery is wrong (same semantics as choosing that folder’s immediate subdirs as units).

The orchestrator then resolves:

1. **`--spring-repo`** or **`SPRING_REPO`** — if set, used as-is.
2. Else **`spring_repo`** from **`<workspace>/workspace.yaml`**.
3. Else **`<workspace>/spring-<play-dir-name>`**, or **`<workspace>/<--spring-name>`** if you pass **`--spring-name`**.

**`--workspace`** / **`--spring-name`** — only when you use a non-default layout; both are passed through to the same install step the script runs at the start.

## CLI usage

Minimal (**only `--play-repo`**; workspace prep runs automatically). From inside the kit:

```bash
cd /path/to/play-to-spring-kit
python3 scripts/migration_orchestrator.py --play-repo ../cms-content-service
```

Or with an absolute play path from any cwd:

```bash
python3 /path/to/play-to-spring-kit/scripts/migration_orchestrator.py \
  --play-repo /path/to/cms-content-service
```

Custom workspace / Spring folder name:

```bash
python3 scripts/migration_orchestrator.py \
  --play-repo /path/to/play-project \
  --workspace /path/to/workspace-dir \
  --spring-name my-spring-app
```

Explicit overrides:

```bash
python3 scripts/migration_orchestrator.py \
  --play-repo /path/to/play-project \
  --spring-repo /path/to/spring-project \
  --status-file /path/to/spring-project/migration-status.json
```

**Status file precedence:** `--status-file` → `MIGRATION_STATUS_FILE` → **`<spring-repo>/migration-status.json`**.

Common flags:

- `--batch-size N` — passed to `migrate-app`
- `--no-cursor` — compile only; exit **2** if errors repeat (stuck detection) without improvement
- `--fail-fast` — exit on first layer `loop_detected` / `max_retries` / `timeout`
- `--dry-run` — print actions, no subprocess side effects (limited)
- `--skip-build-toolkit` — skip **`mvn package`**; require a **`*.jar`** in **`play-to-spring-kit/lib/`**
- `--toolkit-root DIR` — **`java-dev-toolkit`** Maven project path (default: sibling of the kit; see **`JAVA_DEV_TOOLKIT_ROOT`**)
- `--skip-inventory` — do not scan Play `app/` for `source_inventory`
- `--refresh-inventory` — recompute `source_inventory` and **rediscover** `migration_units` even if present
- `--use-semantic-layers` — legacy: loop **model → … → other** using `migrate-app --layer` only (no path-prefix slicing)
- `--export-play-conf` — after setup, write Spring `application.properties` from Play `conf/application.conf` (needs `pyhocon`; non-fatal if absent)
- `--conf-strip-prefix PREFIX` — with `--export-play-conf`, omit extra HOCON key prefixes (repeatable); `akka.` is always omitted

## Migration units (default)

By default the orchestrator discovers **folder-based units** under Play **`app/`**:

1. Prefer a directory **`P`** that has a child named **`controllers`** or **`controller`**; units are **`P`’s immediate subdirectories** (each becomes a `path_prefix` relative to `app/`). If **`P`** itself contains loose `*.java` files, **`P`** is also a unit.
2. Else descend narrow chains until a directory has **≥ 3** subdirectories, then use each subdirectory (with Java) as a unit.
3. Else a single unit for the whole **`app/`** (`path_prefix` empty).

Each unit runs **`migrate-app --path-prefix <prefix>`** (no `--layer` unless you use semantic mode). Per-file transform semantics stay **`LayerDetector`** inside the JAR.

## Init gate

If `initialize.status` in JSON is not **`done`**, the script exits **3** and **does not** modify the file. Generate `pom.xml`, `Application.java`, and `application.properties` first (builder / agent).

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Finished (see `migration_verification` in JSON) |
| 1 | Unexpected error |
| 2 | Stuck compile with `--no-cursor` (error count not decreasing for 3 runs) |
| 3 | Initialize not done |
| 4 | `budget_exhausted` (`total_llm_calls` ≥ max) |
| 5 | `--fail-fast` stopped on slice / layer failure |

## Resuming

Re-run the same command. Slices in **`migration_units`** (or layers in legacy mode) with `status: done` are skipped. State is written after major steps (atomic replace).

## Overnight run

```bash
nohup python3 /path/to/play-to-spring-kit/scripts/migration_orchestrator.py \
  --play-repo "$PLAY_REPO" \
  > orchestrator.log 2>&1 &
echo $! >> orchestrator.log
tail -f orchestrator.log
```

## Gitignore (Spring repo)

Ignore generated diagnostics:

```
.migration/
```

The script writes `errors-<slice-id>.json` (or legacy `errors-<layer>.json`) under `<spring-repo>/.migration/`.

## `migration-status.json` extensions

The script merges backward-compatible fields:

- **`autonomous`**: `total_llm_calls`, `max_total_llm_calls`, `cursor_model`, `max_files_per_cursor_session`, `last_errors_path`
- **Per-layer** (in addition to existing keys): `retry_count`, `llm_calls`, `errors_history`, `failure_reason`, optional `compile_error_counts` (no-cursor stuck detection)
- **`failed_layers`**: append-only summary entries on terminal failure
- **`source_inventory`**: Play `app/**/*.java` counts by layer (LayerDetector rules)
- **`migration_units`**: ordered list of `{ id, path_prefix, java_file_count, status, … }` for folder-based runs (`null` in legacy semantic-layer-only mode)
- **`migration_verification`**: after all slices / layers processed, Spring vs Play file counts

Legacy **semantic** layer order (only with `--use-semantic-layers`): **model → repository → manager → service → controller → other**.

**`dev-toolkit migrate-app`:** optional **`--path-prefix`** (repeat or comma-separated) scopes sources under `app/`; combine with **`--layer`** only if you need both filters.

## Session resume (`cursor-agent`)

Not implemented in v1. For long-lived fixes, see `cursor-agent ls` / `cursor-agent resume` in Cursor docs; future versions may persist `session_id`.
