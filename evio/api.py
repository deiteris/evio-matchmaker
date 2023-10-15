import asyncio
import re
from asyncio import Task
from typing import TypedDict, Any, Literal, Optional
from aiohttp import ClientSession, BasicAuth

class EvioAttribute(TypedDict):
    value: Any


class EvioDeployedMember(TypedDict):
    target_id: str
    target_type: Literal['user']
    target_uuid: str
    url: str


class EvioDeployedMemberExtended(EvioDeployedMember):
    earned_cp: int
    manual_deploy: int
    deployed_at: int
    kills: int
    discord_id: Optional[int]


class EvioUserInfo(TypedDict):
    uid: list[EvioAttribute]
    uuid: list[EvioAttribute]
    langcode: list[EvioAttribute]
    name: list[EvioAttribute]
    created: list[EvioAttribute]
    changed: list[EvioAttribute]
    default_langcode: list[EvioAttribute]
    field_abilities_loadout: list[EvioAttribute]
    field_auto_rifle_skin: list
    field_battle_royale_wins: list[dict]
    field_battle_royale_wins_weekly: list[dict]
    field_best_survival_time: list[EvioAttribute]
    field_burst_rifle_skin: list
    field_cp_earned_weekly: list[EvioAttribute]
    field_custom_crosshair: list
    field_daily_challenge: list[EvioAttribute]
    field_deaths: list[EvioAttribute]
    field_earn_base: list
    field_earned_as_scholar: list[EvioAttribute]
    field_earned_from_scholars: list[EvioAttribute]
    field_earned_scholar_this_week: list[EvioAttribute]
    field_eq_skin: list
    field_erealm: list
    field_ev_coins: list[EvioAttribute]
    field_ev_coins_this_week: list[EvioAttribute]
    field_fractal_uid: list[EvioAttribute]
    field_from_scholars_this_week: list[dict]
    field_hand_cannon_skin: list
    field_k_d: list[EvioAttribute]
    field_kills: list[EvioAttribute]
    field_laser_rifle_skin: list
    field_level: list[EvioAttribute]
    field_lifetime_cp_earned: list[EvioAttribute]
    field_match_history: list[EvioAttribute]
    field_primary_weapon: list[EvioAttribute]
    field_privacy_policy: list[EvioAttribute]
    field_rank: list[EvioAttribute]
    field_score: list[EvioAttribute]
    field_survival_high_scores: list[EvioAttribute]
    field_survival_weekly: list[EvioAttribute]
    field_sweeper_skin: list
    field_sword_skin: list
    field_terms_of_use: list[EvioAttribute]
    field_total_games: list[EvioAttribute]
    field_twitch: list
    field_ui_mod: list
    field_wallet_address: list[EvioAttribute]
    field_weekly_quest_percent_compl: list[dict]
    field_weekly_score: list[EvioAttribute]
    field_youtube: list


class EvioUserInfoExtended(EvioUserInfo):
    kills_delta: int
    score_delta: int
    discord_id: Optional[int]

class EvioClanInfo(TypedDict):
    id: list[EvioAttribute]
    uuid: list[EvioAttribute]
    langcode: list[EvioAttribute]
    type: list
    uid: list
    label: list[EvioAttribute]
    created: list[EvioAttribute]
    changed: list[EvioAttribute]
    path: list
    default_langcode: list[EvioAttribute]
    field_banner: list
    field_clan_points: list[EvioAttribute]
    field_deployed: list[EvioDeployedMember]
    field_discord_link: list
    field_insignia: list
    field_motto: list[EvioAttribute]
    field_points_this_week: list[EvioAttribute]


class EvioFlagsInfo(TypedDict):
    flag_id: str
    entity_id: str
    id: str
    field_flag_nft_address: str
    field_power_level: str
    field_meta: list[str]
    field_scholar: str
    field_scholar_earn_percentage: str
    field_skin: str
    uid: str
    field_earned_today: str
    field_reset_time: str
    field_wallet_image: str
    field_last_lent: str


class EvioScholarInfo(TypedDict):
    field_large_thumb: str
    title: str
    entity_id: str
    field_tier: str
    field_weapon_skin_thumb: str
    field_parent_weapon: str
    field_power_level: str
    field_meta: list[str]
    field_wallet_image: str
    field_collection: str
    field_flag_nft_address: str
    field_scholar: str
    field_scholar_earn_percentage: str
    field_id: str
    field_owner_id: str
    nid: str
    field_owner_name: str
    field_wallet_image_1: str
    uid: str
    field_earned_today: str
    field_reset_time: str

StringBool = Literal['0'] | Literal['1']

class EvioMap(TypedDict):
    title: str
    field_map_thumbnail: str
    field_large_image: str
    author: str
    field_modes: str # Comma-delimited game modes
    field_allow_private_games: StringBool
    field_in_public_rotation: StringBool
    field_is_community_map: StringBool
    changed: str
    revision_id: str
    field_map: str
    nid: int

MatchmakingDatacenter = Literal['san-francisco'] | Literal['new-jersey'] | Literal['singapore'] | Literal['amsterdam']

class MatchmakingPlayerStats(TypedDict):
    char: str
    kills: int
    deaths: int
    score: int
    round_wins: int
    guest_kills: int
    bot_kills: int
    registered_kills: int
    clan_kills: int
    bot_deaths: int
    boss_kills: int
    revives: int
    flags: int
    assists: int


class MatchmakingPlayerInfo(TypedDict):
    account: str | int # NOTE: int is used internally, but API works with str
    stats: Optional[MatchmakingPlayerStats]


