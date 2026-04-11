from __future__ import annotations

from typing import TYPE_CHECKING

from oh_my_agent.gateway.services.types import DoctorResult, DoctorSection

if TYPE_CHECKING:
    from oh_my_agent.runtime.service import RuntimeService


class DoctorService:
    def __init__(self, runtime_service: RuntimeService | None):
        self._runtime = runtime_service

    async def build_report(
        self,
        *,
        platform: str,
        channel_id: str,
        scheduler=None,
        gateway_info: dict | None = None,
    ) -> DoctorResult:
        sections: list[DoctorSection] = []
        gateway_info = gateway_info or {}
        sections.append(
            DoctorSection(
                title="Gateway health",
                lines=[
                    f"- Bot online: `{gateway_info.get('bot_online', False)}`",
                    f"- Channel bound: `{gateway_info.get('channel_bound', channel_id)}`",
                ],
            )
        )
        if self._runtime is None:
            sections.append(DoctorSection(title="Runtime health", lines=["- Enabled: `False`"]))
            return DoctorResult(success=False, message="Runtime service is not enabled.", sections=sections)
        report = await self._runtime.build_doctor_report(
            platform=platform,
            channel_id=channel_id,
            scheduler=scheduler,
        )
        sections.extend(self._parse_sections(report))
        return DoctorResult(success=True, message="Doctor report built.", sections=sections)

    @staticmethod
    def _parse_sections(text: str) -> list[DoctorSection]:
        sections: list[DoctorSection] = []
        current: DoctorSection | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4:
                if current is not None:
                    sections.append(current)
                current = DoctorSection(title=stripped.strip("*"), lines=[])
                continue
            if current is None:
                continue
            if stripped == "" and (not current.lines or current.lines[-1] == ""):
                continue
            current.lines.append(line)
        if current is not None:
            sections.append(current)
        return sections
