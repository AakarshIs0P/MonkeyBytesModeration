from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Config:
    discord_token: str
    discord_prefix: str
    discord_owner_ids: List[int]
    discord_join_message: str
    discord_activity_name: str
    discord_activity_type: str
    discord_status_type: str
    discord_autorole_id: Optional[int] = None
