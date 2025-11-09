import asyncio
import logging
from telethon import TelegramClient, events
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.types import ChannelParticipantsSearch
from telethon.errors import FloodWaitError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TelegramIDFinder:
    def __init__(self, api_id, api_hash, bot_token):
        self.client = TelegramClient('finder_session', api_id, api_hash)
        self.bot_token = bot_token
        self.active_searches = {}

    async def initialize(self):
        await self.client.start(bot_token=self.bot_token)
        logger.info("Бот инициализирован")

    async def find_users_by_id_suffix(self, chat_identifier, id_suffix):
        found_users = []
        
        try:
            chat = await self.client.get_entity(chat_identifier)
            chat_title = getattr(chat, 'title', 'Неизвестный чат')
            
            logger.info(f"Сканируем чат: {chat_title}")
            
            offset = 0
            limit = 100
            total_scanned = 0
            
            while True:
                try:
                    participants = await self.client(GetParticipantsRequest(
                        chat,
                        ChannelParticipantsSearch(''),
                        offset,
                        limit,
                        hash=0
                    ))
                    
                    if not participants.users:
                        break
                    
                    for user in participants.users:
                        total_scanned += 1
                        user_id_str = str(user.id)
                        
                        if user_id_str.endswith(id_suffix):
                            user_info = {
                                'id': user.id,
                                'username': user.username,
                                'first_name': user.first_name,
                                'last_name': user.last_name
                            }
                            found_users.append(user_info)
                    
                    if len(participants.users) < limit:
                        break
                        
                    offset += limit
                    
                    await asyncio.sleep(0.5)
                    
                except FloodWaitError as e:
                    logger.warning(f"Flood wait: {e.seconds} секунд")
                    await asyncio.sleep(e.seconds)
                    continue
                    
        except Exception as e:
            logger.error(f"Ошибка при сканировании чата: {e}")
            raise
        
        return found_users, total_scanned

    async def send_user_info(self, event, user):
        username = f"@{user['username']}" if user['username'] else "Нет юзернейма"
        name_parts = []
        if user['first_name']:
            name_parts.append(user['first_name'])
        if user['last_name']:
            name_parts.append(user['last_name'])
        full_name = ' '.join(name_parts) if name_parts else "Без имени"
        
        message = (
            f"👤 **Найден пользователь**\n"
            f"🆔 ID: `{user['id']}`\n"
            f"📛 Имя: {full_name}\n"
            f"🔗 Юзернейм: {username}"
        )
        
        await event.reply(message, parse_mode='markdown')

    async def handle_search_command(self, event):
        try:
            parts = event.text.split()
            if len(parts) < 3:
                await event.reply(
                    "❌ **Использование:**\n"
                    "`/search <чат> <окончание_ID>`\n\n"
                    "**Примеры:**\n"
                    "`/search @my_channel 123`\n"
                    "`/search https://t.me/channel 456`",
                    parse_mode='markdown'
                )
                return

            chat_identifier = parts[1]
            id_suffix = parts[2]

            if not id_suffix.isdigit() or len(id_suffix) != 3:
                await event.reply("⚠️ Окончание ID должно состоять из 3 цифр")
                return

            user_id = event.sender_id
            if user_id in self.active_searches:
                await event.reply(" У вас уже выполняется поиск. Дождитесь завершения.")
                return

            self.active_searches[user_id] = True
            status_msg = await event.reply(
                f"🔍 **Начинаю поиск...**\n"
                f"Чат: `{chat_identifier}`\n"
                f"Ищем ID с окончанием: `{id_suffix}`",
                parse_mode='markdown'
            )

            try:
                found_users, total_scanned = await self.find_users_by_id_suffix(
                    chat_identifier, id_suffix
                )
                if found_users:
                    await status_msg.edit(
                        f"✅ **Поиск завершен!**\n"
                        f"Просканировано: {total_scanned} пользователей\n"
                        f"Найдено совпадений: {len(found_users)}",
                        parse_mode='markdown'
                    )
                    
                    for user in found_users:
                        await self.send_user_info(event, user)
                        await asyncio.sleep(0.3)
                else:
                    await status_msg.edit(
                        f" **Совпадений не найдено**\n"
                        f"Просканировано: {total_scanned} пользователей\n"
                        f"Окончание ID: `{id_suffix}`",
                        parse_mode='markdown'
                    )

            except Exception as e:
                await status_msg.edit(f" Ошибка при поиске: {str(e)}")

        except Exception as e:
            await event.reply(f" Ошибка: {str(e)}")
        finally:
            self.active_searches.pop(event.sender_id, None)

    async def handle_help_command(self, event):
        help_text = """
**Бот для поиска пользователей по ID**

**Команды:**
 `/search <чат> <окончание_ID>` - поиск пользователей
 `/help` - эта справка

**Примеры:**
• `/search @public_chat 789` - найти в чате пользователей с ID ...789
• `/search https://t.me/channel 123` - найти в канале ID ...123

• Поиск может занять время в больших чатах
• Отображаются только публичные данные
        """
        await event.reply(help_text, parse_mode='markdown')

    async def setup_handlers(self):
        self.client.add_event_handler(
            self.handle_search_command,
            events.NewMessage(pattern='/search')
        )
        self.client.add_event_handler(
            self.handle_help_command, 
            events.NewMessage(pattern='/help')
        )
        self.client.add_event_handler(
            self.handle_help_command,
            events.NewMessage(pattern='/start')
        )

    async def run(self):
        await self.initialize()
        await self.setup_handlers()
        
        me = await self.client.get_me()
        logger.info(f"Бот @{me.username} запущен и готов к работе")
        
        await self.client.run_until_disconnected()


# Конфигурация
API_ID = 111  # API ID
API_HASH = ' '  # API Hash
BOT_TOKEN = ''  # Токен


async def main():
    finder_bot = TelegramIDFinder(API_ID, API_HASH, BOT_TOKEN)
    await finder_bot.run()


if __name__ == '__main__':
    if hasattr(asyncio, 'WindowsSelectorEventLoopPolicy'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())