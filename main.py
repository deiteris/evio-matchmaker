import asyncio
import sqlite3
import discord
import ssl
import logging
from random import randint
from datetime import datetime, timedelta
from aiohttp import web, ClientSession, BasicAuth
from discord.ext import commands, tasks
from custom_types import MatchmakingBot
from json import load
from traceback import format_exc

from evio.api import MatchmakingMatchInfoResponse
from evio import cog as evio

discord.utils.setup_logging()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = MatchmakingBot(
    command_prefix=commands.when_mentioned_or("/"),
    intents=intents
)

routes = web.RouteTableDef()

with open('config.json', encoding='utf-8') as f:
    cfg = load(f)


@tasks.loop(seconds=15)
async def timeout_matches():
    now = datetime.utcnow()
    ids_to_delete: list[str] = []
    async with bot.matches_lock:
        for m_id, m in bot.matches.items():
            # TODO: This is kinda suboptimal, but what to do... We'll have to poll all matches and check if any of them were cancelled
            if m.started_at is None:
                continue
            data = await m.get_match_data()
            status = data['status']
            # Everyone left at any stage
            if status == 'cancelled':
                logging.info(f'Match {m.match_id} was cancelled.')
                ids_to_delete.append(m_id)
            # No one has joined the match
            elif status == 'pending' and now - m.started_at > timedelta(minutes=2):
                logging.info(f'Match {m.match_id} is in pending for more than 2 minutes.')
                ids_to_delete.append(m_id)
            await asyncio.sleep(randint(0, 300) / 1000) # Sleep between 0-300ms
        for m_id in ids_to_delete:
            m = bot.matches[m_id]
            del bot.matches[m_id]
            m.cancel()
            for msg in m.user_messages.values():
                try:
                    await msg.edit(content='Match has been abandoned. Stats will not be tracked.', view=None)
                except:
                    pass


@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    await bot.tree.sync()
    timeout_matches.start()

# TODO: Move to "evio" module
@routes.post('/matchCallback')
async def matchCallback(req: web.Request):
    if not req.content_type.startswith('application/json'):
        return
    data: MatchmakingMatchInfoResponse = await req.json()
    match_data = data['match']
    match_data['map'] = int(match_data['map'])
    for team in match_data['teams']:
        for player in team['players']:
            player['account'] = int(player['account'])

    match_id = match_data['matchId']
    logging.info(f'Received callback for {match_id}')
    if match_id not in bot.matches:
        # TODO: Currently, matches don't persist between restarts. Respond with 200 to keep ev.io happy
        return web.json_response(status=200)

    async with bot.matches_lock:
        m = bot.matches[match_id]
        del bot.matches[match_id]

    try:
        res = m.finish(match_data)
    except:
        # To avoid spam in case there're any issues with the match
        logging.error(format_exc())
        for msg in m.user_messages.values():
            try:
                await msg.edit(content=f'Something went wrong with the match. Please notify @emojikage about the issue.', view=None)
            except:
                logging.error(format_exc())
        return web.json_response(status=200)
    embed = m.render_info(True, False)
    for msg in m.user_messages.values():
        try:
            await msg.edit(content=f'Match finished! {res}', embed=embed, view=None)
        except:
            logging.error(format_exc())
    return web.json_response(status=200)


async def main():
    async with bot:
        app = web.Application()
        app.add_routes(routes)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=None, port=8080)
        await site.start()

        client = ClientSession(raise_for_status=True)
        credentials = BasicAuth(cfg['evio_username'], cfg['evio_password'])

        # Match tracking
        bot.matches = {}
        bot.lobbies = {}
        bot.matches_lock = asyncio.Lock()
        bot.lobbies_lock = asyncio.Lock()

        # Initialize db connection
        bot.db = sqlite3.connect('bot.db')
        bot.db.execute('PRAGMA foreign_keys=ON')
        bot.db.row_factory = sqlite3.Row

        await bot.add_cog(evio.Evio(bot, client, credentials, cfg['callback_url']))
        await bot.start(cfg['token'])

if __name__ == '__main__':
    asyncio.run(main())
