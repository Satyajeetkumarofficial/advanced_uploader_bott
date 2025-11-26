from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from database import get_user_doc, is_banned
from config import BOT_USERNAME
from utils.progress import human_readable


def register_start_handlers(app: Client):
    @app.on_message(filters.command("start"))
    async def start_cmd(_, message: Message):
        if is_banned(message.from_user.id):
            return

        user = get_user_doc(message.from_user.id)
        limit_count = user.get("daily_count_limit", 0)
        limit_size = user.get("daily_size_limit", 0)
        used_c = user.get("used_count_today", 0)
        used_s = user.get("used_size_today", 0)

        count_status = (
            f"{used_c}/{limit_count}" if limit_count and limit_count > 0 else f"{used_c}/∞"
        )
        size_status = (
            f"{human_readable(used_s)}/{human_readable(limit_size)}"
            if limit_size and limit_size > 0
            else f"{human_readable(used_s)}/∞"
        )

        await message.reply_text(
            f"👋 Namaste {message.from_user.first_name}!\n\n"
            f"Main @{BOT_USERNAME} hoon – Advanced URL Uploader Bot.\n\n"
            "Main kya kar sakta hoon:\n"
            "• Direct http/https download + yt-dlp deep scan\n"
            "• Quality select (1080p/720p/480p...) where supported\n"
            "• Rename: `URL | new_name.mp4`\n"
            "• Telegram file/video rename: `/rename new_name.ext` (reply)\n"
            "• Thumbnail, caption, spoiler, screenshots album, sample clip\n"
            "• Prefix/suffix naming, daily count + size limit, premium system\n\n"
            "🔗 URL format:\n"
            "`https://example.com/video.mp4`\n"
            "`URL | new_name.mp4`\n\n"
            "🎛 Quick settings ke liye `/help` use karo.\n\n"
            f"📊 Count today: {count_status}\n"
            f"📦 Size today: {size_status}",
            disable_web_page_preview=True,
        )

    @app.on_message(filters.command("help"))
    async def help_cmd(_, message: Message):
        if is_banned(message.from_user.id):
            return

        text = (
            "🤓 **Advanced URL Uploader Bot – Help**\n\n"
            "🔗 **URL Format**\n"
            "• Normal: `https://example.com/video.mp4`\n"
            "• Rename ke sath: `URL | new_name.mp4`\n\n"
            "📥 **Main Features**\n"
            "• Direct http/https download + yt-dlp deep scan\n"
            "• Quality select (1080p/720p/480p...)\n"
            "• Telegram file/video rename: `/rename new_name.ext` (reply)\n"
            "• Thumbnail, caption, spoiler, screenshots album, sample clip\n"
            "• Daily count + size limit, premium system, cooldown\n"
            "• Upload type: Video ya Document (URL se aaya file)\n\n"
            "🎛 Neeche buttons se quick settings control kar sakte ho "
            "(screenshots, sample, thumbnail, caption, upload type)."
        )

        kb = InlineKeyboardMarkup(
            [
                # row 1 – screenshots
                [
                    InlineKeyboardButton("📸 Screenshot ON", callback_data="help_ss_on"),
                    InlineKeyboardButton("📸 Screenshot OFF", callback_data="help_ss_off"),
                ],
                # row 2 – sample
                [
                    InlineKeyboardButton("🎬 Sample ON", callback_data="help_sample_on"),
                    InlineKeyboardButton("🎬 Sample OFF", callback_data="help_sample_off"),
                ],
                # row 3 – thumbnail
                [
                    InlineKeyboardButton("🖼 Thumb SET", callback_data="help_thumb_set"),
                    InlineKeyboardButton("👁 Thumb VIEW", callback_data="help_thumb_view"),
                    InlineKeyboardButton("🗑 Thumb DEL", callback_data="help_thumb_del"),
                ],
                # row 4 – caption
                [
                    InlineKeyboardButton("📝 Caption SET", callback_data="help_cap_set"),
                    InlineKeyboardButton("👁 Caption VIEW", callback_data="help_cap_view"),
                    InlineKeyboardButton("🗑 Caption DEL", callback_data="help_cap_del"),
                ],
                # row 5 – upload type
                [
                    InlineKeyboardButton(
                        "🎞 Upload as VIDEO", callback_data="help_up_vid"
                    ),
                    InlineKeyboardButton(
                        "📁 Upload as DOCUMENT", callback_data="help_up_doc"
                    ),
                ],
            ]
        )

        await message.reply_text(
            text, reply_markup=kb, disable_web_page_preview=True
      )
