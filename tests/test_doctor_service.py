from __future__ import annotations

import pytest

from oh_my_agent.gateway.services.doctor_service import DoctorService


class _RuntimeDoctorStub:
    async def build_doctor_report(self, *, platform: str, channel_id: str, scheduler=None) -> str:
        del platform, channel_id, scheduler
        return "\n".join(
            [
                "**Runtime health**",
                "- Enabled: `True`",
                "",
                "**Scheduler health**",
                "- Enabled: `True`",
            ]
        )


@pytest.mark.asyncio
async def test_build_report_assembles_gateway_and_runtime_sections():
    service = DoctorService(_RuntimeDoctorStub())

    result = await service.build_report(
        platform="discord",
        channel_id="100",
        gateway_info={"bot_online": True, "channel_bound": "100"},
    )

    assert result.success is True
    assert [section.title for section in result.sections] == [
        "Gateway health",
        "Runtime health",
        "Scheduler health",
    ]
    assert "- Bot online: `True`" in result.sections[0].lines
