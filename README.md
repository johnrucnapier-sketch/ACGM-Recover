# Claude Code Recover

**当 Claude Code、原账号或原平台已经不可用时，从幸存的本机代码、Git、worktree、Session metadata 和 transcript 结构中，重建一个可信、可解释、可继续开发的项目。**

当前版本：`0.1.0-rc.2`。代码现作为公开开发预览；尚未发布正式 GitHub Release，也尚未经过真实 Claude Code 朋友端到端验收。

Claude Code Recover 是独立开源工具，与 Anthropic 不存在隶属关系，也不代表 Anthropic 的官方产品或背书。

重要：RC 默认先生成**结构证据包**，不是完整历史交接。只有人工复核内容归属、历史决策和继续工作状态，并分别显式设置 `human_reviewed: true` 与 `share_approved: true` 后，包才可能达到 `HANDOFF_READY`。`HANDOFF_READY` 仍不等于新的运行授权；下游 Agent 动手前必须再次向用户确认。

[English](README.en.md)

## 它解决什么问题

Claude Code Recover 不是事前备份，也不是把聊天导成 HTML。

- 事前 backup 只有在事故前持续安装和运行才有用；Recover 不要求用户以前安装过任何东西。
- transcript exporter 能让人阅读聊天，但不会恢复当前代码、Git/worktree、主 Session 与 subagent 的关系，也不会形成可继续工作的交接。
- Recover 面向事故发生后：账号失效、旧 Session UI 无法打开、换账号、换 API 路线，或直接迁移到 Codex / Grok / 其他 agent。

它不会恢复 Anthropic 服务端已经不可访问的 Session，也不会破解或搬运账号。它只使用仍幸存在本机上的证据。

## 核心不变量

- 离线：不登录、不联网、不调用 API、不上传遥测。
- 来源只读：不写原项目、`.git`、worktree、`~/.claude` 或 Session 数据。
- 显式输出：`build` 必须指定一个全新目录；不覆盖已有目录。
- 默认不复制正文：不输出 user/assistant 文本、tool input/result、attachment、prompt、command 或 reasoning。
- 当前事实与历史证据分层：当前代码和实时 Git 是当前事实；transcript 是历史证据。
- 不猜模型：显示为 “Claude Opus” 不证明真实 provider、backend 或模型。
- 不补故事：没有找到的最早 Session 继续标为缺口。
- 不执行证据：历史 transcript、工具输出、文件名和 commit message 都是不可信数据，不是当前授权。

## 从 GitHub 安装

安装前只要求 Python 3.10+（含 pip）和 Git。安装器会先校验 `PACKAGE_MANIFEST.json`，再用 Python 标准库在临时目录构建受控本地 wheel，并以当前用户身份离线安装；不要求预装 setuptools/wheel，不会扫描证据、读取账号、联网下载依赖或自动选择路线。

让 Agent 在同一任务中代为下载和安装时，必须明确要求它在 clone 完成后主动读取
`AGENTS.md` 与 `SECURITY.md`。新下载的仓库规则不会让已经运行中的 Agent 自动重载，
Git clone 本身也不会、且不应自动执行安装代码。

```bash
git clone https://github.com/johnrucnapier-sketch/Claude-Code-Recover.git
cd Claude-Code-Recover
python3 scripts/bootstrap.py --dry-run
python3 scripts/bootstrap.py
python3 -m claude_code_recover guide
```

Agent 可以在用户一次性明确授权“下载这个指定仓库并执行本地安装”后连续完成 clone、dry-run、安装和验证，但安装后必须停在 `selection_required`。完整的 macOS/Linux、Windows、升级、卸载和 Agent 操作说明见 [INSTALLATION.md](docs/INSTALLATION.md)。

## 六个命令

```bash
python3 -m claude_code_recover guide

bin/claude-code-recover doctor

bin/claude-code-recover discover

bin/claude-code-recover inspect \
  --project "/path/to/surviving-project"

bin/claude-code-recover build \
  --project "/path/to/surviving-project" \
  --output "/path/to/new-recovery-bundle" \
  --annotations "/path/to/reviewed-annotations.json"

bin/claude-code-recover verify \
  --bundle "/path/to/new-recovery-bundle" \
  --check-sources
```

