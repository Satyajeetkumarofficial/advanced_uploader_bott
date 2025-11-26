import os
import re
import time
import requests
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from database import (
    get_user_doc,
    is_banned,
    update_stats,
    set_screenshots,
    set_sample,
    set_thumb,
    set_caption,
    set_upload_type,
)
from utils.downloader import (
    get_formats,
    download_direct_with_progress,
    download_with_ytdlp,
    head_info,
)
from utils.uploader import upload_with_thumb_and_progress
from utils.progress import human_readable
from config import MAX_FILE_SIZE, NORMAL_COOLDOWN_SECONDS

URL_REGEX = r"https?://[^\s]+"

# chat_id -> state dict
PENDING_DOWNLOAD: dict[int, dict] = {}


def split_url_and_name(text: str):
    parts = text.split("|", 1)
    url_part = parts[0].strip()
    custom_name = parts[1].strip() if len(parts) > 1 else None
    return url_part, custom_name


def safe_filename(name: str) -> str:
    name = "".join(c for c in name if c not in "\\/:*?\"<>|")
    return name or "file"


def is_ytdlp_site(url: str) -> bool:
    # generic: yt-dlp bohot sites handle kar leta hai
    return True


def build_quality_keyboard(formats):
    buttons = []
    for f in formats:
        h = f["height"] or "?"
        size_str = human_readable(f["filesize"]) if f["filesize"] else "?"
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{h}p {f['ext']} ({size_str})",
                    callback_data=f"fmt_{f['format_id']}",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton("🌐 Direct URL se try karo", callback_data="direct_dl")]
    )
    return InlineKeyboardMarkup(buttons)


