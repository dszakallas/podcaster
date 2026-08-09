import json
import logging
from pathlib import Path
from typing import Optional, Union

import httpx
from jinja2 import Template

from ..utils import find_notebook_dir, get_env_var
from .base import Notifier

logger = logging.getLogger(__name__)

DEFAULT_DISCORD_TEMPLATE = Template(
    "🎙️ **Podcast Distributed**\n"
    "{%- if notebook_title %}\n- **Title**: {{ notebook_title }}{% endif %}\n"
    "- **Notebook ID**: `{{ notebook_id }}`\n"
    "{%- if wf_preset %}\n- **Workflow Preset**: `{{ wf_preset }}`{% endif %}\n"
    "{%- if dest_display %}\n- **Destination**: `{{ dest_display }}`{% endif %}\n"
    "{%- if wf_config_str %}\n- **Workflow Config**: `{{ wf_config_str }}`{% endif %}"
)


async def send_discord_notification(
    notebook_id: str,
    podcast_dir: str,
    webhook_url: Optional[str] = None,
    bot_token: Optional[str] = None,
    channel_id: Optional[Union[int, str]] = None,
    dist_result: Optional[dict] = None,
    name: Optional[str] = None,
) -> dict:

    notebook_dir_name = find_notebook_dir(podcast_dir, notebook_id) or notebook_id

    state_metadata = {}
    if podcast_dir and notebook_dir_name:
        state_file = Path(podcast_dir) / notebook_dir_name / "state.json"
        if state_file.exists():
            try:
                with open(state_file, "r") as f:
                    state_metadata = json.load(f)
            except Exception as e:
                logger.debug(f"Could not load state.json: {e}")

    notebook_title = state_metadata.get("notebook_title")
    wf_preset = state_metadata.get("preset")
    wf_config = state_metadata.get("config")

    webhook_url = webhook_url or get_env_var("NOTIFIER", name, "DISCORD_WEBHOOK_URL")
    bot_token = bot_token or get_env_var("NOTIFIER", name, "DISCORD_BOT_TOKEN")
    channel_id = channel_id or get_env_var("NOTIFIER", name, "DISCORD_CHANNEL_ID")

    dest_display = None
    if dist_result and isinstance(dist_result, dict):
        dest_display = dist_result.get("distribution") or dist_result.get("destination")

    wf_config_str = None
    if wf_config and isinstance(wf_config, dict):
        cfg_items = [f"{k}={v}" for k, v in wf_config.items() if v is not None]
        if cfg_items:
            wf_config_str = ", ".join(cfg_items)

    message = DEFAULT_DISCORD_TEMPLATE.render(
        notebook_title=notebook_title,
        notebook_id=notebook_id,
        wf_preset=wf_preset,
        dest_display=dest_display,
        wf_config_str=wf_config_str,
    ).strip()

    if webhook_url:
        logger.debug(f"Sending Discord notification via webhook to {webhook_url}")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    webhook_url,
                    json={"content": message},
                    timeout=10.0,
                )
                resp.raise_for_status()
            logger.debug("Discord webhook notification sent successfully.")
            return {"notebook_id": notebook_id, "status": "success"}
        except Exception as e:
            logger.debug(f"Discord webhook notification failed: {e}")
            return {
                "notebook_id": notebook_id,
                "status": "partial_success",
                "message": f"Discord notification failed: {e}",
            }
    elif bot_token and channel_id:
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
        }
        logger.debug(
            f"Sending Discord notification via Bot API to channel {channel_id}"
        )
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    headers=headers,
                    json={"content": message},
                    timeout=10.0,
                )
                resp.raise_for_status()
            logger.debug("Discord bot notification sent successfully.")
            return {"notebook_id": notebook_id, "status": "success"}
        except Exception as e:
            logger.debug(f"Discord bot notification failed: {e}")
            return {
                "notebook_id": notebook_id,
                "status": "partial_success",
                "message": f"Discord notification failed: {e}",
            }
    else:
        logger.warning(
            "Discord webhook_url or bot_token/channel_id not found. Skipping notification."
        )
        return {
            "notebook_id": notebook_id,
            "status": "partial_success",
            "message": "Discord notification skipped due to missing credentials.",
        }


class DiscordNotifier(Notifier):
    """Discord notification mechanism implementation."""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        bot_token: Optional[str] = None,
        channel_id: Optional[Union[int, str]] = None,
        name: Optional[str] = None,
    ):
        self.webhook_url = webhook_url
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.name = name

    async def notify(
        self,
        notebook_id: str,
        podcast_dir: str,
        dist_result: Optional[dict] = None,
    ) -> dict:
        return await send_discord_notification(
            notebook_id=notebook_id,
            webhook_url=self.webhook_url,
            bot_token=self.bot_token,
            channel_id=self.channel_id,
            dist_result=dist_result,
            podcast_dir=podcast_dir,
            name=self.name,
        )
