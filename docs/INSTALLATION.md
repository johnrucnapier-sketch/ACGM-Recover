# Installation and guided start / 安装与引导启动

Claude Code Recover is installed from a reviewed local checkout. Installation is offline and user-scoped: it does not require administrator privileges, download Python dependencies, inspect recovery evidence, or select a continuation route.

Claude Code Recover 从经过检查的本地仓库安装。安装过程离线且只作用于当前用户：不需要管理员权限、不下载 Python 依赖、不扫描恢复证据，也不会替用户选择继续工作路线。

## Requirements / 要求

- Python 3.10 or newer, including `pip`;
- Git for the recovery workflow;
- a named, reviewed Claude Code Recover source tree whose `PACKAGE_MANIFEST.json` matches.

After validating the exact source manifest, bootstrap uses only the Python standard library to create a temporary pure-Python wheel, then asks pip to install that local wheel with `--no-index`, `--no-deps`, and `--no-build-isolation`. It does not require or download setuptools or the wheel package.

`pyproject.toml` retains setuptools metadata for conventional packaging tools; the reviewed bootstrap path does not invoke that build backend.

安装器固定使用 `--no-index`、`--no-deps` 和 `--no-build-isolation`，不依赖额外构建工具，也不会静默联网下载；缺少 Python、pip 或 Git 时会明确失败。

## User installation / 用户安装

Named project repository / 项目指定仓库：

```text
https://github.com/johnrucnapier-sketch/Claude-Code-Recover
```

macOS or Linux:

```bash
git clone https://github.com/johnrucnapier-sketch/Claude-Code-Recover.git
cd Claude-Code-Recover
python3 scripts/bootstrap.py --dry-run
python3 scripts/bootstrap.py
python3 -m claude_code_recover guide
```

Windows PowerShell or Command Prompt:

```powershell
git clone https://github.com/johnrucnapier-sketch/Claude-Code-Recover.git
cd Claude-Code-Recover
py -3 scripts\bootstrap.py --dry-run
py -3 scripts\bootstrap.py
py -3 -m claude_code_recover guide --no-default-sources
```

`bootstrap.py` first checks Python, pip, and every source hash listed in `PACKAGE_MANIFEST.json`. Outside a virtual environment it performs this user installation without shell interpolation:

```text
PYTHON -m pip install --no-deps --no-build-isolation --no-index --user VERIFIED_LOCAL_WHEEL
```

For a PEP 668 `EXTERNALLY-MANAGED` interpreter, bootstrap locates the marker through the selected interpreter's `sysconfig` and probes that same pip with `pip install --help`. If and only if pip advertises the option, the executable plan combines the user scope and override:

```text
PYTHON -m pip install --no-deps --no-build-isolation --no-index --user --break-system-packages VERIFIED_LOCAL_WHEEL
```

This remains a current-user install; bootstrap never uses the override without `--user`. If the marker check fails, pip help fails, or the option is absent, both dry-run and installation fail closed with `install_command_executable: false` and no wheel build or pip mutation. Inside an active virtual environment, bootstrap checks neither the base interpreter marker nor the override, omits `--user`, and installs into that environment. It never requests administrator privileges.

For every pip subprocess, bootstrap removes all inherited `PIP_*` variables, `PYTHONUSERBASE`, and `PYTHONNOUSERSITE`, then sets only `PIP_DISABLE_PIP_VERSION_CHECK=1`, `PIP_NO_INDEX=1`, and `PIP_CONFIG_FILE` to the platform null device. Environment values such as `PIP_TARGET`, `PIP_PREFIX`, `PIP_ROOT`, `PIP_USER`, or `PIP_BREAK_SYSTEM_PACKAGES` therefore cannot silently change the reviewed argv or redirect the user installation.

对于 Homebrew 等带有 `EXTERNALLY-MANAGED` 标记的 Python，安装器只会在当前 pip 明确支持时，把 `--user` 与 `--break-system-packages` 成对加入安装命令。无法确认时会在修改前停止。若这种用户级 override 安装随后失败，bootstrap 不会自动调用没有 `--user` 范围的 `pip uninstall`；它会报告 `externally_managed_no_automatic_cleanup`，等待检查和另行授权。

同版本重复执行时会从刚通过 manifest 校验的源码强制重装，避免继续运行同版本但来源不明、残缺或已被修改的旧包；发现较旧版本时必须显式增加 `--upgrade`；发现已安装版本比源码新时拒绝降级。bootstrap 不会把 pip 的隐式行为当成升级策略。

After installation it clears Python path/user-site overrides, changes to a temporary directory outside the checkout, verifies that the imported module is not the repository copy, and then verifies `python -m claude_code_recover --version`, `doctor --no-default-sources`, `guide --no-default-sources`, canonical distribution metadata, and the legacy transition module alias. It does not depend on the console script being present in `PATH`.

