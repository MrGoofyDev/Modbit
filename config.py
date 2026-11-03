import os


def get_discord_token() -> str:
    token = os.getenv("DISCORD_TOKEN")
    if token is None or not token.strip():
        raise RuntimeError(
            "DISCORD_TOKEN is not set. Add your Discord bot token to the hosting "
            "platform's environment variables and restart Modex."
        )
    return token.strip()