def register_url_handlers(app: Client):
    @app.on_message(
        filters.private
        & filters.text
        & ~filters.command(
            [
                "start",
                "help",
                "setthumb",
                "delthumb",
                "showthumb",
                "setcaption",
                "delcaption",
                "showcaption",
                "myplan",
                "spoiler_on",
                "spoiler_off",
                "screens_on",
                "screens_off",
                "sample_on",
                "sample_off",
                "setsample",
                "setprefix",
                "setsuffix",
                "rename",
                "setpremium",
                "delpremium",
                "setlimit",
                "userstats",
                "users",
                "stats",
                "botstatus",
                "ban",
                "unban",
                "broadcast",
                "banlist",
            ]
        )
    )
    async def handle_url(_, message: Message):
        chat_id = message.chat.id
        user_id = message.from_user.id

        if is_banned(user_id):
            return

        user = get_user_doc(user_id)

        # 1️⃣ RENAME MODE – user ne rename button ke baad naya naam bheja
        state = PENDING_DOWNLOAD.get(chat_id)
        if state and state.get("mode") == "await_new_name":
            new_name = message.text.strip()
            if re.search(URL_REGEX, new_name):
                await message.reply_text(
                    "❗ Abhi rename mode me ho.\n"
                    "Naya file name bhejo, example: `my_video.mp4`",
                    quote=True,
                )
                return

            new_name = safe_filename(new_name)
            if not new_name:
                await message.reply_text(
                    "❗ Sahi file name bhejo, example: `my_video.mp4`",
                    quote=True,
                )
                return

            state["custom_name"] = new_name
            state["filename"] = new_name

            if state["type"] == "yt":
                formats = state["formats"]
                text = (
                    "✅ File name set ho gaya.\n\n"
                    f"📄 File: `{state['filename']}`\n\n"
                    "🎥 Ab quality select karo:"
                )
                await message.reply_text(
                    text, reply_markup=build_quality_keyboard(formats)
                )
                state["mode"] = "await_quality"
                return

            if state["type"] == "direct":
                url = state["url"]
                filename = state["filename"]
                head_size = state.get("head_size", 0)

                if head_size > 0 and head_size > MAX_FILE_SIZE:
                    await message.reply_text(
                        f"⛔ File Telegram limit se badi hai.\nSize: {human_readable(head_size)}"
                    )
                    del PENDING_DOWNLOAD[chat_id]
                    return

                progress_msg = await message.reply_text("⬇️ Downloading...")
                try:
                    path, downloaded_bytes = await download_direct_with_progress(
                        url, filename, progress_msg
                    )
                    file_size = os.path.getsize(path)
                    if file_size > MAX_FILE_SIZE:
                        await message.reply_text(
                            "❌ File Telegram limit se badi hai, upload nahi ho sakti."
                        )
                        os.remove(path)
                        del PENDING_DOWNLOAD[chat_id]
                        return

                    update_stats(downloaded=downloaded_bytes, uploaded=0)
                    await upload_with_thumb_and_progress(
                        app, message, path, user_id, progress_msg
                    )
                except Exception as e:
                    await message.reply_text(f"❌ Error: `{e}`")
                finally:
                    if chat_id in PENDING_DOWNLOAD:
                        del PENDING_DOWNLOAD[chat_id]
                return

        # 2️⃣ NORMAL MODE – naya URL aaya hai

        text = message.text.strip()
        url_candidate, custom_name = split_url_and_name(text)
        match = re.search(URL_REGEX, url_candidate)
        if not match:
            # URL nahi hai → bot silent
            return

        url = match.group(0)

        # Cooldown (normal users only)
        if not user.get("is_premium", False) and NORMAL_COOLDOWN_SECONDS > 0:
            last_ts = user.get("last_upload_ts") or 0
            now = time.time()
            diff = now - last_ts
            if last_ts > 0 and diff < NORMAL_COOLDOWN_SECONDS:
                wait_left = int(NORMAL_COOLDOWN_SECONDS - diff)
                m, s = divmod(wait_left, 60)
                if m > 0:
                    wait_txt = f"{m}m {s}s"
                else:
                    wait_txt = f"{s}s"
                await message.reply_text(
                    "⏳ Thoda rukna padega.\n"
                    f"Agla upload {wait_txt} baad kar sakte ho.\n"
                    "Premium users ke liye cooldown nahi hota.",
                )
                return

        limit_c = user["daily_count_limit"]
        limit_s = user["daily_size_limit"]
        used_c = user["used_count_today"]
        used_s = user["used_size_today"]

        if limit_c and limit_c > 0 and used_c >= limit_c:
            await message.reply_text(
                f"⛔ Aaj ka upload count limit khatam.\n"
                f"Used: {used_c}/{limit_c}\n"
                "Admin se contact karo ya premium ke liye request karo."
            )
            return

        wait_msg = await message.reply_text("🔍 Link deep scan ho raha hai (`HEAD` + `yt-dlp`)...")

        head_size, head_ctype, head_fname = head_info(url)

        remaining_size = None
        if limit_s and limit_s > 0:
            remaining_size = max(limit_s - used_s, 0)

        if head_size > 0:
            if head_size > MAX_FILE_SIZE:
                await wait_msg.edit_text(
                    f"⛔ Single file size bohot bada hai.\n"
                    f"Size: {human_readable(head_size)} (> Telegram limit)"
                )
                return
            if remaining_size is not None and head_size > remaining_size:
                await wait_msg.edit_text(
                    "⛔ Aaj ka **daily size limit** exceed ho jayega is file se.\n"
                    f"Remain: {human_readable(remaining_size)}, File: {human_readable(head_size)}"
                )
                return

        # yt-dlp try
        try:
            formats, info = get_formats(url) if is_ytdlp_site(url) else ([], None)
        except Exception:
            formats, info = [], None

        # yt-dlp case
        if formats:
            title = info.get("title", head_fname or "video")

            filtered = []
            for f in formats:
                size = f.get("filesize") or 0
                if size and size > MAX_FILE_SIZE:
                    continue
                if remaining_size is not None and size and size > remaining_size:
                    continue
                filtered.append(f)

            use_formats = filtered if filtered else formats

            base_name = custom_name or f"{title}.mp4"
            base_name = safe_filename(base_name)

            thumb_url = info.get("thumbnail")

            PENDING_DOWNLOAD[chat_id] = {
                "type": "yt",
                "url": url,
                "user_id": user_id,
                "formats": use_formats,
                "title": title,
                "filename": base_name,
                "custom_name": custom_name,
                "head_size": head_size,
                "thumb_url": thumb_url,
                "mode": "await_name_choice",
            }

            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Default name", callback_data="name_default"
                        ),
                        InlineKeyboardButton("✏ Rename", callback_data="name_rename"),
                    ]
                ]
            )

            await wait_msg.edit_text(
                "✅ Deep scan complete.\n\n"
                f"🔗 URL: `{url}`\n"
                f"📄 Detected file name:\n`{base_name}`\n\n"
                "Neeche se naam choose karo:",
                reply_markup=kb,
            )
            return

        # direct file case
        await wait_msg.edit_text("🌐 Direct file download mode...")

        filename = head_fname or url.split("/")[-1] or "file"
        if len(filename) > 64:
            filename = "file_from_url"
        if custom_name:
            filename = custom_name
        filename = safe_filename(filename)

        PENDING_DOWNLOAD[chat_id] = {
            "type": "direct",
            "url": url,
            "user_id": user_id,
            "title": filename,
            "filename": filename,
            "custom_name": custom_name,
            "head_size": head_size,
            "mode": "await_name_choice",
        }

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Default name", callback_data="name_default"),
                    InlineKeyboardButton("✏ Rename", callback_data="name_rename"),
                ]
            ]
        )

        await wait_msg.edit_text(
            "✅ Deep scan complete.\n\n"
            f"🔗 URL: `{url}`\n"
            f"📄 Detected file name:\n`{filename}`\n\n"
            "Neeche se naam choose karo:",
            reply_markup=kb,
        )

    @app.on_callback_query()
    async def callbacks(client: Client, query):
        data = query.data
        chat_id = query.message.chat.id
        user_id = query.from_user.id
        msg = query.message

        # 0️⃣ HELP BUTTONS (no download state required)
        if data.startswith("help_"):
            user = get_user_doc(user_id)

            # Screenshot toggle
            if data == "help_ss_on":
                set_screenshots(user_id, True)
                await query.answer("✅ Screenshots ON (3 snaps per video).", show_alert=True)
                return
            if data == "help_ss_off":
                set_screenshots(user_id, False)
                await query.answer("✅ Screenshots OFF.", show_alert=True)
                return

            # Sample toggle
            if data == "help_sample_on":
                set_sample(user_id, True, None)
                await query.answer("✅ Sample clip ON (default 15s).", show_alert=True)
                return
            if data == "help_sample_off":
                set_sample(user_id, False, None)
                await query.answer("✅ Sample clip OFF.", show_alert=True)
                return

            # Thumb actions
            if data == "help_thumb_set":
                await query.answer(
                    "Thumbnail set karne ke liye kisi photo par reply karke /setthumb bhejo.",
                    show_alert=True,
                )
                return
            if data == "help_thumb_view":
                if not user.get("thumb_file_id"):
                    await query.answer("❌ Thumbnail set nahi hai.", show_alert=True)
                    return
                await client.send_photo(
                    chat_id=msg.chat.id,
                    photo=user["thumb_file_id"],
                    caption="🖼 Ye aapka current thumbnail hai.",
                )
                await query.answer("Thumbnail bhej diya.", show_alert=False)
                return
            if data == "help_thumb_del":
                set_thumb(user_id, None)
                await query.answer("✅ Thumbnail delete ho gaya.", show_alert=True)
                return

            # Caption actions
            if data == "help_cap_set":
                await query.answer(
                    "Caption set karne ke liye: /setcaption mera naya caption {file_name}",
                    show_alert=True,
                )
                return
            if data == "help_cap_view":
                cap = user.get("caption")
                if not cap:
                    await query.answer("❌ Caption set nahi hai.", show_alert=True)
                    return
                await client.send_message(
                    msg.chat.id, f"📝 Current caption:\n`{cap}`"
                )
                await query.answer("Caption bhej diya.", show_alert=False)
                return
            if data == "help_cap_del":
                set_caption(user_id, None)
                await query.answer("✅ Caption delete ho gaya.", show_alert=True)
                return

            # Upload type actions
            if data == "help_up_vid":
                set_upload_type(user_id, "video")
                await query.answer(
                    "✅ Ab URL se videos 'VIDEO' mode me upload honge.", show_alert=True
                )
                return
            if data == "help_up_doc":
                set_upload_type(user_id, "document")
                await query.answer(
                    "✅ Ab URL se videos 'DOCUMENT' (file) mode me upload honge.",
                    show_alert=True,
                )
                return

            return

        # 1️⃣ Download-related callbacks (require state)
        state = PENDING_DOWNLOAD.get(chat_id)
        if not state:
            await query.answer("⏱ Time out. Dubara URL bhejo.", show_alert=True)
            return

        url = state["url"]
        filename = state["filename"]
        head_size = state.get("head_size", 0)

        user = get_user_doc(user_id)
        limit_c = user["daily_count_limit"]
        limit_s = user["daily_size_limit"]
        used_c = user["used_count_today"]
        used_s = user["used_size_today"]

        if limit_c and limit_c > 0 and used_c >= limit_c:
            await msg.edit_text(
                f"⛔ Count limit exceed: {used_c}/{limit_c}\n" "Dubara kal try karo."
            )
            del PENDING_DOWNLOAD[chat_id]
            return

        remaining_size = None
        if limit_s and limit_s > 0:
            remaining_size = max(limit_s - used_s, 0)

        # Name selection step
        if data == "name_default":
            await query.answer("Default file name use hoga.", show_alert=False)
            if state["type"] == "yt":
                formats = state["formats"]
                await msg.edit_text(
                    "🎥 Video/streaming site detect hui.\n"
                    f"📄 File: `{state['filename']}`\n\n"
                    "Quality select karo:",
                    reply_markup=build_quality_keyboard(formats),
                )
                state["mode"] = "await_quality"
                return

            if state["type"] == "direct":
                if head_size > 0 and head_size > MAX_FILE_SIZE:
                    await msg.edit_text(
                        f"⛔ File Telegram limit se badi hai.\n"
                        f"Size: {human_readable(head_size)}"
                    )
                    del PENDING_DOWNLOAD[chat_id]
                    return
                if remaining_size is not None and head_size > 0 and head_size > remaining_size:
                    await msg.edit_text(
                        "⛔ Daily size limit exceed ho jayega is file se.\n"
                        f"Remain: {human_readable(remaining_size)}, File: {human_readable(head_size)}"
                    )
                    del PENDING_DOWNLOAD[chat_id]
                    return

                progress_msg = await msg.edit_text("⬇️ Downloading...")
                try:
                    path, downloaded_bytes = await download_direct_with_progress(
                        url, filename, progress_msg
                    )
                    file_size = os.path.getsize(path)
                    if file_size > MAX_FILE_SIZE:
                        await msg.edit_text(
                            "❌ File Telegram limit se badi hai, upload nahi ho sakti."
                        )
                        os.remove(path)
                        del PENDING_DOWNLOAD[chat_id]
                        return

                    if remaining_size is not None and file_size > remaining_size:
                        await msg.edit_text(
                            "⛔ Daily size limit exceed ho jayega is file se.\n"
                            f"Remain: {human_readable(remaining_size)}, File: {human_readable(file_size)}"
                        )
                        os.remove(path)
                        del PENDING_DOWNLOAD[chat_id]
                        return

                    update_stats(downloaded=downloaded_bytes, uploaded=0)
                    await upload_with_thumb_and_progress(
                        client, msg, path, user_id, progress_msg
                    )
                except Exception as e:
                    await msg.edit_text(f"❌ Error: `{e}`")
                finally:
                    if chat_id in PENDING_DOWNLOAD:
                        del PENDING_DOWNLOAD[chat_id]
                return

        if data == "name_rename":
            await query.answer("Naya file name bhejo (ext ke sath).", show_alert=True)
            state["mode"] = "await_new_name"
            await msg.reply_text(
                "✏ Naya file name bhejo (extension ke sath),\n"
                "example: `my_video.mp4`"
            )
            return

        # Direct download fallback
        if data == "direct_dl":
            await query.answer("Direct download try ho raha hai...", show_alert=False)
            progress_msg = await msg.edit_text("⬇️ Direct download try ho raha hai...")
            try:
                path, downloaded_bytes = await download_direct_with_progress(
                    url, filename, progress_msg
                )
            except Exception as e:
                await msg.edit_text(f"❌ Direct download fail: `{e}`")
                if os.path.exists(filename):
                    os.remove(filename)
                del PENDING_DOWNLOAD[chat_id]
           
