import asyncio
import logging
import csv
import os
import json
import random
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.types import ChannelParticipantsSearch
from telethon.errors import FloodWaitError

 
import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TelegramSessionManager:
    def __init__(self, sessions_folder=config.SESSIONS_FOLDER, api_id=config.BOT_API_ID, api_hash=config.BOT_API_HASH):
        self.sessions_folder = sessions_folder
        self.api_id = api_id
        self.api_hash = api_hash
        self.active_clients = {}
        self.available_sessions = []
        
        if not os.path.exists(sessions_folder):
            os.makedirs(sessions_folder)
        
        self._load_sessions()

    def _load_sessions(self):
        self.available_sessions = []
        for file in os.listdir(self.sessions_folder):
            if file.endswith('.session'):
                session_name = file.replace('.session', '')
                self.available_sessions.append(session_name)
        logger.info(f"Загружено сессий: {len(self.available_sessions)}")

    def get_sessions_count(self):
        return len(self.available_sessions)

    async def get_random_client(self):
        if not self.available_sessions:
            return None
        
        session_name = random.choice(self.available_sessions)
        return await self.get_client(session_name)

    async def get_client(self, session_name):
        if session_name in self.active_clients:
            return self.active_clients[session_name]
        
        session_path = os.path.join(self.sessions_folder, f"{session_name}.session")
        if not os.path.exists(session_path):
            return None
        
        try:
            client = TelegramClient(session_path, self.api_id, self.api_hash)
            await client.start()
            self.active_clients[session_name] = client
            logger.info(f"Сессия {session_name} успешно запущена")
            return client
        except Exception as e:
            logger.error(f"Ошибка запуска сессии {session_name}: {e}")
            return None

    async def close_all(self):
        for client in self.active_clients.values():
            try:
                await client.disconnect()
            except:
                pass
        self.active_clients.clear()


