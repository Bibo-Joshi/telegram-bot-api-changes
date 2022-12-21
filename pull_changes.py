import asyncio
import datetime
import re
import subprocess
from argparse import ArgumentParser
from enum import StrEnum
from http import HTTPStatus

from collections.abc import Collection

from httpx import AsyncClient
from telegram import Bot
from telegram.constants import ParseMode

REFERENCE_LINKS = sorted({
    "https://core.telegram.org/bots/api",
    "https://core.telegram.org/bots/payments",
    "https://core.telegram.org/passport",
    "https://core.telegram.org/bots/webapps",
})

SUPPLEMENTARY_LINKS = sorted({
    "https://core.telegram.org/bots",
    "https://core.telegram.org/bots/faq",
    "https://core.telegram.org/bots/webhooks",
    "https://core.telegram.org/bots/self-signed",
    "https://corefork.telegram.org/api/links",
    "https://core.telegram.org/bots/features",
    "https://core.telegram.org/bots/samples",
})


class ChangeList(StrEnum):
    SUPPLEMENTARY = "supplementary"
    REFERENCE = "reference"


PAGE_GENERATION_TIME_REGEX = re.compile(r"<!-- page generated in \d+\.\d+m?s -->")


def get_file_name(url: str) -> str:
    return url.replace("https://", "").replace("/", ".").replace(".telegram.org", "")


def remove_page_generation_time(html: str) -> str:
    return re.sub(PAGE_GENERATION_TIME_REGEX, "", html)


async def download_one(url: str, client: AsyncClient) -> None:
    response = await client.get(url)
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(f"Failed to download {url}: {response.status_code}")
    with open(f"{get_file_name(url)}.html", "w", encoding="utf-8") as f:
        f.write(remove_page_generation_time(response.text))


async def download_all(urls: Collection[str]) -> None:
    async with AsyncClient() as client:
        await asyncio.gather(*(download_one(url, client) for url in urls))


def diff_2_html(title: str) -> bytes | None:
    full_title = (
        f"{title} on {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
    )
    try:
        completed_process = subprocess.run(
            [
                "diff2html",
                "--style",
                "side",
                "--fileContentToggle",
                "--output",
                "stdout",
                "--title",
                full_title,
            ],
            check=True,
            capture_output=True,
        )

        return completed_process.stdout
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 3:
            print("No changes detected. Exiting.")
            return None
        else:
            raise exc


async def send_to_telegram(
    token: str, chat_id: int | str, html_data: bytes, filename: str, caption: str
) -> None:
    async with Bot(token) as bot:
        await bot.send_document(
            chat_id=chat_id,
            document=html_data,
            filename=filename,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )


def main(token: str, change_list: ChangeList) -> None:
    if change_list == ChangeList.SUPPLEMENTARY:
        urls = SUPPLEMENTARY_LINKS
        title = "Supplementary Docs"
        filename = "supplementary_docs.html"
    else:
        urls = REFERENCE_LINKS
        title = "Reference Docs"
        filename = "reference_docs.html"

    title = f"Changes in {title}"

    print("This is main with values", urls, title, filename)

    caption_urls = "\n".join((f"• <a href='{url}'>{get_file_name(url)}</a>" for url in urls))
    caption = f"{title} since last update. Tracked pages:\n\n{caption_urls}"

    asyncio.run(download_all(urls=urls))

    print("Downloaded all files")

    if html_data := diff_2_html(title=title):
        print("Changes detected. Sending to Telegram.")
        print(html_data.decode("utf-8"))
        asyncio.run(
            send_to_telegram(
                token=token,
                chat_id="@bot_api_changes",
                html_data=html_data,
                filename=filename,
                caption=caption,
            )
        )
    print("No changes detected. Exiting from main.")


if __name__ == "__main__":
    print("Starting script.")
    parser = ArgumentParser()
    parser.add_argument("-T", "--token", type=str, required=True)
    parser.add_argument(
        "-L",
        "--change-list",
        type=str,
        required=True,
        choices=tuple(ChangeList),
    )

    arguments = parser.parse_args()
    print("Arguments parsed.")
    main(token=arguments.token, change_list=ChangeList(arguments.change_list))
