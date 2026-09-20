from pydantic import BaseModel, Field


class ExtractRequest(BaseModel):
    url: str = Field(..., min_length=8, description="WCL report URL containing #fight=<id>")


class LoginRequest(BaseModel):
    guild_name: str = Field(..., min_length=1, description="公会名，用于访问验证")


class PlayerReportRequest(BaseModel):
    """生成玩家报告：1 个玩家出单人复盘，2 个出对比。"""

    report_code: str = Field(..., min_length=1)
    fight_id: int
    players: list[str] = Field(..., min_length=1, max_length=2)