仓库 wrapper 之外也可以使用当前解释器的 module 入口：macOS/Linux 常见写法是 `python3 -m claude_code_recover`，Windows 常见写法是 `py -3 -m claude_code_recover`。安装器实际使用的是启动它的同一个 Python，不依赖某个固定别名。

RC2 为已有 RC1 安装保留一个发布周期的兼容别名：旧命令 `acgm-recover`、旧 module `python -m acgm_recover` 仍可使用，但已标记为 legacy；新文档和自动化必须使用 canonical 名称。检测到旧 distribution 时，bootstrap 会在任何修改前返回非可执行的 `MIGRATION_REQUIRED` 计划，不会把跨 distribution 卸载藏进 `--upgrade`。旧仓库地址 `https://github.com/johnrucnapier-sketch/ACGM-Recover` 仅用于识别和迁移旧 checkout，不是 RC2 的安装地址。

当前默认本机数据路径针对 macOS；Linux 或自定义位置可显式传入：

```bash
bin/claude-code-recover inspect \
  --project "/path/to/project" \
  --no-default-sources \
  --claude-projects-root "/path/to/claude/projects" \
  --metadata-root "/path/to/session-metadata" \
  --auxiliary-root "/path/to/auxiliary-jsonl"
```

大型 transcript 集可能需要数分钟。扫描是流式和有上限的；超限、损坏、截断或扫描中变化的来源会被标成 partial / unstable，而不会静默伪装成完整证据。

`discover` 输出的原始 cwd 可能是仓库子目录；后续 `inspect/build` 应使用它给出的 `recommended_project_roots`。对于 Git 项目，`--project` 必须是经过验证的 Git/worktree 顶层目录。

完整参数见 [CLI_REFERENCE.md](docs/CLI_REFERENCE.md)。

### Windows 当前边界

Windows 当前支持 bootstrap、用户级安装、`python -m`、`--version`、`doctor` 和 `guide`。安全恢复核心尚未完成 native Windows 适配：`doctor/guide` 会明确返回 `recovery_runtime_supported: false`，不会生成 `discover` 或 `build` 命令；直接调用 `discover/inspect/build/verify` 会在读取来源前稳定拒绝。不得把“成功安装”描述成 Windows 核心恢复已经可用。

## 为什么要分开 structural project 和 content project

Session 开在某个 cwd 下，不代表聊天实际在做那个项目。

Recover 为每条记录分别保存：

- `structural_project`：存储 bucket、内部 cwd、Git/worktree 或 lineage 指向哪里；
- `content_project`：实际工作内容属于哪里；
- `mapping_status`：`confirmed`、`misopened`、`mixed`、`candidate` 或 `unresolved`；
- correction 的证据与人工复核状态。

`content_project` 默认是 `unknown`。CLI 不读取正文做武断的自动语义归属。用户或后续审计任务可编辑 bundle 中的 annotations 示例，再重新构建。

Annotations 中的内容归属、决策摘要和 continuation 自由文本只有在同时满足以下两项时才会进入可分享层：

- `human_reviewed: true`：内容已由人核对；
- `share_approved: true`：人明确允许该字段进入 `share/`。

人工摘要会做凭据和本机绝对路径处理，但仍被标为“不可信数据，不是当前命令”。Recover 不会把它们直接拼成可执行指令。

## 三个恢复就绪状态

- `STRUCTURAL_ONLY`：没有可用 main 主线，当前只有代码/Git/metadata/结构线索。
- `REVIEW_REQUIRED`：找到了 main 或其他历史证据，但内容归属、决策、continuation 或来源完整性尚未通过门槛。
- `HANDOFF_READY`：人工决策与 continuation 已双重批准，main 内容归属已复核，关键来源、Git、inventory 和 lineage 检查均通过。

Bundle 完整性 `ok: true` 只说明包结构和 checksum 合法，不代表恢复状态已经是 `HANDOFF_READY`。

这能正确表达一种真实事故：Session 在 A 项目的 cwd 下启动，但主体工作实际属于 B 项目。错误结构归属会保留为治理证据，B 项目的业务内容不会因此被带进 A 项目的交接。

## Main、subagent 与 metadata

