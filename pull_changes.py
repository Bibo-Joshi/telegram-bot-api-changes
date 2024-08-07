import asyncio
import datetime
import platform
import re
import subprocess
from argparse import ArgumentParser
from collections.abc import Collection
from enum import StrEnum
from http import HTTPStatus

from httpx import AsyncClient
from telegram import Bot
from telegram.constants import ParseMode


class ChangeList(StrEnum):
    SUPPLEMENTARY = "supplementary"
    REFERENCE = "reference"


PAGE_GENERATION_TIME_REGEX = re.compile(r"<!-- page generated in \d+(\.\d+)?m?s -->")
CURRENCY_TABLE_REGEX = re.compile(
    r"""
    # Match the header of the payment amount table
    <thead>\s*
        <tr>\s*
        <th>Code</th>\s*
        <th>Title</th>\s*
        <th>Min\ amount</th>\s*
        <th>Max\ amount</th>\s*
        </tr>\s*
    </thead>\s*
    <tbody>\s*
    ([\s\S](?!</tbody>))+ # Match all chars until the end of the table
    > # Closing bracet of the last rew
    </tbody> # Closing tag of the table
    """,
    re.VERBOSE,
)
PAYMENT_AMOUNTS_REGEX = re.compile(
    r"""
    <td> # Opening tag of the column
    (
        ([^<\d]+\d[\d/,\.'\-;\s\u00A0\u2007\u202F]+\d) # Either first currency abbreviation and then amount
        | # or
        (\d[\d/,\.'\-;\s\u00A0\u2007\u202F]+\d[^<\d]+) # first amount and then currency abbreviation
    )
    </td> # Closing tag of the column
    """,
    re.VERBOSE,
)


def get_urls(change_list: ChangeList) -> list[str]:
    with open(f"{change_list.value}_links.txt", encoding="utf-8") as file:
        return [url.strip() for url in file.readlines()]


def get_file_name_base(url: str) -> str:
    return f'{url.replace("https://", "").replace("/", ".").replace(".telegram.org", "")}'


def get_file_name(url: str) -> str:
    return f"{get_file_name_base(url)}.html"


def remove_page_generation_time(html: str) -> str:
    return re.sub(PAGE_GENERATION_TIME_REGEX, "", html)


def remove_payment_amounts(html: str) -> str:
    if not (match := re.search(CURRENCY_TABLE_REGEX, html)):
        return html
    table = match.group(0)
    for match in re.finditer(PAYMENT_AMOUNTS_REGEX, table):
        print(match.group(0))
    empty_table = re.sub(PAYMENT_AMOUNTS_REGEX, "<td></td>", table)
    return html.replace(table, empty_table)


def sanitize_html(html: str) -> str:
    return remove_payment_amounts(remove_page_generation_time(html))


async def download_one(url: str, client: AsyncClient) -> None:
    response = await client.get(url)
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(f"Failed to download {url}: {response.status_code}")
    with open(f"{get_file_name(url)}", "w", encoding="utf-8") as file:
        file.write(sanitize_html(response.text))


async def download_all(urls: Collection[str]) -> None:
    async with AsyncClient(timeout=20) as client:
        await asyncio.gather(*(download_one(url, client) for url in urls))


def diff_2_html(title: str) -> bytes | None:
    full_title = (
        f"Changes in {title} on {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
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
            shell=platform.system() == "Windows",
        )

        return completed_process.stdout
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 3:
            return None
        if exc.stderr:
            print(exc.stderr.decode("utf-8"))
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
            write_timeout=60,
            connect_timeout=60,
            read_timeout=60,
        )


def main(token: str, change_list: ChangeList) -> None:
    if change_list == ChangeList.SUPPLEMENTARY:
        title = "Supplementary Docs"
        filename = "supplementary_docs.html"
    else:
        title = "Reference Docs"
        filename = "reference_docs.html"

    urls = get_urls(change_list)
    caption_base = f"Changed <i>{title}</i>:\n\n"

    asyncio.run(download_all(urls=urls))

    if html_data := diff_2_html(title=title):
        html_text = html_data.decode("utf-8")
        caption_urls = "\n".join(
            f"• <a href='{url}'>{get_file_name_base(url)}</a>"
            for url in urls
            if get_file_name(url) in html_text
        )
        asyncio.run(
            send_to_telegram(
                token=token,
                chat_id="@bot_api_changes",
                html_data=html_data,
                filename=filename,
                caption=f"{caption_base}{caption_urls}",
            )
        )


if __name__ == "__main__":
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
    main(token=arguments.token, change_list=ChangeList(arguments.change_list))
