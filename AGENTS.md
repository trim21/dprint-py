# agents.md

## 项目概述

`dprint-py` 是一个 Python 打包项目，将 [dprint](https://dprint.dev/)（一个可插拔、可配置的代码格式化工具，由 Rust 编写）的预编译二进制文件打包为 Python wheel，发布到 PyPI。

- **名称**: `dprint-py`
- **上游**: https://github.com/dprint/dprint
- **仓库**: https://github.com/trim21/dprint-py
- **许可证**: MIT

## 项目结构

```
dprint-py/
├── scripts/
│   └── sync_version.py  # 同步 project.version 到上游版本
├── build.py              # 自定义 wheel 构建脚本
├── taskfile.yaml         # Task 任务定义（版本发布）
├── readme.md             # 项目说明
└── .github/              # CI/CD 配置
    ├── mergify.yml
    ├── renovate.json
    └── workflows/
        ├── _build_wheels.yaml   # 可复用的 wheel 构建
        ├── auto-tag.yaml        # 合并 PR 后自动打 tag
        ├── lint.yaml            # pre-commit 检查
        ├── release.yaml         # tag 触发 → 构建并发布到 PyPI
        ├── sync-version.yaml    # Renovate PR 中同步 project.version
        └── test.yaml            # 构建并测试 wheel
```

## 工作原理

`build.py` 从 dprint 的 GitHub Releases 下载对应平台的 zip 包，提取二进制文件，并手动构建符合 Python wheel 规范的 `.whl` 文件。

### 支持的平台

| 平台     | 架构   | manylinux / macOS 版本 |
|----------|--------|------------------------|
| Windows  | x86_64 | -                      |
| Linux    | x86_64 | manylinux 2.17         |
| Linux    | arm64  | manylinux 2.17         |
| macOS    | arm64  | 11.0                   |
| macOS    | x86_64 | 11.0                   |

## 构建

```bash
python build.py
```

构建产物输出到 `dist/` 目录，wheel 命名格式：`dprint_py-{version}-{tag}.whl`

## 配置说明

所有打包配置位于 `pyproject.toml` 的 `[tool.pack-binary]` 段：

- `cmd`: 命令名（`dprint`）
- `context.version`: dprint 上游版本号
- `project`: 最终 wheel 的 PyPI 项目元数据（名称、版本、描述、作者等）
- `target`: 各平台下载 URL 模板和平台配置

## CI/CD 自动化

### 上游更新 → 自动发版流程

1. **Renovate** 检测 `dprint/dprint` 上游新 release → 自动创建 PR 更新 `context.version`
2. **sync-version** workflow 调用 `scripts/sync_version.py` 自动同步 `project.version`（格式为 `{upstream}.0`）
3. **Mergify** 检测到 `ci:auto-merge` 标签 + CI 通过 → 自动 squash merge
4. **auto-tag** workflow 在 PR 合并到 master 后自动创建 `v{project.version}` tag（使用 PAT 推送以触发 release）
5. **release** workflow 由 tag 触发 → 构建 wheel 并发布到 PyPI

## 发布流程

使用 Task 进行手动版本发布：

```bash
task bump
```

该命令会：
1. 提交 `pyproject.toml` 的修改
2. 创建带 `v` 前缀的 git tag
3. 推送到远程仓库

## 技术栈

- **Python 3.14** (构建环境)
- **依赖**: httpx, pydantic, jinja2
- **包管理器**: uv
- **任务运行器**: Task (go-task)