class MatchmakingTeamInfo(TypedDict):
    players: list[MatchmakingPlayerInfo]
    placement: Optional[int]

GameMode = Literal['team_deathmatch']

class MatchmakingMatchInfoRequest(TypedDict):
    callbackUrl: str
    teams: list[MatchmakingTeamInfo]
    duration: int
    gravity: float
    timeVelocity: int
    damageMultiplier: float
    killsToWin: int
    gameMode: GameMode
    map: str
    region: MatchmakingDatacenter


MatchStatus = Literal['pending'] | Literal['running'] | Literal['complete'] | Literal['cancelled']

class CreatedMatchInfo(TypedDict):
    matchId: str
    status: MatchStatus
    teams: list[MatchmakingTeamInfo]
    duration: int
    gravity: float
    timeVelocity: int
    damageMultiplier: float
    killsToWin: int
    gameMode: GameMode
    map: str
    region: MatchmakingDatacenter


class MatchmakingMatchInfoResponse(TypedDict):
    match: CreatedMatchInfo

GameMode = Literal['Deathmatch'] | Literal['Instagib'] | Literal['Search and Destroy'] | Literal['Snipe the Streamer'] | Literal['Sniper Shotgun'] | Literal['Team Deathmatch']

RE_UID = r'href="/user/(\d+)"'
RE_PAGE_NUM = r'href="\?page=(\d+)"'


class EvioApiClient:

    def __init__(self, client: ClientSession, credentials: BasicAuth) -> None:
        self.client = client
        self.credentials = credentials
        self.api_base_url = 'https://ev.io'
        self.matchmaking_base_url = 'https://evio-match-api.herokuapp.com'


    async def create_match(self, match_info: MatchmakingMatchInfoRequest) -> MatchmakingMatchInfoResponse:
        res = await self.client.post(f'{self.matchmaking_base_url}/v1/matches', json=match_info, headers={'Content-Type': 'application/json'})
        return await res.json()


    async def get_match(self, match_id: str) -> MatchmakingMatchInfoResponse:
        res = await self.client.get(f'{self.matchmaking_base_url}/v1/matches/{match_id}', headers={'Content-Type': 'application/json'})
        data: MatchmakingMatchInfoResponse = await res.json()
        # NOTE: API returns str ID while we want int ID. Convert now to avoid conversions later.
        match = data['match']
        match['map'] = int(match['map'])
        for team in match['teams']:
            for player in team['players']:
                player['account'] = int(player['account'])
        return data


    async def get_maps(self) -> list[EvioMap]:
        res = await self.client.get(f'{self.api_base_url}/maps', headers={'Content-Type': 'application/json'})
        data: list[EvioMap] = await res.json()
        # NOTE: API returns str ID while we want int ID. Convert now to avoid conversions later.
        for item in data:
            item['nid'] = int(item['nid'])
        return data


    async def get_scholar_info(self, evio_user_id: int) -> list[EvioScholarInfo]:
        res = await self.client.get(f'{self.api_base_url}/scholar/{evio_user_id}', headers={'Content-Type': 'application/json'})
        return await res.json()


    async def get_flags_info(self, evio_user_id: int) -> list[EvioFlagsInfo]:
        res = await self.client.get(f'{self.api_base_url}/flags/{evio_user_id}', headers={'Content-Type': 'application/json'})
        return await res.json()


    async def get_user_info(self, evio_user_id: int) -> EvioUserInfo:
        res = await self.client.get(f'{self.api_base_url}/user/{evio_user_id}?_format=json', headers={'Content-Type': 'application/json'})
        return await res.json()


    async def get_user_info_by_name(self, username: str) -> EvioUserInfo | None:
        res = await self.client.get(f'{self.api_base_url}/rankings?uid={username}', headers={'Content-Type': 'application/json'})
        content = await res.text()
        uid = re.search(RE_UID, content)
        if not uid:
            return None
        return await self.get_user_info(uid[1])


    async def get_clan_info(self, evio_clan_id: int) -> EvioClanInfo:
        res = await self.client.get(f'{self.api_base_url}/group/{evio_clan_id}?_format=json', headers={'Content-Type': 'application/json'})
        return await res.json()


    async def patch_clan_info(self, evio_clan_id: int, data: EvioClanInfo):
        res = await self.client.patch(f'{self.api_base_url}/group/{evio_clan_id}?_format=json', json=data, auth=self.credentials, headers={'Content-Type': 'application/json'})
        return await res.json()


    async def get_clan_member_ids_page(self, evio_clan_id: int, page: int) -> list[int]:
        res = await self.client.get(f'{self.api_base_url}/group/{evio_clan_id}/members?page={page}', headers={'Content-Type': 'text/html'})
        content = await res.text()
        return [int(member_id) for member_id in re.findall(RE_UID, content)]


    async def get_clan_member_ids(self, evio_clan_id: int) -> list[int]:
        res = await self.client.get(f'{self.api_base_url}/group/{evio_clan_id}/members', headers={'Content-Type': 'text/html'})
        content = await res.text()

        pages = [int(num) for num in re.findall(RE_PAGE_NUM, content)]
        tasks: list[Task] = []
        for i in range(max(pages) + 1):
            tasks.append(asyncio.ensure_future(self.get_clan_member_ids_page(evio_clan_id, i)))
        completed_tasks: list[list[int]] = await asyncio.gather(*tasks, return_exceptions=True)

        members: list[int] = []
        for result in completed_tasks:
            members += result

        return members
