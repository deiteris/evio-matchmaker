import sqlite3
from json import dumps
from datetime import datetime
from typing import TypedDict, Optional
from enum import IntEnum

from .api import EvioUserInfo, MatchStatus

TABLE_PREFIX = 'evio'
# TODO: Maybe need to store in the DB
MAPS_POOL = [
    232, 724, 698,
    690, 752, 682,
    449, 275, 276,
    748, 120, 131,
    132, 234, 191
]


class MatchmakingRegionEnum(IntEnum):
    AMSTERDAM = 0
    SAN_FRANCISCO = 1
    NEW_JERSEY = 2
    SINGAPORE = 3

    __MAPPING__ = {
        'amsterdam': AMSTERDAM,
        'san-francisco': SAN_FRANCISCO,
        'new-jersey': NEW_JERSEY,
        'singapore': SINGAPORE
    }

    __REVERSE__ = {
        AMSTERDAM: 'amsterdam',
        SAN_FRANCISCO: 'san-francisco',
        NEW_JERSEY: 'new-jersey',
        SINGAPORE: 'singapore'
    }

    __LABELS__ = {
        AMSTERDAM: 'Amsterdam',
        SAN_FRANCISCO: 'San Francisco',
        NEW_JERSEY: 'New Jersey',
        SINGAPORE: 'Singapore'
    }

    @classmethod
    def from_value(cls, value: str) -> 'MatchmakingRegionEnum':
        return cls.__MAPPING__[value]

    @classmethod
    def to_value(cls, value: 'MatchmakingRegionEnum') -> str:
        return cls.__REVERSE__[value]

    @classmethod
    def label(cls, value: 'MatchmakingRegionEnum') -> str:
        return cls.__LABELS__[value]


class MatchStatusEnum(IntEnum):
    RUNNING = 0
    PENDING = 1
    CANCELLED = 2
    COMPLETE = 3

    __MAPPING__ = {
        'running': RUNNING,
        'pending': PENDING,
        'cancelled': CANCELLED,
        'complete': COMPLETE
    }

    __REVERSE__ = {
        RUNNING: 'running',
        PENDING: 'pending',
        CANCELLED: 'cancelled',
        COMPLETE: 'complete'
    }

    @classmethod
    def from_value(cls, value: str) -> 'MatchStatusEnum':
        return cls.__MAPPING__[value]

    @classmethod
    def to_value(cls, value: 'MatchStatusEnum') -> str:
        return cls.__REVERSE__[value]

    def label(value: 'MatchStatusEnum') -> str:
        return value.name.capitalize()


class GameMode(IntEnum):
    Casual = 0
    Competitive = 1


class League(IntEnum):
    Custom = 0
    Solo = 1
    Duo = 2
    Trio = 3
    Quadro = 4
    Penta = 5


class DBPlayer(TypedDict):
    user_id: int
    name: str
    discord_id: int


class DBPlayerWithStats(DBPlayer):
    won: int
    draw: int
    lost: int
    kills: int
    deaths: int
    assists: int
    mmr: int


class DBDeployedMember(TypedDict):
    user_id: int
    user_uuid: str
    earned_cp: int
    manual_deploy: bool
    deployed_at: int


class DBStatsChange(TypedDict):
    user_id: int
    league_id: int
    won: int
    lost: int
    draw: int
    kills: int
    deaths: int
    assists: int
    mmr: int


class DBBlobPlayerInfo(TypedDict):
    name: str
    kills: int
    deaths: int
    assists: int
    score: int
    mmr: int


class DBBlobTeamInfo(TypedDict):
    placement: int
    players: DBBlobPlayerInfo


class DBBlobMatchConfig(TypedDict):
    damageMultiplier: float
    duration: int
    killsToWin: int
    gameMode: str
    gravity: float
    timeVelocity: int
    region: str
    map: int


class DBLeague(TypedDict):
    name: str
    team_size: int
    match_config: str


class DBHistoricalMatch(TypedDict):
    status: int # MatchStatusEnum
    mode_id: int
    league_id: int
    match_id: str
    config: str
    teams: str
    map: int
    region: int # MatchmakingRegionEnum
    comment: str | None
    created_at: int


