from discord.ext.commands import Bot
from sqlite3 import Connection
from evio.mm.lobby import MatchmakingLobby, CustomLobby
from asyncio import Lock

class MatchmakingBot(Bot):
    db: Connection
    # Stores lobbies with running matches
    matches: dict[str, MatchmakingLobby | CustomLobby]
    matches_lock: Lock
    # Stores lobbies with pending matches
    lobbies: dict[str, MatchmakingLobby | CustomLobby]
    lobbies_lock: Lock