class TelegramIDFinder:
    def __init__(self, api_id=config.BOT_API_ID, api_hash=config.BOT_API_HASH, bot_token=config.BOT_TOKEN, 
                 csv_file=config.CHATS_CSV_FILE, sessions_folder=config.SESSIONS_FOLDER):
        self.bot_client = TelegramClient('bot_session', api_id, api_hash)
        self.bot_token = bot_token
        self.active_searches = {}
        self.csv_file = csv_file
        self.session_manager = TelegramSessionManager(sessions_folder, api_id, api_hash)
        
        if not os.path.exists(csv_file):
            with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['chat_link'])

    async def initialize(self):
        await self.bot_client.start(bot_token=self.bot_token)
        logger.info("Бот инициализирован")
        
        sessions_count = self.session_manager.get_sessions_count()
        if sessions_count == 0:
            logger.warning("В папке sessions не найдено сессий для парсинга!")
        else:
            logger.info(f"Доступно сессий для парсинга: {sessions_count}")

    def read_chats_from_csv(self):
        chats = []
        try:
            with open(self.csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('chat_link'):
                        chats.append(row['chat_link'].strip())
        except Exception as e:
            logger.error(f"Ошибка чтения CSV: {e}")
        return chats

    def add_chat_to_csv(self, chat_link):
        try:
            with open(self.csv_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([chat_link])
            return True
        except Exception as e:
            logger.error(f"Ошибка записи в CSV: {e}")
            return False

    async def find_users_by_id_suffix(self, chat_identifier, id_suffix, session_client):
        found_users = []
        total_scanned = 0
        error_message = None

        try:
            chat = await session_client.get_entity(chat_identifier)
            chat_title = getattr(chat, 'title', 'Неизвестный чат')
            
            offset = 0
            limit = 100
            
            while True:
                try:
                    participants = await session_client(GetParticipantsRequest(
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
                                'last_name': user.last_name,
                                'chat_title': chat_title,
                                'found_at': datetime.now().isoformat()
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
            error_message = str(e)
        
        return found_users, total_scanned, error_message

    async def search_multiple_chats_parallel(self, chat_identifiers, id_suffix):
        all_found_users = []
        total_scanned = 0
        failed_chats = []
        tasks = []
        for chat_identifier in chat_identifiers:
            task = self.search_single_chat_with_session(chat_identifier, id_suffix)
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, result in enumerate(results):
            chat_identifier = chat_identifiers[i]
            
            if isinstance(result, Exception):
                failed_chats.append({
                    "chat": chat_identifier,
                    "error": str(result)
                })
                continue
                
            found_users, scanned, error = result
            all_found_users.extend(found_users)
            total_scanned += scanned
            
            if error:
                failed_chats.append({
                    "chat": chat_identifier,
                    "error": error
                })
        
        return all_found_users, total_scanned, failed_chats

    async def search_single_chat_with_session(self, chat_identifier, id_suffix):
        session_client = await self.session_manager.get_random_client()
        if not session_client:
            return [], 0, "Нет доступных сессий для парсинга"
        
        return await self.find_users_by_id_suffix(chat_identifier, id_suffix, session_client)

    def create_json_response(self, data):
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        return f"```json\n{json_str}\n```"

    async def handle_start_command(self, event):
        chats = self.read_chats_from_csv()
        sessions_count = self.session_manager.get_sessions_count()
        
        response_data = {
            "status": "active",
            "chats_in_database": len(chats),
            "sessions_for_parsing": sessions_count,
            "developer": "@yoxiko",
            "note": "Бот не нарушает правила Telegram, все данные из публичных чатов и открытх источников.",
            "timestamp": datetime.now().isoformat()
        }
        
        await event.reply(self.create_json_response(response_data))

    async def handle_search_command(self, event):
        try:
            parts = event.text.split()
            if len(parts) < 3:
                response_data = {
                    "error": "Invalid command format",
                    "usage": "/search <чаты> <окончание_ID>",
                    "examples": [
                        "/search @chat1,@chat2 123",
                        "/search csv 123", 
                        "/search https://t.me/channel 456"
                    ]
                }
                await event.reply(self.create_json_response(response_data))
                return

            chats_param = parts[1]
            id_suffix = parts[2]

            if not id_suffix.isdigit() or len(id_suffix) != 3:
                response_data = {
                    "error": "ID suffix must be 3 digits"
                }
                await event.reply(self.create_json_response(response_data))
                return

            user_id = event.sender_id
            if user_id in self.active_searches:
                response_data = {
                    "error": "Search already in progress"
                }
                await event.reply(self.create_json_response(response_data))
                return

            self.active_searches[user_id] = True

            if chats_param.lower() == 'csv':
                chat_identifiers = self.read_chats_from_csv()
                if not chat_identifiers:
                    response_data = {
                        "error": "No chats in database"
                    }
                    await event.reply(self.create_json_response(response_data))
                    return
                search_type = "database"
            else:
                chat_identifiers = [chat.strip() for chat in chats_param.split(',')]
                chat_identifiers = chat_identifiers[:100]
                search_type = "manual"

            sessions_count = self.session_manager.get_sessions_count()
            if sessions_count == 0:
                response_data = {
                    "error": "No parsing sessions available"
                }
                await event.reply(self.create_json_response(response_data))
                return

            search_id = f"{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            start_status = {
                "search_id": search_id,
                "status": "started",
                "search_type": search_type,
                "chats_count": len(chat_identifiers),
                "id_suffix": id_suffix,
                "available_sessions": sessions_count,
                "start_time": datetime.now().isoformat()
            }
            
            await event.reply(self.create_json_response(start_status))

            found_users, total_scanned, failed_chats = await self.search_multiple_chats_parallel(
                chat_identifiers, id_suffix
            )
            
            result_data = {
                "search_id": search_id,
                "status": "completed",
                "search_type": search_type,
                "statistics": {
                    "chats_processed": len(chat_identifiers),
                    "chats_failed": len(failed_chats),
                    "total_users_scanned": total_scanned,
                    "users_found": len(found_users)
                },
                "found_users": found_users,
                "failed_chats": failed_chats,
                "end_time": datetime.now().isoformat()
            }
            
            await event.reply(self.create_json_response(result_data))

        except Exception as e:
            error_data = {
                "error": str(e)
            }
            await event.reply(self.create_json_response(error_data))
        finally:
            self.active_searches.pop(event.sender_id, None)

    async def handle_addchat_command(self, event):
        try:
            parts = event.text.split()
            if len(parts) < 2:
                response_data = {
                    "error": "Invalid command format",
                    "usage": "/addchat <chat_link>"
                }
                await event.reply(self.create_json_response(response_data))
                return

            chat_link = parts[1]
            
            session_client = await self.session_manager.get_random_client()
            if not session_client:
                response_data = {
                    "error": "No parsing sessions available"
                }
                await event.reply(self.create_json_response(response_data))
                return

            try:
                chat = await session_client.get_entity(chat_link)
                chat_title = getattr(chat, 'title', 'Unknown chat')
                
                if self.add_chat_to_csv(chat_link):
                    response_data = {
                        "status": "success",
                        "chat_added": {
                            "link": chat_link,
                            "title": chat_title
                        }
                    }
                else:
                    response_data = {
                        "error": "Failed to add chat to database"
                    }
                    
                await event.reply(self.create_json_response(response_data))
                    
            except Exception as e:
                response_data = {
                    "error": f"Chat not found: {str(e)}"
                }
                await event.reply(self.create_json_response(response_data))

        except Exception as e:
            response_data = {
                "error": str(e)
            }
            await event.reply(self.create_json_response(response_data))

    async def handle_listchats_command(self, event):
        chats = self.read_chats_from_csv()
        
        response_data = {
            "chats_count": len(chats),
            "chats": chats
        }
        
        await event.reply(self.create_json_response(response_data))

    async def handle_stats_command(self, event):
        chats = self.read_chats_from_csv()
        sessions_count = self.session_manager.get_sessions_count()
        
        response_data = {
            "database": {
                "chats_count": len(chats),
                "file": self.csv_file
            },
            "sessions": {
                "available_count": sessions_count,
                "folder": self.session_manager.sessions_folder
            },
            "system": {
                "active_searches": len(self.active_searches),
                "timestamp": datetime.now().isoformat()
            },
            "developer": "@yoxiko"
        }
        
        await event.reply(self.create_json_response(response_data))

    async def setup_handlers(self):
        self.bot_client.add_event_handler(
            self.handle_start_command,
            events.NewMessage(pattern='/start')
        )
        self.bot_client.add_event_handler(
            self.handle_search_command,
            events.NewMessage(pattern='/search')
        )
        self.bot_client.add_event_handler(
            self.handle_addchat_command,
            events.NewMessage(pattern='/addchat')
        )
        self.bot_client.add_event_handler(
            self.handle_listchats_command,
            events.NewMessage(pattern='/listchats')
        )
        self.bot_client.add_event_handler(
            self.handle_stats_command,
            events.NewMessage(pattern='/stats')
        )

    async def run(self):
        await self.initialize()
        await self.setup_handlers()
        
        me = await self.bot_client.get_me()
        logger.info(f"Бот @{me.username} запущен")
        
        sessions_count = self.session_manager.get_sessions_count()
        if sessions_count == 0:
            logger.error("НЕТ ДОСТУПНЫХ СЕССИЙ ДЛЯ ПАРСИНГА!")
            logger.error(f"Положите файлы .session в папку: {os.path.abspath(self.session_manager.sessions_folder)}")
        else:
            logger.info(f"Доступно сессий для парсинга: {sessions_count}")
        
        await self.bot_client.run_until_disconnected()

    async def close(self):
        await self.session_manager.close_all()
        await self.bot_client.disconnect()


async def main():
    finder_bot = TelegramIDFinder()
    try:
        await finder_bot.run()
    finally:
        await finder_bot.close()


if __name__ == '__main__':
    if hasattr(asyncio, 'WindowsSelectorEventLoopPolicy'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())