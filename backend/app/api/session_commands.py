from __future__ import annotations

import re

from app.api.models import (
    ProductDevelopmentSession,
    ProductDevelopmentSessionCommandPreview,
    ProductProject,
)
from app.models.development_session import DevelopmentSessionCommandIntent

_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:sk|ghp|github_pat|bearer)[_-]?[a-z0-9][a-z0-9_.-]{7,}\b"
    r"|\b(?:api[_-]?key|token|authorization)\s*[=:]\s*\S+"
)


class DevelopmentSessionCommandPreviewer:
    """Rule-only parser for the deliberately tiny conversation control surface.

    It does not execute actions, invoke a model, retain the command text, or accept credentials.
    """

    def preview(
        self,
        *,
        session: ProductDevelopmentSession,
        project: ProductProject,
        command: str,
    ) -> ProductDevelopmentSessionCommandPreview:
        intent = self._intent(command)
        target = self._project_label(project)
        if intent is DevelopmentSessionCommandIntent.CONTINUE_OLD_BASE:
            return self._card(
                session,
                intent,
                "基于旧基线继续",
                target,
                (
                    "会创建一条新的关联运行记录。",
                    "已完成工作包与保存的检查点将被复用。",
                    "旧运行和证据不会被修改。",
                ),
                token_cost="将消耗后续开发/修复预算",
                affects_local_data=False,
                hint="确认后仅按服务端保存的旧基线继续未完成工作包。",
            )
        if intent is DevelopmentSessionCommandIntent.CONTINUE_DEVELOPMENT:
            return self._card(
                session,
                intent,
                "继续开发",
                target,
                (
                    "会创建一条新的关联运行记录。",
                    "只处理未完成工作包；已完成部分不会重复开发。",
                ),
                token_cost="将消耗后续开发/修复预算",
                affects_local_data=False,
                hint="确认后由服务端检查基线、预算和依赖环境。",
            )
        if intent is DevelopmentSessionCommandIntent.REPLAN:
            return self._card(
                session,
                intent,
                "重新规划",
                target,
                (
                    "会基于当前仓库基线创建新的开发会话和运行记录。",
                    "旧会话、旧运行和证据保持不变。",
                ),
                token_cost="将消耗新的规划模型预算",
                affects_local_data=False,
                hint="确认后才会请求规划模型。",
            )
        if intent is DevelopmentSessionCommandIntent.ARCHIVE_RUN:
            available = session.latest_run_id is not None
            return self._card(
                session,
                intent,
                "归档当前运行",
                (
                    f"运行 {str(session.latest_run_id)[:8]}"
                    if session.latest_run_id is not None
                    else "当前会话尚未创建运行"
                ),
                ("运行将从默认列表隐藏，仍可在归档记录中恢复查看。",),
                token_cost="不消耗模型 Token",
                affects_local_data=False,
                executable=available,
                hint=("确认后归档当前运行。" if available else "当前会话没有可归档的运行。"),
            )
        if intent is DevelopmentSessionCommandIntent.ARCHIVE_PROJECT:
            return self._card(
                session,
                intent,
                "归档项目",
                target,
                (
                    "项目会从默认侧边栏隐藏，后续可恢复。",
                    "GitHub 仓库、运行记录和本地工作区不会被删除。",
                ),
                token_cost="不消耗模型 Token",
                affects_local_data=False,
                hint="确认后归档当前项目。",
            )
        if intent is DevelopmentSessionCommandIntent.DELETE_PROJECT:
            return self._card(
                session,
                intent,
                "永久删除项目本地数据",
                target,
                (
                    "将删除 DevFlow 本地项目记录、工作区、项目缓存和本地凭据。",
                    "GitHub 仓库不会被删除。",
                    "下一步仍需输入项目名称完成二次确认。",
                ),
                token_cost="不消耗模型 Token",
                affects_local_data=True,
                hint="确认后先展示删除影响范围；不会立即删除。",
            )
        return self._card(
            session,
            DevelopmentSessionCommandIntent.UNKNOWN,
            "未识别为安全操作",
            target,
            ("仅支持继续开发、基于旧基线继续、重新规划、归档运行、归档项目和删除项目。",),
            token_cost="不消耗模型 Token",
            affects_local_data=False,
            executable=False,
            hint="命令未保存，也不会调用模型或执行任何操作。",
        )

    @staticmethod
    def _intent(command: str) -> DevelopmentSessionCommandIntent:
        normalized = " ".join(command.strip().lower().split())
        if not normalized or _SECRET_VALUE_RE.search(command):
            return DevelopmentSessionCommandIntent.UNKNOWN
        if "删除" in normalized and "项目" in normalized:
            return DevelopmentSessionCommandIntent.DELETE_PROJECT
        if "归档" in normalized and "项目" in normalized:
            return DevelopmentSessionCommandIntent.ARCHIVE_PROJECT
        if "归档" in normalized and ("运行" in normalized or "run" in normalized):
            return DevelopmentSessionCommandIntent.ARCHIVE_RUN
        if "重新规划" in normalized or "replan" in normalized:
            return DevelopmentSessionCommandIntent.REPLAN
        if "旧基线" in normalized or "old base" in normalized:
            return DevelopmentSessionCommandIntent.CONTINUE_OLD_BASE
        if "继续" in normalized or "resume" in normalized or "continue" in normalized:
            return DevelopmentSessionCommandIntent.CONTINUE_DEVELOPMENT
        return DevelopmentSessionCommandIntent.UNKNOWN

    @staticmethod
    def _card(
        session: ProductDevelopmentSession,
        intent: DevelopmentSessionCommandIntent,
        action_name: str,
        target_label: str,
        impact: tuple[str, ...],
        *,
        token_cost: str,
        affects_local_data: bool,
        executable: bool = True,
        hint: str,
    ) -> ProductDevelopmentSessionCommandPreview:
        return ProductDevelopmentSessionCommandPreview(
            session_id=session.session_id,
            intent=intent,
            action_name=action_name,
            target_label=target_label,
            impact=impact,
            token_cost=token_cost,
            affects_local_data=affects_local_data,
            executable_after_confirmation=executable,
            confirmation_hint=hint,
        )

    @staticmethod
    def _project_label(project: ProductProject) -> str:
        return project.repository_url.rstrip("/").split("/")[-1] or project.repository_url
