from abc import abstractmethod, ABC
from json import loads
from discord import Embed, Color, Message, User
from typing import Any, TypedDict
from datetime import datetime

from evio.api import EvioMap, EvioApiClient, MatchmakingMatchInfoRequest, MatchmakingTeamInfo, MatchmakingDatacenter, MatchmakingPlayerInfo, CreatedMatchInfo
from evio.db import EvioDB, DBStatsChange, DBBlobTeamInfo, DBBlobPlayerInfo, League, GameMode, MatchData, DBPlayerWithStats, MatchmakingRegionEnum, MatchStatusEnum

# If I want 2-step threshold, then I need additional value that will be used
MMR_DIFF_THRESHOLD = 500
# MMR_DIMINISHING_THRESHOLD = MMR_DIFF_THRESHOLD * 2
ADDITIONAL_MMR_RATE = 20
# DIMINISHING_MMR_RATE = 10
BASE_MMR_RATE = 30 # 10 points per match outcome


def map_value(x: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min


def calc_mmr_bonus(mmr_diff: int) -> float:
    return min(ADDITIONAL_MMR_RATE, map_value(mmr_diff, 0, MMR_DIFF_THRESHOLD, 0, ADDITIONAL_MMR_RATE))


def get_mmr_bonus(diff: float, won: bool) -> int:
    if diff < -MMR_DIFF_THRESHOLD or diff > MMR_DIFF_THRESHOLD:
        return -ADDITIONAL_MMR_RATE
    elif diff < 0 and not won or diff > 0 and won:
        return -round(calc_mmr_bonus(abs(diff)))
    return round(calc_mmr_bonus(abs(diff)))


def get_rating_diff(player_mmr: int, enemy_avg_mmr: int) -> int:
    return player_mmr - enemy_avg_mmr


MAPS_POOL = [
    232, 724, 698,
    690, 752, 682,
    449, 275, 276,
    748, 120, 131,
    132, 234, 191
]

TEAM_MAP = {
    0: 'Red',
    1: 'Blue'
}

MATCH_INFO_MAP = {
    'damageMultiplier': 'Damage multiplier',
    'duration': 'Duration in seconds',
    'killsToWin': 'Kills to win',
    'gameMode': 'Game mode',
    'gravity': 'Gravity',
    'timeVelocity': 'Time velocity multiplier',
    'region': 'Region',
    'gameMode': 'Game mode'
}

class LobbyPlayerInfo(TypedDict):
    name: str
    mvp_count: int
    mmr: int

class LobbyTeamInfo(TypedDict):
    win_count: int
    avg_mmr: int
    players: dict[int, LobbyPlayerInfo]


def get_avg_team_mmr(players: list[LobbyPlayerInfo]) -> int:
    if not len(players):
        return 0
    return sum([player["mmr"] for player in players]) // len(players)


class AbstractLobby(ABC):

    def __init__(self, api: EvioApiClient, db: EvioDB, map: EvioMap, league: League, mode: GameMode, callback_url: str, creator: User) -> None:
        self.db = db
        self.api = api

        self.creator = creator
        self.callback_url = callback_url
        self.league_data = self.db.get_league_data(league.value, 'match_config', 'team_size')
        self.match_config: dict = loads(self.league_data['match_config'])
        self.map = map
        self.region = MatchmakingRegionEnum.AMSTERDAM

        self.league = league
        self.mode = mode

        self.teams: tuple[LobbyTeamInfo] = (
            LobbyTeamInfo(win_count=0, avg_mmr=0, players={}),
            LobbyTeamInfo(win_count=0, avg_mmr=0, players={}),
            LobbyTeamInfo(players={})
        )

        # TODO: Maybe there's a better solution rather than making a reverse lookup table?
        self.discord_player_map: dict[int, dict[str, Any]] = {}
        self.user_messages: dict[int, Message] = {}
        self.match_id: str | None = None
        self.created_at = datetime.utcnow()
        self.started_at: datetime | None = None
        self.winner: int | None = None


    def join(self, team_number: int, member: DBPlayerWithStats, discord_id: int) -> str | None:
        team = self.teams[team_number]
        players = team['players']
        team_size = self.league_data['team_size']
        if team_number != 2 and team_size != -1 and len(players) >= team_size:
            return 'Team is full.'
        if discord_id in self.discord_player_map:
            self.leave(discord_id)
        user_id = member['user_id']
        players[user_id] = LobbyPlayerInfo(name=member['name'], mvp_count=0, mmr=member['mmr'])
        self.discord_player_map[discord_id] = { 'user_id': user_id, 'team': team_number }
        team['avg_mmr'] = get_avg_team_mmr(players.values())


    def leave(self, discord_id: int) -> str | None:
        if discord_id not in self.discord_player_map:
            return 'You are not present in any team.'
        p = self.discord_player_map[discord_id]
        del self.teams[p['team']]['players'][p['user_id']]
        del self.discord_player_map[discord_id]


    def is_full(self) -> bool:
        team_size = self.league_data['team_size']
        return len(self.teams[0]['players']) == team_size and len(self.teams[1]['players']) == team_size


    def is_empty(self) -> bool:
        return not len(self.teams[0]['players']) and not len(self.teams[1]['players'])


    def is_team_joinable(self, team: int) -> bool:
        return len(self.teams[team]['players']) < self.league_data['team_size']


    def lookup_player(self, discord_id: int) -> dict[str, Any] | None:
        if discord_id not in self.discord_player_map:
            return None
        return self.discord_player_map[discord_id]


    @abstractmethod
    def render_info(self):
        pass


    async def get_match_data(self) -> CreatedMatchInfo | None:
        if self.match_id is None:
            return None
        data = await self.api.get_match(self.match_id)
        return data['match']


    # def render_mvp(self, counter: int) -> str:
    #     if counter == 0:
    #         return ''
    #     num = "".join(["⁰¹²³⁴⁵⁶⁷⁸⁹"[ord(c)-ord('0')] for c in str(counter)])
    #     return f' ★{num}'


    def render_kda(self, player: dict) -> str:
        if 'stats' not in player:
            return ''
        stats = player['stats']
        if not stats:
            return ''
        return f" [{stats['kills']}/{stats['deaths']}/{stats['assists']}]"


    async def start(self) -> str:
        payload = MatchmakingMatchInfoRequest(
            **self.match_config,
            map=str(self.map['nid']),
            region=MatchmakingRegionEnum.to_value(self.region),
            teams=[
                MatchmakingTeamInfo(players=[MatchmakingPlayerInfo(account=str(player_id)) for player_id in team['players']])
                for team in self.teams
            ],
            callbackUrl=self.callback_url
        )
        data = await self.api.create_match(payload)
        match_id = data["match"]["matchId"]
        self.match_id = match_id
        self.started_at = datetime.utcnow()
        # TODO: Reset players KDA for the match?
        self.winner = None
        return match_id

    def cancel(self):
        self.db.insert_match(
            MatchData(
                match_id=self.match_id,
                league_id=self.league.value,
                status=MatchStatusEnum.from_value('cancelled'),
                mode_id=self.mode.value,
                config=self.match_config,
                teams=[
                    DBBlobTeamInfo(
                        players=[
                            DBBlobPlayerInfo(name=player['name'])
                            for player in team['players'].values()
                        ],
                        # placement=None
                    )
                    for team in self.teams[:2]
                ],
                map=self.map['nid'],
                region=self.region.value,
                comment=None,
            ),
            [user_id for team in self.teams[:2] for user_id in team['players']]
        )


    def finish(self, data: CreatedMatchInfo) -> str | None:
        status = data['status']

        if status != 'complete':
            return 'Match is not complete yet.'

        result = None
        teams = data['teams']

        draw = int(teams[0]['placement'] == teams[1]['placement'])
        winner = int(teams[0]['placement'] > teams[1]['placement'])
        self.winner = None if draw else winner
        team_match_info: list[DBBlobTeamInfo] = []
        changes: list[DBStatsChange] = []
        # TODO: It's potentially dangerous to iterate over teams received from ev.io
        # In case a player was not included in the stats for some reason, his stats won't be taken into account
        for i, team in enumerate(teams[:2]):
            enemy_team_avg_mmr = self.teams[int(not i)]['avg_mmr']
            lobby_players = self.teams[i]['players']
            placement = team['placement']
            players: list[DBBlobPlayerInfo] = []
            won = not placement
            sign = 1 if won else -1
            # mvp = max(teams[winner]['players'], key=lambda x: x['stats']['score'])
            # self.teams[winner]['players'][mvp['account']]['mvp_count'] += 1
            for player in team['players']:
                user_id = player['account']
                lobby_player = lobby_players[user_id]

                stats = player['stats']
                kda = {
                    'kills': stats['kills'],
                    'deaths': stats['deaths'],
                    'assists': stats['assists'],
                }
                lobby_player['stats'] = kda # Required by render_info

                change = DBStatsChange(
                    won=int(won),
                    lost=int(placement and not draw),
                    draw=draw,
                    user_id=user_id,
                    league_id=self.league.value,
                    mmr=0,
                    **kda
                )

                player_info = DBBlobPlayerInfo(
                    name=lobby_player['name'],
                    **kda
                )

                if self.mode is GameMode.Competitive and not draw:
                    diff = get_rating_diff(lobby_player['mmr'], enemy_team_avg_mmr)
                    bonus = get_mmr_bonus(diff, won)
                    mmr_change = (BASE_MMR_RATE + bonus) * sign
                    print(f'{lobby_player["name"]} / PLACEMENT: {placement} / MMR: {lobby_player["mmr"]} / ENEMY AVG MMR: {enemy_team_avg_mmr} / CHANGE: {mmr_change}')
                    # TODO: Need to avoid making player MMR below zero
                    lobby_player['mmr'] += mmr_change
                    change['mmr'] = mmr_change
                    player_info['mmr'] = lobby_player['mmr']

                players.append(player_info)
                changes.append(change)
            team_match_info.append(DBBlobTeamInfo(players=players, placement=placement))

        # Recalculate team average MMR
        if self.mode is GameMode.Competitive:
            for team in self.teams:
                team['avg_mmr'] = get_avg_team_mmr(team['players'].values())

        self.db.update_players_stats(changes)

        if not draw:
            winner = teams[0]['placement'] > teams[1]['placement'] # True (1) - Red, False (0) - Blue
            result = f'Team {TEAM_MAP[winner]} is the winner!'
            # self.teams[winner]['win_count'] += 1
        else:
            result = 'Draw!'

        self.db.insert_match(
            MatchData(
                match_id=self.match_id,
                league_id=self.league.value,
                status=MatchStatusEnum.from_value(status),
                mode_id=self.mode.value,
                config=self.match_config,
                teams=team_match_info,
                map=self.map['nid'],
                region=self.region.value,
                comment=None,
            ),
            [player['account'] for team in teams for player in team['players']]
        )

        return result


class MatchmakingLobby(AbstractLobby):

    def __init__(self, api: EvioApiClient, db: EvioDB, map: EvioMap, league: League, mode: GameMode, callback_url: str, creator: User) -> None:
        super().__init__(api, db, map, league, mode, callback_url, creator)


    # TODO: Probably needs to be handled by states
    def render_info(self, include_players: bool = False, is_searching: bool = False) -> Embed:
        super().render_info()
        if self.mode is GameMode.Competitive and include_players:
            teams_info = f'Team Red ({self.teams[0]["avg_mmr"]}) vs Team Blue ({self.teams[1]["avg_mmr"]})'
        else:
            teams_info = f'Team Red vs Team Blue'
        embed = Embed(title=f'{self.mode.name} ev.io {self.league.name.lower()} lobby', description=teams_info, color=Color.darker_grey())

        if include_players:
            if self.mode is GameMode.Competitive:
                embed.add_field(name=f'Team Red{" 🏆" * (self.winner == 0)}', value='\n'.join([f"{player['name']} ({player['mmr']}){self.render_kda(player)}" for player in self.teams[0]['players'].values()]), inline=True)
                embed.add_field(name=f'Team Blue{" 🏆" * (self.winner == 1)}', value='\n'.join([f"{player['name']} ({player['mmr']}){self.render_kda(player)}" for player in self.teams[1]['players'].values()]), inline=True)
            else:
                embed.add_field(name=f'Team Red{" 🏆" * (self.winner == 0)}', value='\n'.join([f"{player['name']}{self.render_kda(player)}" for player in self.teams[0]['players'].values()]), inline=True)
                embed.add_field(name=f'Team Blue{" 🏆" * (self.winner == 1)}', value='\n'.join([f"{player['name']}{self.render_kda(player)}" for player in self.teams[1]['players'].values()]), inline=True)
        embed.add_field(name='Map', value=self.map['title'], inline=False)
        embed.add_field(name='Region', value=MatchmakingRegionEnum.label(self.region), inline=False)
        embed.add_field(name='Configuration', value='\n'.join([f'{MATCH_INFO_MAP[key]}: {value}' for key, value in self.match_config.items()]), inline=False)
        if is_searching:
            embed.set_image(url=f"https://john-doe.xyz/static/globe.gif")
        else:
            embed.set_image(url=f"https://ev.io/{self.map['field_large_image']}")
        # embed.set_author(name=self.creator.name, icon_url=self.creator.avatar.url if self.creator.avatar else None)
        embed.set_footer(text=f'Match ID: {self.match_id}\nCreated at: {self.created_at.isoformat()}')
        return embed


class CustomLobby(AbstractLobby):

    def __init__(self, api: EvioApiClient, db: EvioDB, map: EvioMap, league: League, mode: GameMode, callback_url: str, creator: User) -> None:
        super().__init__(api, db, map, league, mode, callback_url, creator)


    def render_info(self, include_players: bool = False, is_searching: bool = False) -> Embed:
        teams_info = f'Team Red vs Team Blue'
        embed = Embed(title=f'{self.mode.name} ev.io {self.league.name.lower()} lobby', description=teams_info, color=Color.darker_grey())
        embed.add_field(name=f'Team Red{" 🏆" * (self.winner == 0)}', value='\n'.join([f"{player['name']}{self.render_kda(player)}" for player in self.teams[0]['players'].values()]), inline=True)
        embed.add_field(name=f'Team Blue{" 🏆" * (self.winner == 1)}', value='\n'.join([f"{player['name']}{self.render_kda(player)}" for player in self.teams[1]['players'].values()]), inline=True)
        embed.add_field(name='Spectators', value='\n'.join([player['name'] for player in self.teams[2]['players'].values()]), inline=True)
        embed.add_field(name='Map', value=self.map['title'], inline=False)
        embed.add_field(name='Region', value=MatchmakingRegionEnum.label(self.region), inline=False)
        embed.add_field(name='Configuration', value='\n'.join([f'{MATCH_INFO_MAP[key]}: {value}' for key, value in self.match_config.items()]), inline=False)
        embed.set_author(name=self.creator.name, icon_url=self.creator.avatar.url if self.creator.avatar else None)
        embed.set_image(url=f"https://ev.io/{self.map['field_large_image']}")
        embed.set_footer(text=f'Match ID: {self.match_id}\nCreated at: {self.created_at.isoformat()}')
        return embed
