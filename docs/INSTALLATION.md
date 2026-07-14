# Installation and guided start / 安装与引导启动

ACGM Recover is installed from a reviewed local checkout. Installation is offline and user-scoped: it does not require administrator privileges, download Python dependencies, inspect recovery evidence, or select a continuation route.

ACGM Recover 从经过检查的本地仓库安装。安装过程离线且只作用于当前用户：不需要管理员权限、不下载 Python 依赖、不扫描恢复证据，也不会替用户选择继续工作路线。

## Requirements / 要求

- Python 3.10 or newer, including `pip`;
- Git for the recovery workflow;
- an official, reviewed ACGM Recover source tree whose `PACKAGE_MANIFEST.json` matches.

After validating the exact source manifest, bootstrap uses only the Python standard library to create a temporary pure-Python wheel, then asks pip to install that local wheel with `--no-index`, `--no-deps`, and `--no-build-isolation`. It does not require or download setuptools or the wheel package.

`pyproject.toml` retains setuptools metadata for conventional packaging tools; the reviewed bootstrap path does not invoke that build backend.

安装器固定使用 `--no-index`、`--no-deps` 和 `--no-build-isolation`，不依赖额外构建工具，也不会静默联网下载；缺少 Python、pip 或 Git 时会明确失败。

## User installation / 用户安装

Official repository / 官方仓库：

```text
https://github.com/johnrucnapier-sketch/ACGM-Recover
```

macOS or Linux:

```bash
git clone https://github.com/johnrucnapier-sketch/ACGM-Recover.git
cd ACGM-Recover
python3 scripts/bootstrap.py --dry-run
python3 scripts/bootstrap.py
python3 -m acgm_recover guide
```

Windows PowerShell or Command Prompt:

```powershell
git clone https://github.com/johnrucnapier-sketch/ACGM-Recover.git
cd ACGM-Recover
py -3 scripts\bootstrap.py --dry-run
py -3 scripts\bootstrap.py
py -3 -m acgm_recover guide --no-default-sources
```

`bootstrap.py` first checks Python, pip, and every source hash listed in `PACKAGE_MANIFEST.json`. Outside a virtual environment it performs this user installation without shell interpolation:

```text
PYTHON -m pip install --user --no-deps --no-build-isolation --no-index VERIFIED_LOCAL_WHEEL
```

Inside an active virtual environment, bootstrap intentionally omits `--user` and installs into that environment. It never requests administrator privileges.

同版本重复执行时会从刚通过 manifest 校验的源码强制重装，避免继续运行同版本但来源不明、残缺或已被修改的旧包；发现较旧版本时必须显式增加 `--upgrade`；发现已安装版本比源码新时拒绝降级。bootstrap 不会把 pip 的隐式行为当成升级策略。

After installation it clears `PYTHONPATH`/`PYTHONHOME`, changes to a temporary directory outside the checkout, verifies that the imported module is not the repository copy, and then verifies `python -m acgm_recover --version`, `doctor --no-default-sources`, and `guide --no-default-sources`. It does not depend on the console script being present in `PATH`.

安装完成后，它会验证版本、`doctor` 与 `guide`，不会因为用户级脚本目录没有加入 `PATH` 而误报安装失败。

## Agent-assisted clone and install / Agent 代为下载和安装

An agent may complete clone and installation in one authorized task, but only when the user's authorization explicitly covers both actions. A safe instruction is:

> Clone only `https://github.com/johnrucnapier-sketch/ACGM-Recover.git` into a new directory. Confirm the owner/repository and current commit, read `SECURITY.md` and `AGENTS.md`, run `python scripts/bootstrap.py --dry-run`, and if the manifest and prerequisites pass, run `python scripts/bootstrap.py`. Show me the final `guide` report. Do not run `discover`, inspect account data, infer a model/provider, choose a route, or read any transcript until I explicitly confirm the next step.

如果用户在同一次授权中已经明确允许“下载官方仓库并执行本地安装”，Agent 可以在 dry-run 和 manifest 校验通过后继续安装，不必人为拆成很多操作。但安装结束必须停在 `selection_required`；不能把“找到 Claude/Codex CLI”当成路线选择依据。

For a release tag, a higher-assurance agent should clone that exact tag or commit instead of an unpinned moving branch. The manifest detects accidental mismatch; it is not a digital signature and cannot authenticate a maliciously replaced repository.

## Route confirmation / 路线确认

Run one of the following only after the user chooses it:

```bash
python -m acgm_recover guide --route claude-new-account
python -m acgm_recover guide --route claude-compatible-api
python -m acgm_recover guide --route agent-neutral
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

## Uninstall / 卸载

```bash
python -m pip uninstall acgm-recover
```

Uninstalling the Python package does not delete the downloaded repository, surviving projects, Claude data, or any recovery bundle. Remove those separately only with explicit user approval.

卸载 Python 包不会删除源码仓库、原项目、Claude 数据或已生成的恢复包；这些内容只能在用户另行明确授权后处理。
