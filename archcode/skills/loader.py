"""SkillLoader:三层扫描 + frontmatter 解析 + seen 遮蔽诊断(skills-design.md 0.1/0.2)。

三层优先级从高到低(0.1):
  1. 项目级  <work_dir>/.archcode/skills/*.md      随 Git 共享
  2. 用户级  <源码根>/.archcode/skills/*.md        个人跨项目(本地学习项目,不放 C 盘)
  3. 内置级  archcode/skills/builtin/*.md          包数据占位,目录可不存在

同名遮蔽:seen 字典先到先得,被遮蔽时产生诊断(不静默);解析失败跳过 + 诊断,不中断加载。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from archcode.paths import application_data_dir, project_data_dir
from archcode.skills.models import SkillManifest

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VALID_MODES = {"inline", "fork"}
VALID_CONTEXTS = {"full", "recent", "none"}


def parse_frontmatter(text: str) -> tuple[dict | None, str, str | None]:
    """解析 SKILL.md:返回 (frontmatter 元数据, 正文, 错误)。

    Windows 友好(细节审计 2026-09-19):
    - 容忍 BOM 头(记事本保存的 UTF-8)与首部空白;
    - 统一 CRLF → LF(否则正文/块里残留 \\r);
    - 开界线必须是行首独立的 ``---``(``-----`` 水平线不算),闭界线按
      ``\\n---`` 行首匹配(避免 description 里的 ``---`` 子串误闭合)。

    - 无 frontmatter / 未闭合 / YAML 非法 → (None, 规整后文本, 错误说明)
    - 错误非 None 时调用方应跳过该文件并记录诊断
    """
    text = text.replace("\r\n", "\n").lstrip("\ufeff \t\r\n")
    if not text.startswith("---\n"):
        return None, text, "缺少 YAML frontmatter(文件必须以行首 --- 开头)"
    end = text.find("\n---", 3)
    if end == -1:
        return None, text, "frontmatter 未闭合(缺少第二个 ---)"
    block = text[3:end].strip()
    body = text[end + len("\n---") :].lstrip("\n")
    try:
        meta = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        return None, text, f"frontmatter YAML 解析失败: {exc}"
    if not isinstance(meta, dict):
        return None, text, "frontmatter 必须是键值映射"
    return meta, body, None


def substitute_arguments(body: str, args: str) -> str:
    """$ARGUMENTS 占位符替换:字面替换,空参替换为空串,兜底逻辑由模板文本承载。"""
    return body.replace("$ARGUMENTS", args)


def _parse_allowed_tools(meta: dict) -> tuple[str, ...]:
    """读取可选的 allowedTools 声明(兼容 allowedTools / allowed_tools / allowed-tools)。"""
    raw = None
    for key in ("allowedTools", "allowed_tools", "allowed-tools"):
        if key in meta:
            raw = meta[key]
            break
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    return tuple(str(item).strip() for item in raw if str(item).strip())


class SkillLoader:
    """三层扫描 → SkillManifest 集合。每次 scan() 全量重扫,返回新的快照。"""

    def __init__(self, work_dir: str | Path | None = None) -> None:
        self._work_dir = Path(work_dir).resolve() if work_dir else None
        self._manifests: dict[str, SkillManifest] = {}
        self.diagnostics: list[str] = []
        # 失败兜底(冗余):上次成功解析的 manifest 与正文。
        # 文件被编辑坏时沿用旧版——已激活/已可见的 Skill 不因一次手误消失;
        # 文件被删除则是显式移除,不沿用。详见 skills-design.md 0.1。
        self._last_good: dict[str, SkillManifest] = {}
        self._last_good_bodies: dict[str, str] = {}
        self.scan()

    # ── 路径 ────────────────────────────────────────────────────────

    def _layer_dirs(self) -> list[tuple[str, Path]]:
        """三层来源目录,按优先级从高到低(0.1)。

        去重:work_dir 恰为源码根时 project/user 是同一目录,跳过重复层
        (否则每个 Skill 都会产生一条假遮蔽诊断)。
        """
        layers: list[tuple[str, Path]] = []
        seen_dirs: set[Path] = set()
        for source, directory in (
            ("project", project_data_dir(self._work_dir) / "skills")
            if self._work_dir is not None
            else ("user", application_data_dir() / "skills"),
            ("user", application_data_dir() / "skills"),
            ("builtin", Path(__file__).resolve().parent / "builtin"),
        ):
            resolved = directory.resolve()
            if resolved in seen_dirs:
                continue
            seen_dirs.add(resolved)
            layers.append((source, directory))
        return layers

    # ── 扫描 ────────────────────────────────────────────────────────

    def scan(self) -> dict[str, SkillManifest]:
        self.diagnostics = []
        fresh: dict[str, SkillManifest] = {}
        seen: set[str] = set()
        for source, directory in self._layer_dirs():
            if not directory.is_dir():
                continue
            # 单文件 Skill:顶层 *.md
            for path in sorted(directory.glob("*.md")):
                manifest = self._parse_file(path, source)
                if manifest is None:
                    continue
                if manifest.name in seen:
                    self.diagnostics.append(
                        f"[skills] '{manifest.name}' 的 {source} 版本被更高优先级版本遮蔽,未加载: {path}"
                    )
                    continue
                seen.add(manifest.name)
                fresh[manifest.name] = manifest
            # 目录型 Skill:含 SKILL.md 的子目录(0.5)
            for skill_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file():
                    continue
                manifest = self._parse_file(skill_md, source, skill_dir=skill_dir)
                if manifest is None:
                    continue
                if manifest.name in seen:
                    self.diagnostics.append(
                        f"[skills] '{manifest.name}' 的 {source} 版本被更高优先级版本遮蔽,未加载: {skill_md}"
                    )
                    continue
                seen.add(manifest.name)
                fresh[manifest.name] = manifest

        # 失败兜底:文件存在但解析失败(编辑中的半成品)→ 沿用上次成功版本;
        # 文件被删除 → 显式移除,不沿用。
        for name, old in self._last_good.items():
            if name in fresh:
                continue
            if old.path.is_file():
                fresh[name] = old
                self.diagnostics.append(
                    f"[skills] '{name}' 解析失败,已沿用上次成功版本: {old.path}"
                )

        self._last_good.update(fresh)
        self._manifests = fresh
        return self._manifests

    def _parse_file(
        self, path: Path, source: str, skill_dir: Path | None = None
    ) -> SkillManifest | None:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            self.diagnostics.append(f"[skills] 文件读取失败,已跳过 {path}: {exc}")
            return None
        checksum = hashlib.sha256(raw).hexdigest()
        try:
            text = raw.decode("utf-8-sig")  # utf-8-sig:自动剥 Windows 记事本的 BOM 头
        except UnicodeDecodeError as exc:
            self.diagnostics.append(f"[skills] 文件非 UTF-8,已跳过 {path}: {exc}")
            return None

        meta, _body, error = parse_frontmatter(text)
        if error or meta is None:
            self.diagnostics.append(f"[skills] 解析失败,已跳过 {path}: {error}")
            return None

        name = str(meta.get("name", "")).strip()
        description = str(meta.get("description", "")).strip()
        if not _NAME_RE.match(name):
            self.diagnostics.append(
                f"[skills] 名字非法(须为小写字母/数字/连字符),已跳过 {path}: {name!r}"
            )
            return None
        if not description:
            self.diagnostics.append(f"[skills] 缺少 description,已跳过 {path}")
            return None

        mode = str(meta.get("mode", "inline")).strip().lower()
        if mode not in VALID_MODES:
            self.diagnostics.append(
                f"[skills] mode 非法(只允许 inline/fork),已跳过 {path}: {mode!r}"
            )
            return None
        context = str(meta.get("context", "recent")).strip().lower()
        if context not in VALID_CONTEXTS:
            self.diagnostics.append(
                f"[skills] context 非法(只允许 full/recent/none),已跳过 {path}: {context!r}"
            )
            return None
        model = meta.get("model")
        model = str(model).strip() if model is not None else None

        return SkillManifest(
            mode=mode,
            context=context,
            model=model or None,
            name=name,
            description=description,
            path=path,
            source=source,
            checksum=checksum,
            allowed_tools=_parse_allowed_tools(meta),
            is_directory=skill_dir is not None,
            skill_dir=skill_dir,
        )

    # ── 查询 ────────────────────────────────────────────────────────

    def get(self, name: str) -> SkillManifest | None:
        """取 manifest;有 path 时**热重载**——重读文件(0.1/0.2)。

        编辑 SKILL.md(正文或 frontmatter)后,下一次 get(激活 / /skill info)
        即拿到新版本,无需重启。容错语义与 scan 的失败兜底一致:

        - 重读解析失败(改坏)→ 沿用内存中上次成功的 manifest,绝不返回 None
          让已知 Skill 消失(教程版此处漏捕 OSError,文件被删会直接炸出);
        - frontmatter 的 name 被改名 → 沿用旧 manifest(目录/命令按旧名注册)。
        """
        skill = self._manifests.get(name)
        if skill is None:
            return None
        fresh = self._parse_file(skill.path, skill.source, skill_dir=skill.skill_dir)
        if fresh is None:
            # 解析失败:_parse_file 已记诊断;沿用内存中上次成功的版本
            return skill
        if fresh.name != name:
            self.diagnostics.append(
                f"[skills] '{name}' 的 frontmatter name 被改为 {fresh.name!r},"
                f"本次沿用旧版本;改名需 /skill reload + 重启生效"
            )
            return skill
        self._manifests[name] = fresh
        self._last_good[name] = fresh
        return fresh

    def manifests(self) -> dict[str, SkillManifest]:
        return dict(self._manifests)

    def read_body(self, name: str) -> str | None:
        """读取 Skill 正文(渐进披露第二层:激活时才读)。

        失败兜底:文件损坏/读取失败时沿用上次成功的正文(冗余),
        已激活的 Skill 不会因文件被改坏而失效。从未成功读过 → None。
        """
        manifest = self._manifests.get(name)
        if manifest is not None:
            try:
                text = manifest.path.read_text(encoding="utf-8-sig")
            except OSError:
                text = None
            if text is not None:
                _meta, body, error = parse_frontmatter(text)
                if not error:
                    self._last_good_bodies[name] = body
                    return body
        return self._last_good_bodies.get(name)

    def get_catalog_text(self) -> str:
        """system prompt 用的 Skill 目录文本(§0.2 第一阶段)。无 Skill 时返回空串。"""
        if not self._manifests:
            return ""
        lines = [
            "你可以使用以下 Skill(用户请求匹配某个 Skill 时,用 LoadSkill 工具激活它):",
            "",
        ]
        for name, manifest in sorted(self._manifests.items()):
            lines.append(f"- {name}: {manifest.description} [{manifest.source}]")
        return "\n".join(lines)
