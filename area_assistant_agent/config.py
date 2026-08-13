"""Runtime configuration loaded from process environment variables."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class AgentConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    base_url: str = "https://api.fe8.cn/v1"
    api_key: str = ""
    model: str = ""
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls):
        return cls(
            host="127.0.0.1",
            port=int(os.environ.get("AI_AREA_ASSISTANT_PORT", "8765")),
            base_url=os.environ.get("AI_AREA_ASSISTANT_BASE_URL", "https://api.fe8.cn/v1"),
            api_key=os.environ.get("AI_AREA_ASSISTANT_API_KEY", ""),
            model=os.environ.get("AI_AREA_ASSISTANT_MODEL", ""),
            timeout_seconds=float(os.environ.get("AI_AREA_ASSISTANT_TIMEOUT_SECONDS", "30")),
        )
