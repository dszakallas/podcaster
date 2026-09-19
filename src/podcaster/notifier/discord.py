import logging
from typing import Any

import httpx
from jinja2 import Template

from .base import Notifier, register_notifier

logger = logging.getLogger(__name__)

DEFAULT_DISCORD_TEMPLATE = Template(
    "🎙️ **Podcast Distributed**\n"
    "{%- if dest_display %}\n- **Destination**: `{{ dest_display }}`\n{%- endif %}\n"
    "{%- for key, value in metadata.items() %}\n"
    "{%- if value is iterable and value is not string and value is not mapping %}\n"
    "{%- if value | length > 0 and (value[0] | string | length) > 5 %}\n"
    "- **{{ key | replace('_', ' ') | title }}**:\n"
    "{%- for item in value %}\n"
    "  • {{ item }}\n"
    "{%- endfor %}\n"
    "{%- else %}\n"
    "- **{{ key | replace('_', ' ') | title }}**: {{ value | join(', ') }}\n"
    "{%- endif %}\n"
    "{%- else %}\n"
    "- **{{ key | replace('_', ' ') | title }}**: {{ value }}\n"
    "{%- endif %}\n"
    "{%- endfor %}\n"
)


async def send_discord_notification(
    metadata: dict | None = None,
    webhook_url: str | None = None,
    bot_token: str | None = None,
    channel_id: int | str | None = None,
    dist_result: dict | None = None,
) -> dict:

    meta = metadata or {}
    if "notification" in meta and isinstance(meta["notification"], dict):
        meta = meta["notification"]

    dest_display = None
    if dist_result and isinstance(dist_result, dict):
        dest_display = dist_result.get("distribution") or dist_result.get("destination")

    message = DEFAULT_DISCORD_TEMPLATE.render(
        metadata=meta,
        dest_display=dest_display,
    ).strip()

    if webhook_url:
        logger.debug("Sending Discord notification via webhook.")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    webhook_url,
                    json={"content": message},
                    timeout=10.0,
                )
                resp.raise_for_status()
            logger.debug("Discord webhook notification sent successfully.")
            return {"status": "success", "metadata": meta}
        except Exception as e:
            logger.debug(
                "Discord webhook notification failed with %s.", type(e).__name__
            )
            return {
                "status": "partial_success",
                "message": "Discord notification failed.",
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
            return {"status": "success", "metadata": meta}
        except Exception as e:
            logger.debug("Discord bot notification failed with %s.", type(e).__name__)
            return {
                "status": "partial_success",
                "message": "Discord notification failed.",
            }
    else:
        logger.warning(
            "Discord webhook_url or bot_token/channel_id not found. Skipping notification."
        )
        return {
            "status": "partial_success",
            "message": "Discord notification skipped due to missing credentials.",
        }


class DiscordNotifier(Notifier):
    """Discord notification mechanism implementation."""

    def __init__(
        self,
        webhook_url: str | None = None,
        bot_token: str | None = None,
        channel_id: int | str | None = None,
        name: str | None = None,
    ):
        self.webhook_url = webhook_url
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.name = name

    async def notify(
        self,
        metadata: dict | None = None,
        dist_result: dict | None = None,
    ) -> dict:
        return await send_discord_notification(
            metadata=metadata,
            webhook_url=self.webhook_url,
            bot_token=self.bot_token,
            channel_id=self.channel_id,
            dist_result=dist_result,
        )


def _build_discord_notifier(cfg: Any, name: str | None = None) -> Notifier:
    assert cfg.discord is not None
    return DiscordNotifier(
        webhook_url=cfg.discord.webhook_url,
        bot_token=cfg.discord.bot_token,
        channel_id=cfg.discord.channel_id,
        name=name,
    )


register_notifier("discord", _build_discord_notifier)
