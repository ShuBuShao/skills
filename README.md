# Agent Skills

可通过 [`skills`](https://skills.sh/) CLI 安装的个人 Agent Skills 集合。每个 Skill 独立放在 `skills/<skill-name>/` 下，仓库根目录不放 `SKILL.md`，以便 CLI 发现多个 Skill。

## 安装

查看仓库中的 Skill：

```bash
npx skills add ShuBuShao/skills --list
```

安装指定 Skill：

```bash
npx skills add ShuBuShao/skills --skill multi-repo-workspace
```

安装到用户级目录：

```bash
npx skills add ShuBuShao/skills --skill multi-repo-workspace --global
```

## 可用技能

- `multi-repo-workspace`：创建协调多个仓库的开发工作区。
- `migration-bug-guide`：按当前模块、新旧实现和项目规范，生成或更新业务目录的翻新缺陷修复指引。

安装翻新缺陷修复指引技能：

```bash
npx skills add ShuBuShao/skills --skill migration-bug-guide
```

## 仓库结构

```text
skills/
  <skill-name>/
    SKILL.md
    agents/openai.yaml        # 可选的 Codex UI 元数据
    scripts/                  # 可选的确定性执行脚本
    references/               # 可选的按需说明
tests/                        # 仓库级行为测试
.github/workflows/            # 结构、测试和安装兼容性验证
```

新增 Skill 时遵循以下约定：

- 目录名与 `SKILL.md` 中的 `name` 一致，使用小写字母、数字和连字符。
- `SKILL.md` 只保留触发条件、关键决策和执行流程；条件性细节放入 `references/`。
- 重复、易错或涉及文件安全的操作放入 `scripts/`，并在 `tests/` 增加行为测试。
- 不提交密钥、本机绝对路径、项目私有配置或生成产物。
- 不在单个 Skill 内增加 README、变更日志或安装文档；安装说明统一维护在仓库根目录。

## 本地验证

```bash
python3 -m unittest discover -s tests -v
npx skills add . --list
```
