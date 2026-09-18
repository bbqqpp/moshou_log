from pydantic import BaseModel, Field


class ExtractRequest(BaseModel):
    url: str = Field(..., min_length=8, description="WCL report URL containing #fight=<id>")


class LoginRequest(BaseModel):
    guild_name: str = Field(..., min_length=1, description="公会名，用于访问验证")