class MatchData(TypedDict):
    match_id: str
    status: MatchStatus
    league_id: int
    mode_id: int
    config: DBBlobMatchConfig
    teams: list[DBBlobTeamInfo, DBBlobTeamInfo]
    map: int
    region: str
    comment: str | None


class EvioDB:

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.init()


    def init(self):
        self.db.execute(f'''
            CREATE TABLE IF NOT EXISTS {TABLE_PREFIX}_players (
                user_id BIGINT PRIMARY KEY,
                name NVARCHAR UNIQUE,
                deleted_at BIGINT
            ) WITHOUT ROWID'''
        )
        self.db.execute(f'''
            CREATE TABLE IF NOT EXISTS {TABLE_PREFIX}_discord_integration (
                user_id BIGINT PRIMARY KEY REFERENCES {TABLE_PREFIX}_players(user_id) ON DELETE CASCADE,
                discord_id BIGINT UNIQUE
            ) WITHOUT ROWID'''
        )
        self.db.execute(f'''
            CREATE TABLE IF NOT EXISTS {TABLE_PREFIX}_player_settings (
                user_id BIGINT PRIMARY KEY REFERENCES {TABLE_PREFIX}_players(user_id) ON DELETE CASCADE,
                regions TEXT,
                maps TEXT
            ) WITHOUT ROWID'''
        )
        self.db.execute(f'''
            CREATE TABLE IF NOT EXISTS {TABLE_PREFIX}_bans (
                user_id BIGINT PRIMARY KEY REFERENCES {TABLE_PREFIX}_players(user_id) ON DELETE CASCADE,
                reason NVARCHAR(255)
            ) WITHOUT ROWID'''
        )
        self.db.execute(f'''
            CREATE TABLE IF NOT EXISTS {TABLE_PREFIX}_leagues (
                league_id BIGINT PRIMARY KEY,
                name NVARCHAR,
                team_size INT,
                match_config TEXT
            ) WITHOUT ROWID'''
        )
        self.db.execute(f'''
            CREATE TABLE IF NOT EXISTS {TABLE_PREFIX}_competitive_stats (
                user_id BIGINT REFERENCES {TABLE_PREFIX}_players(user_id) ON DELETE CASCADE,
                league_id BIGINT REFERENCES {TABLE_PREFIX}_leagues(league_id) ON DELETE CASCADE,
                won BIGINT DEFAULT 0,
                lost BIGINT DEFAULT 0,
                draw BIGINT DEFAULT 0,
                kills BIGINT DEFAULT 0,
                deaths BIGINT DEFAULT 0,
                assists BIGINT DEFAULT 0,
                mmr BIGINT DEFAULT 2000
            )'''
        )
        self.db.execute(f'''
            CREATE TABLE IF NOT EXISTS {TABLE_PREFIX}_players_history (
                user_id BIGINT REFERENCES {TABLE_PREFIX}_players(user_id) ON DELETE CASCADE,
                match_id VARCHAR(36) REFERENCES {TABLE_PREFIX}_matches_history(match_id) ON DELETE CASCADE
            )'''
        )
        self.db.execute(f'''
            CREATE TABLE IF NOT EXISTS {TABLE_PREFIX}_matches_history (
                match_id VARCHAR(36) PRIMARY KEY,
                league_id BIGINT REFERENCES {TABLE_PREFIX}_leagues(league_id) ON DELETE CASCADE,
                mode_id BIGINT,
                status TINYINT,
                config TEXT,
                teams TEXT,
                map INT,
                region TINYINT,
                comment NVARCHAR(255),
                created_at BIGINT
            ) WITHOUT ROWID'''
        )
        self.db.execute(f'''
            CREATE INDEX IF NOT EXISTS matches_created_at_idx ON {TABLE_PREFIX}_matches_history(created_at)'''
        )
        self.db.executemany(f'''
            INSERT OR IGNORE INTO {TABLE_PREFIX}_leagues VALUES (?,?,?,?)''', (
                (League.Custom.value,   League.Custom.name,    -1, '{"damageMultiplier":1,"duration":300,"gameMode":"team_deathmatch","gravity":0.07,"killsToWin":30,"timeVelocity":1}'),
                (League.Solo.value,     League.Solo.name,       1, '{"damageMultiplier":1,"duration":300,"gameMode":"team_deathmatch","gravity":0.07,"killsToWin":25,"timeVelocity":1}'),
                (League.Duo.value,      League.Duo.name,        2, '{"damageMultiplier":1,"duration":480,"gameMode":"team_deathmatch","gravity":0.07,"killsToWin":50,"timeVelocity":1}'),
                (League.Trio.value,     League.Trio.name,       3, '{"damageMultiplier":1,"duration":480,"gameMode":"team_deathmatch","gravity":0.07,"killsToWin":50,"timeVelocity":1}'),
                (League.Quadro.value,   League.Quadro.name,     4, '{"damageMultiplier":1,"duration":900,"gameMode":"team_deathmatch","gravity":0.07,"killsToWin":150,"timeVelocity":1}'),
                (League.Penta.value,    League.Penta.name,      5, '{"damageMultiplier":1,"duration":900,"gameMode":"team_deathmatch","gravity":0.07,"killsToWin":250,"timeVelocity":1}'),
            )
        )
        self.db.commit()


    def get_player(self, user_id: int, *fields: str) -> DBPlayer | None:
        return self.db.execute(f'SELECT {",".join(fields)} FROM {TABLE_PREFIX}_players WHERE user_id = ?', (user_id,)).fetchone()


    def get_players(self, *fields: str) -> list[DBPlayer]:
        return self.db.execute(f'SELECT {",".join(fields)} FROM {TABLE_PREFIX}_players').fetchall()


    def get_player_by_discord_id(self, discord_id: int, *fields: str) -> Optional[DBPlayer]:
        return self.db.execute(f'SELECT {",".join(fields)} FROM {TABLE_PREFIX}_players AS p LEFT JOIN {TABLE_PREFIX}_discord_integration AS i ON i.user_id = p.user_id WHERE i.discord_id = ?', (discord_id,)).fetchone()


    def get_player_with_stats(self, discord_id: int, league_id: int, *fields: str) -> Optional[DBPlayerWithStats]:
        return self.db.execute(f'SELECT {",".join([field for field in fields])} FROM {TABLE_PREFIX}_competitive_stats AS s LEFT JOIN {TABLE_PREFIX}_players AS p ON p.user_id = s.user_id LEFT JOIN {TABLE_PREFIX}_discord_integration AS i ON i.user_id = p.user_id WHERE i.discord_id = ? AND s.league_id = ?', (discord_id, league_id)).fetchone()


    def get_top_10_players(self, league_id: int, page: int, *fields: str) -> list[DBPlayerWithStats]:
        return self.db.execute(f'SELECT ROW_NUMBER() OVER (ORDER BY s.mmr DESC) AS pos, {",".join([field for field in fields])} FROM {TABLE_PREFIX}_competitive_stats AS s LEFT JOIN {TABLE_PREFIX}_players AS p ON p.user_id = s.user_id WHERE s.league_id = ? AND p.deleted_at IS NULL LIMIT 10 OFFSET {page * 10}', (league_id,)).fetchall()


    def get_league_data(self, league_id: int, *fields: str) -> DBLeague:
        return self.db.execute(f'SELECT {",".join([field for field in fields])} FROM {TABLE_PREFIX}_leagues WHERE league_id = ?', (league_id,)).fetchone()


    def remove_player(self, discord_id: int):
        self.db.execute(f'UPDATE {TABLE_PREFIX}_players AS p SET deleted_at = ? FROM (SELECT user_id FROM {TABLE_PREFIX}_discord_integration WHERE discord_id = ?) AS i WHERE p.user_id = i.user_id', (int(datetime.utcnow().timestamp()), discord_id))
        self.db.execute(f'DELETE FROM {TABLE_PREFIX}_discord_integration WHERE discord_id = ?', (discord_id,))
        self.db.commit()


    def register_player(self, user: EvioUserInfo, discord_id: int):
        user_id = user['uid'][0]['value']
        self.db.execute(f'INSERT INTO {TABLE_PREFIX}_players(user_id, name) VALUES (?,?)', (user_id, user['name'][0]['value']))
        self.db.execute(f'INSERT INTO {TABLE_PREFIX}_discord_integration VALUES (?,?)', (user_id, discord_id))
        self.db.execute(f'INSERT INTO {TABLE_PREFIX}_player_settings(user_id, regions, maps) VALUES (?,?,?)', (user_id, dumps([MatchmakingRegionEnum.AMSTERDAM], separators=(',', ':')), dumps(MAPS_POOL, separators=(',', ':'))))
        self.db.executemany(f'INSERT INTO {TABLE_PREFIX}_competitive_stats (user_id, league_id) VALUES (?,?)', [(user_id, e.value) for e in League])
        self.db.commit()


    def update_player_registration(self, user_id: int, discord_id: int):
        self.db.execute(f'UPDATE {TABLE_PREFIX}_players SET deleted_at = NULL WHERE user_id = ?', (user_id,))
        self.db.execute(f'INSERT INTO {TABLE_PREFIX}_discord_integration VALUES (?,?)', (user_id, discord_id))
        self.db.commit()


    def update_players_stats(self, data: list[DBStatsChange]):
        self.db.executemany(f'UPDATE {TABLE_PREFIX}_competitive_stats SET won = won + :won, lost = lost + :lost, draw = draw + :draw, kills = kills + :kills, deaths = deaths + :deaths, assists = assists + :assists, mmr = mmr + :mmr WHERE user_id = :user_id AND league_id = :league_id', data)
        self.db.commit()


    def insert_match(self, data: MatchData, user_ids: list[int]):
        self.db.execute(f'INSERT INTO {TABLE_PREFIX}_matches_history VALUES (?,?,?,?,?,?,?,?,?,?)', (data['match_id'], data['league_id'], data['mode_id'], data['status'], dumps(data['config'], separators=(',', ':')), dumps(data['teams'], separators=(',', ':')), data['map'], data['region'], data['comment'], int(datetime.utcnow().timestamp())))
        self.db.executemany(f'INSERT INTO {TABLE_PREFIX}_players_history VALUES (?,?)', [(user_id, data['match_id']) for user_id in user_ids])
        self.db.commit()


    def get_player_settings(self, user_id: int, *fields: str) -> dict | None:
        return self.db.execute(f'SELECT {",".join(fields)} FROM {TABLE_PREFIX}_player_settings WHERE user_id = ?', (user_id,)).fetchone()


    def set_player_settings(self, user_id: int, *, regions: list[str] | None = None, maps: list[int] | None = None):
        if regions is None and maps is None:
            return
        query = []
        data = []
        if regions is not None:
            query.append('regions = ?')
            data.append(dumps(regions, separators=(',', ':')))
        if maps is not None:
            query.append('maps = ?')
            data.append(dumps(maps, separators=(',', ':')))
        data.append(user_id)
        self.db.execute(f'UPDATE {TABLE_PREFIX}_player_settings SET {",".join(query)} WHERE user_id = ?', data)
        self.db.commit()

    # TODO: Pagination
    def get_player_match_history(self, discord_id: int, start_from: int = 0, limit: int = 25) -> list[DBHistoricalMatch]:
        return self.db.execute(f'SELECT mh.status, mh.league_id, mh.mode_id, mh.match_id, mh.config, mh.teams, mh.map, mh.region, mh.comment, mh.created_at FROM {TABLE_PREFIX}_matches_history AS mh LEFT JOIN {TABLE_PREFIX}_players_history AS ph ON mh.match_id = ph.match_id LEFT JOIN {TABLE_PREFIX}_discord_integration AS i ON i.user_id = ph.user_id WHERE i.discord_id = ? ORDER BY mh.created_at DESC LIMIT ?', (discord_id, limit)).fetchall()