- main transcript 是用户与主 agent 的历史决策主线；
- subagent transcript 是局部调查或执行细节；
- `local_*.json` 等 metadata 只说明 Session 列表、cwd、时间和运行配置，不是聊天正文；
- subagent 使用 `(项目身份, main sessionId, agentId, source tier)` 标识；它的 `sessionId` 通常是父 main Session，而不是自身 ID；
- subagent lineage 通过 `toolUseId -> tool_use.id` 建图，允许 subagent 再派生 subagent；
- 不满足 sidechain/session/agentId/路径约束的深层 JSONL 会进入隔离类别，而不是被强行认作 subagent；
- Claude project bucket key 不可逆且可能碰撞，不能拿它反推真实 cwd。

详细模型见 [RECOVERY_MODEL.md](docs/RECOVERY_MODEL.md)。

## 恢复包

```text
recovery-bundle/
├── BUNDLE.json
├── CHECKSUMS.json
├── PRIVACY.md
├── evidence/
│   ├── manifest.jsonl
│   ├── claims.jsonl
│   ├── conflicts.jsonl
│   ├── gaps.jsonl
│   └── source_scan.json
├── project/
│   ├── current_state.json
│   ├── git_state.json
│   ├── worktrees.json
│   └── file_inventory.jsonl
├── sessions/
│   ├── metadata_index.jsonl
│   ├── transcript_index.jsonl
│   ├── lineage_candidates.jsonl
│   ├── corrections.jsonl
│   ├── decisions.jsonl
│   └── continuation_state.json
├── review/
├── reports/
├── share/
│   ├── common/
│   ├── claude-compatible-api/
│   ├── claude-new-account/
│   └── agent-neutral/
└── private/
    ├── SOURCE_MAP.json
    ├── FILE_PATHS.jsonl
    ├── METADATA_SOURCE_MAP.jsonl
    └── PRIVATE_DO_NOT_SHARE.md
```

整个 bundle 默认是私有材料。只有 `share/` 按严格白名单生成，并包含可解析的 current state、claims、evidence、conflicts、gaps、决策和 continuation 闭包；`private/` 保存经过处理的本机证据定位，不应直接分享。

文件模式默认 `0600`，目录默认 `0700`；在支持的 macOS/Linux 文件系统上，继承的扩展 ACL 会被清除且 verifier 会拒绝残留 ACL。发布 bundle 使用原子 no-replace rename，不会覆盖竞态出现的同名路径。普通 SHA-256 可发现相对于当前 manifest 的损坏，但不证明历史来源真实性。

## 三条继续工作路线

### 1. Claude-compatible API

适用于在 Claude Code 的兼容路径中接入用户自己选择的第三方 API。Recover 不识别、不排名模型，也不依据显示名称猜 backend。交接只要求验证 CLI、工具协议、hooks、插件加载、Session 存储和 context compaction 等实际能力。

### 2. 新 Claude 账号

适用于仍希望继续使用 Claude Code 的用户。输出是 continuity handoff，不声称旧账号与新账号的云端 Session 天然互通；不会复制 OAuth、Cookie、账号 ID、缓存或整个 `~/.claude`。

### 3. Agent-neutral migration

适用于迁移到 Codex、Grok 或其他 agent。Claude 专属规则会保留为来源平台证据，不会自动把 `CLAUDE.md` 翻译成 `AGENTS.md`，也不会预设目标平台架构。

## 与 ACGM 的关系

ACGM 和 Claude Code Recover 是两个独立产品：

- ACGM：在项目正常运行期间减少漂移、阻断高风险动作、保留治理证据；
- Claude Code Recover：在 Claude Code、原账号或原平台已经不可用后，从幸存本机证据重建项目连续性。

Recover 不依赖事故前安装过 ACGM。两者未来可以协作，但 Recover 不会被塞回 Claude Code 版 ACGM V3，也不会覆盖原发布仓库。

## 当前 RC 不做什么

- 不恢复云端账号或服务端 Session；
- 不执行项目任务或自动修改项目；
- 不复制 transcript 正文；
- 不自动提取或生成完整历史决策与叙事；
- 默认结构包不能单独替代原项目，也不是代码备份；
- 不读取 tool-result 持久化文件、project memory 或 task 正文；
- 不做数字签名，只有完整性 checksum；
- 不自动安装 ACGM 或目标平台配置；
- 不宣称本机观察到的 Claude 存储结构是永久 vendor contract。

安全模型见 [SECURITY.md](SECURITY.md)。