安装完成后，它会验证版本、`doctor` 与 `guide`，不会因为用户级脚本目录没有加入 `PATH` 而误报安装失败。

## Agent-assisted clone and install / Agent 代为下载和安装

An agent may complete clone and installation in one authorized task, but only when the user's authorization explicitly covers both actions. A safe instruction is:

> Clone only `https://github.com/johnrucnapier-sketch/Claude-Code-Recover.git` into a new directory. Confirm the owner/repository and current commit, read `SECURITY.md` and `AGENTS.md`, run `python scripts/bootstrap.py --dry-run`, and if the manifest and prerequisites pass, run `python scripts/bootstrap.py`. Show me the final `guide` report. Do not run `discover`, inspect account data, infer a model/provider, choose a route, or read any transcript until I explicitly confirm the next step.

Repository instruction files are not an execution trigger. Codex builds its instruction
chain when a task starts, so an already-running task must explicitly read the newly
cloned `AGENTS.md`; Claude Code loads the thin `CLAUDE.md`, which imports that same
contract. Other agents must use the README instruction above. Git clone itself never
runs bootstrap code.

如果用户在同一次授权中已经明确允许“下载指定仓库并执行本地安装”，Agent 可以在 dry-run 和 manifest 校验通过后继续安装，不必人为拆成很多操作。但安装结束必须停在 `selection_required`；不能把“找到 Claude/Codex CLI”当成路线选择依据。

For a release tag, a higher-assurance agent should clone that exact tag or commit instead of an unpinned moving branch. The manifest detects accidental mismatch; it is not a digital signature and cannot authenticate a maliciously replaced repository.

## Route confirmation / 路线确认

Run one of the following only after the user chooses it:

```bash
python -m claude_code_recover guide --route claude-new-account
python -m claude_code_recover guide --route claude-compatible-api
python -m claude_code_recover guide --route agent-neutral
```

The CLI records `explicit_cli_argument`, not `user_confirmed`. The operating Agent must display the choice and obtain user confirmation before running `discover` or any deeper recovery command.

CLI 只证明收到了显式参数，不证明账号身份，也不证明参数一定由用户本人输入。它不会读取 token、Cookie、OAuth、账号缓存或显示模型名称来推断路线。

## Windows boundary / Windows 边界

The RC's bootstrap, package installation, module entrypoint, `--version`, `doctor`, and `guide` are designed to run on Windows. The secure recovery core is **not yet implemented for native Windows**. `doctor` and `guide` therefore report `recovery_runtime_supported: false`, `guide` does not emit `discover` or `build` commands there, and direct `discover`, `inspect`, `build`, or `verify` calls fail before source access with `recovery_runtime_not_supported_on_platform`.

当前 RC 在 Windows 上只支持安全下载、安装和引导检查。核心 bundle 流程暂不支持 native Windows，原因包括：Windows pipe selector、DACL/权限语义、reparse point/路径边界，以及原子 no-replace 发布尚未形成等价安全实现。不得通过跳过权限检查或普通重命名来伪装成已经支持。

Windows users can install and review the plan now, but should run core recovery on a supported macOS/Linux environment against a safely mounted copy until a dedicated Windows security port passes E2E tests.

## Upgrade / 升级

Review the new tag or commit, update the checkout, then run:

```bash
python scripts/bootstrap.py --dry-run --upgrade
python scripts/bootstrap.py --upgrade
```

The installer never downloads source updates itself. It cannot silently replace a reviewed checkout with a newer remote version.

### RC1 name migration / RC1 名称迁移

If `acgm-recover` RC1 is installed, RC3 returns `MIGRATION_REQUIRED` before any PEP 668 capability probe or pip mutation—even when `--upgrade` is supplied. The response contains a non-executable plan whose uninstall and rerun steps are all marked unauthorized. A user must separately review and authorize removal of the old distribution, then rerun bootstrap from the verified RC3 tree. Cross-distribution uninstall is deliberately not hidden inside `--upgrade` because the two distributions can own overlapping compatibility files.

旧 checkout 地址 `https://github.com/johnrucnapier-sketch/ACGM-Recover` 只用于识别 RC1 来源。不要继续从旧地址安装 RC3。用户另行授权卸载 RC1 后，从 RC3 fresh install 安装的新 distribution 会继续提供旧 CLI `acgm-recover` 与旧 module `python -m acgm_recover` 作为过渡入口。

## Uninstall / 卸载

```bash
python -m pip uninstall claude-code-recover
```

Uninstalling the Python package does not delete the downloaded repository, surviving projects, Claude data, or any recovery bundle. Remove those separately only with explicit user approval.

卸载 Python 包不会删除源码仓库、原项目、Claude 数据或已生成的恢复包；这些内容只能在用户另行明确授权后处理。
