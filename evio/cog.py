import websockets.client
import logging
from .mm.lobby import MATCH_INFO_MAP, MMR_DIFF_THRESHOLD, MAPS_POOL, CustomLobby, MatchmakingLobby, get_avg_team_mmr
from custom_types import MatchmakingBot
from uuid import uuid4
from datetime import datetime
from json import loads, dumps
from sqlite3 import IntegrityError
from discord import Client, app_commands, Interaction, ui, ButtonStyle, Client, Embed, Color, User, SelectOption, Message
from discord.emoji import Emoji
from discord.enums import ButtonStyle
from discord.ext import commands
from discord.interactions import Interaction
from discord.partial_emoji import PartialEmoji
from discord.ui import View
from typing import Any, Coroutine
from aiohttp import ClientSession, BasicAuth
from random import choice, shuffle
from traceback import format_exc
from table2ascii import table2ascii
from urllib.parse import quote

from .api import EvioMap, EvioApiClient, EvioUserInfo
from .db import EvioDB, League, GameMode, DBHistoricalMatch, MatchStatusEnum, MatchmakingRegionEnum


class MatchmakingLobbyScreen(View):

    def __init__(self, bot: MatchmakingBot, api: EvioApiClient, db: EvioDB, user: User, maps: list[EvioMap], league_id: int, mode_id: int, callback_url: str, player_settings: dict):
        super().__init__(timeout=None)

        self.bot = bot
        self.db = db
        self.api = api
        self.creator = user

        self.discord_message: Message = None

        self.league = League(league_id)
        self.mode = GameMode(mode_id)

        self.maps = maps
        player_maps = loads(player_settings['maps'])
        player_regions = loads(player_settings['regions'])
        self.map_pool = [map for map in maps if map['nid'] in player_maps]
        self.region = MatchmakingRegionEnum(player_regions[0])
        self.callback_url = callback_url

        # TODO: Kinda suboptimal... We take league data here, then once again when creating a new lobby
        self.league_data = self.db.get_league_data(league_id, 'match_config')
        self.match_config: dict = loads(self.league_data['match_config'])

        self.lobby_key = None
        self.created_at = datetime.utcnow()


    def render_info(self) -> Embed:
        embed = Embed(title=f'{self.mode.name} ev.io {self.league.name.lower()} matchmaking', color=Color.darker_grey())
        embed.add_field(name='Map pool', value=', '.join([map['title'] for map in self.map_pool]), inline=False)
        embed.add_field(name='Region', value=MatchmakingRegionEnum.label(self.region), inline=False)
        embed.add_field(name='Configuration', value='\n'.join([f'{MATCH_INFO_MAP[key]}: {value}' for key, value in self.match_config.items()]), inline=False)
        # IDEA: Maybe cache images and make a compilation of images for selected maps?
        embed.set_author(name=self.creator.name, icon_url=self.creator.avatar.url if self.creator.avatar else None)
        embed.set_image(url=f"https://john-doe.xyz/static/mm_maps.png")
        # embed.set_image(url=f"https://ev.io/{self.map['field_large_image']}")
        # embed.set_footer(text=f'Lobby ID: {self.lobby_key}\nCreated at: {self.created_at.isoformat()}')
        embed.set_footer(text=f'Created at: {self.created_at.isoformat()}')
        return embed


    async def trigger_lobby_leave(self, discord_id: int):
        lobby = self.bot.lobbies[self.lobby_key]
        lobby.leave(discord_id)
        if not lobby.is_empty():
            self.lobby_key = None
            del lobby.user_messages[discord_id]
            return
        async with self.bot.lobbies_lock:
            del self.bot.lobbies[self.lobby_key]
        self.lobby_key = None


    async def trigger_lobby_start(self):
        lobby = self.bot.lobbies[self.lobby_key]
        if not lobby.is_full():
            for msg in lobby.user_messages.values():
                try:
                    await msg.edit(embed=lobby.render_info(False, True))
                except:
                    logging.error(format_exc())
            return
        match_id = await lobby.start()
        async with self.bot.matches_lock:
            self.bot.matches[match_id] = lobby
        async with self.bot.lobbies_lock:
            del self.bot.lobbies[self.lobby_key]
        for msg in lobby.user_messages.values():
            # TODO: Readiness screen
            try:
                await msg.edit(content='Match was found!', embed=lobby.render_info(True, False), view=ConnectScreen(match_id))
            except:
                logging.error(format_exc())


    @ui.button(label="Select map pool", style=ButtonStyle.gray, row=0)
    async def select_map(self, interaction: Interaction, _: ui.Button):
        if interaction.user.id != self.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return
        await interaction.response.edit_message(view=MMMapSelectionScreen(self, [map['nid'] for map in self.map_pool]))


    @ui.button(label="Select region", style=ButtonStyle.gray, row=0)
    async def select_region(self, interaction: Interaction, _: ui.Button):
        if interaction.user.id != self.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return
        await interaction.response.edit_message(view=MMRegionSelectionScreen(self))


    @ui.button(label="Search", style=ButtonStyle.green, row=1)
    async def search(self, interaction: Interaction, _: ui.Button):
        def mmr_over_threshold(member_mmr: int, avg_mmr: int):
            diff = member_mmr - avg_mmr
            logging.info(f'MM: Rating diff: {diff}')
            return diff > MMR_DIFF_THRESHOLD or diff < -MMR_DIFF_THRESHOLD

        if interaction.user.id != self.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return
        if any(interaction.user.id in lobby.discord_player_map for lobby in self.bot.lobbies.values()) \
            or any(interaction.user.id in lobby.discord_player_map for lobby in self.bot.matches.values()):
            await interaction.response.send_message("You are already playing in another lobby.", ephemeral=True)
            return
        member = self.db.get_player_with_stats(interaction.user.id, self.league.value, 'p.user_id', 'p.name', 's.mmr')
        if not member:
            await interaction.response.send_message('You must register first.', ephemeral=True)
            return

        target_lobby = None
        member_mmr = member['mmr']
        # FIXME: This may be shit performance-wise, but not sure if there's a better way
        async with self.bot.lobbies_lock:
            shuffled_keys = list(self.bot.lobbies)
            shuffle(shuffled_keys)
            for key in shuffled_keys:
                lobby = self.bot.lobbies[key]
                # Avoid putting MM players into Custom lobbies
                if isinstance(lobby, CustomLobby) \
                    or not any(lobby.map['nid'] == map['nid'] for map in self.map_pool) \
                    or lobby.region is not self.region \
                    or lobby.league is not self.league \
                    or lobby.mode is not self.mode:
                    continue
                logging.info(f'MM: Checking lobby with teams: {lobby.teams}')
                for i, team in enumerate(lobby.teams[:2]):
                    if not lobby.is_team_joinable(i):
                        logging.info(f'MM: Team {i} is full. Players num: {len(team["players"])}.')
                        continue
                    if self.mode is GameMode.Competitive:
                        target_avg_mmr = team['avg_mmr']
                        if target_avg_mmr > 0 and mmr_over_threshold(member_mmr, target_avg_mmr):
                            logging.info(f'MM: Team {i} doesn\'t match MMR requirements. Player MMR: {member_mmr}, their MMR: {target_avg_mmr}')
                            continue
                        enemy_avg_mmr = lobby.teams[int(not i)]['avg_mmr']
                        if enemy_avg_mmr > 0 and mmr_over_threshold(member_mmr, enemy_avg_mmr):
                            logging.info(f'MM: Enemy team {int(not i)} has too high avg MMR. Player MMR: {member_mmr}, their MMR: {enemy_avg_mmr}')
                            continue
                    lobby.join(i, member, interaction.user.id)
                    target_lobby = lobby
                    self.lobby_key = key
                    break

        if target_lobby is None:
            logging.info('MM: No matching lobby found. Creating new lobby.')
            self.lobby_key = str(uuid4())
            target_lobby = MatchmakingLobby(self.api, self.db, choice(self.map_pool), self.league, self.mode, self.callback_url, self.creator)
            target_lobby.join(0, member, interaction.user.id)
            # FIXME: Need to refactor lobby class to accept region
            target_lobby.region = self.region
            async with self.bot.lobbies_lock:
                self.bot.lobbies[self.lobby_key] = target_lobby
        await interaction.response.edit_message(content='Waiting for players...', embed=target_lobby.render_info(False, True), view=MatchSearchScreen(self))

        target_lobby.user_messages[interaction.user.id] = self.discord_message

        await self.trigger_lobby_start()


    @ui.button(label="Cancel", style=ButtonStyle.red, row=1)
    async def cancel(self, interaction: Interaction, _: ui.Button):
        if interaction.user.id != self.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return
        await self.discord_message.delete()


class MatchSearchScreen(View):

    def __init__(self, parent: MatchmakingLobbyScreen):
        super().__init__(timeout=None)
        self.parent = parent


    @ui.button(label="Cancel", style=ButtonStyle.gray, row=0)
    async def cancel(self, interaction: Interaction, _: ui.Button):
        if interaction.user.id != self.parent.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return
        await self.parent.trigger_lobby_leave(interaction.user.id)
        await interaction.response.edit_message(content='Cancelled search.', embed=self.parent.render_info(), view=self.parent)


class MMRegionSelectionScreen(View):

    def __init__(self, parent: MatchmakingLobbyScreen):
        super().__init__(timeout=None)
        self.selector = MMRegionSelect('Select a region', [SelectOption(label=value, value=key) for key, value in MatchmakingRegionEnum.__LABELS__.items()])
        self.add_item(self.selector)
        self.parent = parent


    async def interaction_check(self, interaction: Interaction[Client]) -> Coroutine[Any, Any, bool]:
        if interaction.user.id != self.parent.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return False
        return await super().interaction_check(interaction)


    @ui.button(label="Back", style=ButtonStyle.gray)
    async def back(self, interaction: Interaction, _: ui.Button):
        await interaction.response.edit_message(view=self.parent)


class MMRegionSelect(ui.Select['MMRegionSelectionScreen']):

    def __init__(self, placeholder: str, options: list[SelectOption]):
        super().__init__(placeholder=placeholder, options=options)


    async def callback(self, interaction: Interaction):
        member = self.view.parent.db.get_player_by_discord_id(interaction.user.id, 'p.user_id')
        if not member:
            await interaction.response.send_message('You must register first.', ephemeral=True)
            return
        region = int(self.values[0])
        self.view.parent.db.set_player_settings(member['user_id'], regions=[region])
        self.view.parent.region = MatchmakingRegionEnum(region)
        await interaction.response.edit_message(embed=self.view.parent.render_info(), view=self.view.parent)


class MMMapSelectionScreen(View):

    def __init__(self, parent: MatchmakingLobbyScreen, defaults: list[int]):
        super().__init__(timeout=None)
        self.selector = MMMapSelect('Select maps', [SelectOption(label=item['title'], value=item['nid'], default=item['nid'] in defaults) for item in parent.maps])
        self.add_item(self.selector)
        self.parent = parent


    async def interaction_check(self, interaction: Interaction[Client]) -> Coroutine[Any, Any, bool]:
        if interaction.user.id != self.parent.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return False
        return await super().interaction_check(interaction)


    @ui.button(label="Back", style=ButtonStyle.gray)
    async def back(self, interaction: Interaction, _: ui.Button):
        await interaction.response.edit_message(view=self.parent)


class MMMapSelect(ui.Select['MMMapSelectionScreen']):

    def __init__(self, placeholder: str, options: list[SelectOption]):
        super().__init__(placeholder=placeholder, max_values=len(options), options=options)


    async def callback(self, interaction: Interaction):
        member = self.view.parent.db.get_player_by_discord_id(interaction.user.id, 'p.user_id')
        if not member:
            await interaction.response.send_message('You must register first.', ephemeral=True)
            return
        self.view.parent.db.set_player_settings(member['user_id'], maps=[map['nid'] for map in self.view.parent.map_pool])
        self.view.parent.map_pool = [map for map in self.view.parent.maps for value in self.values if map['nid'] == int(value)]
        await interaction.response.edit_message(embed=self.view.parent.render_info(), view=self.view.parent)

# ---------------------

class CustomLobbyScreen(View):

    def __init__(self, bot: MatchmakingBot, db: EvioDB, user: User, maps: list[EvioMap], lobby: CustomLobby, lobby_key: str):
        super().__init__(timeout=None)

        self.bot = bot
        self.creator = user
        self.db = db

        self.discord_message: Message = None

        self.lobby = lobby
        self.lobby_key = lobby_key

        self.maps = maps

        self.render_buttons()


    def render_buttons(self):
        buttons = (
            ConfigureLobby(label='Configure', row=0, disabled=True if self.creator.id != 277821614345945089 and self.lobby.mode is GameMode.Competitive else False),
            JoinTeamButton(label='Join Team Red', row=1, team=0),
            JoinTeamButton(label='Join Team Blue', row=1, team=1),
            JoinTeamButton(label='Join Spectators', row=1, team=2),
            LeaveTeamButton(label='Leave team', row=1)
        )
        for btn in buttons:
            self.add_item(btn)


    @ui.button(label="Select map", style=ButtonStyle.gray, row=0)
    async def select_map(self, interaction: Interaction, _: ui.Button):
        if interaction.user.id != self.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return
        await interaction.response.edit_message(view=CMapSelectionScreen(self))


    @ui.button(label="Select region", style=ButtonStyle.gray, row=0)
    async def select_region(self, interaction: Interaction, _: ui.Button):
        if interaction.user.id != self.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return
        await interaction.response.edit_message(view=CRegionSelectionScreen(self))


    @ui.button(label="Start", style=ButtonStyle.green, row=2)
    async def start(self, interaction: Interaction, _: ui.Button):
        if interaction.user.id != self.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return
        if len(self.lobby.teams[0]['players']) == 0 or len(self.lobby.teams[1]['players']) == 0:
            await interaction.response.send_message("Cannot start with empty teams.", ephemeral=True)
            return
        if self.lobby.league is not League.Custom and not self.lobby.is_full():
            await interaction.response.send_message("Teams must be full in order to start.", ephemeral=True)
            return
        match_id = await self.lobby.start()
        async with self.bot.lobbies_lock:
            del self.bot.lobbies[self.lobby_key]
        async with self.bot.matches_lock:
            self.bot.matches[match_id] = self.lobby
        await interaction.response.edit_message(embed=self.lobby.render_info(), view=ConnectScreen(match_id))


    @ui.button(label="Cancel", style=ButtonStyle.red, row=2)
    async def cancel(self, interaction: Interaction, _: ui.Button):
        if interaction.user.id != self.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return
        async with self.bot.lobbies_lock:
            del self.bot.lobbies[self.lobby_key]
        await self.discord_message.delete()


class ConfigureLobby(ui.Button['CustomLobbyScreen']):

    def __init__(self, *, style: ButtonStyle = ButtonStyle.secondary, label: str | None = None, disabled: bool = False, custom_id: str | None = None, url: str | None = None, emoji: str | Emoji | PartialEmoji | None = None, row: int | None = None):
        super().__init__(style=style, label=label, disabled=disabled, custom_id=custom_id, url=url, emoji=emoji, row=row)


    async def callback(self, interaction: Interaction) -> Any:
        if interaction.user.id != self.view.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return
        await interaction.response.send_modal(LobbyConfigModal(self.view))


class JoinTeamButton(ui.Button['CustomLobbyScreen']):

    def __init__(self, *, style: ButtonStyle = ButtonStyle.secondary, label: str | None = None, disabled: bool = False, custom_id: str | None = None, url: str | None = None, emoji: str | Emoji | PartialEmoji | None = None, row: int | None = None, team: int):
        super().__init__(style=style, label=label, disabled=disabled, custom_id=custom_id, url=url, emoji=emoji, row=row)
        self.team = team


    async def callback(self, interaction: Interaction) -> Coroutine[Any, Any, Any]:
        view = self.view
        member = view.db.get_player_with_stats(interaction.user.id, view.lobby.league.value, 'p.user_id', 'p.name', 's.mmr')
        if not member:
            await interaction.response.send_message('You must register first.', ephemeral=True)
            return
        # TODO: This is double lookup: here and in .join() method... Maybe not a problem since it's O(1)
        lobby_player = view.lobby.lookup_player(interaction.user.id)
        if lobby_player is None \
            and (any(interaction.user.id in lobby.discord_player_map for lobby in self.view.bot.lobbies.values()) \
            or any(interaction.user.id in lobby.discord_player_map for lobby in self.view.bot.matches.values())):
            await interaction.response.send_message("You are already playing in another lobby.", ephemeral=True)
            return
        err = view.lobby.join(self.team, member, interaction.user.id)
        if err is not None:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.edit_message(embed=view.lobby.render_info(), view=view)


class LeaveTeamButton(ui.Button['CustomLobbyScreen']):

    def __init__(self, *, style: ButtonStyle = ButtonStyle.secondary, label: str | None = None, disabled: bool = False, custom_id: str | None = None, url: str | None = None, emoji: str | Emoji | PartialEmoji | None = None, row: int | None = None):
        super().__init__(style=style, label=label, disabled=disabled, custom_id=custom_id, url=url, emoji=emoji, row=row)


    async def callback(self, interaction: Interaction) -> Any:
        view = self.view
        if view.creator.id == interaction.user.id:
            await interaction.response.send_message('You cannot leave teams in your own lobby. If you want to leave the lobby, use the **Cancel** button.', ephemeral=True)
            return
        member = view.db.get_player_by_discord_id(interaction.user.id, 'p.user_id')
        if not member:
            await interaction.response.send_message('You must register first.', ephemeral=True)
            return
        err = view.lobby.leave(interaction.user.id)
        if err is not None:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.edit_message(embed=view.lobby.render_info(), view=view)


class LobbyConfigModal(ui.Modal):

    def __init__(self, parent: CustomLobbyScreen):
        super().__init__(title='Lobby configuration')

        self.parent = parent
        self.duration = ui.TextInput(label=MATCH_INFO_MAP['duration'], required=True, default=str(self.parent.lobby.match_config['duration']))
        self.damageMultiplier = ui.TextInput(label=MATCH_INFO_MAP['damageMultiplier'], required=True, default=str(self.parent.lobby.match_config['damageMultiplier']))
        self.killsToWin = ui.TextInput(label=MATCH_INFO_MAP['killsToWin'], required=True, default=str(self.parent.lobby.match_config['killsToWin']))
        self.gravity = ui.TextInput(label=MATCH_INFO_MAP['gravity'], required=True, default=str(self.parent.lobby.match_config['gravity']))
        self.timeVelocity = ui.TextInput(label=MATCH_INFO_MAP['timeVelocity'], required=True, default=str(self.parent.lobby.match_config['timeVelocity']))

        self.add_item(self.duration)
        self.add_item(self.damageMultiplier)
        self.add_item(self.killsToWin)
        self.add_item(self.gravity)
        self.add_item(self.timeVelocity)


    async def on_submit(self, interaction: Interaction):
        self.parent.lobby.match_config.update(
            {
                'damageMultiplier': float(self.damageMultiplier.value),
                'duration': int(self.duration.value),
                'killsToWin': int(self.killsToWin.value),
                'gravity': float(self.gravity.value),
                'timeVelocity': float(self.timeVelocity.value)
            }
        )
        await interaction.response.edit_message(embed=self.parent.lobby.render_info(), view=self.parent)


class CRegionSelectionScreen(View):

    def __init__(self, parent: CustomLobbyScreen):
        super().__init__(timeout=None)
        self.selector = CRegionSelect('Select a region', [SelectOption(label=value, value=key) for key, value in MatchmakingRegionEnum.__LABELS__.items()])
        self.add_item(self.selector)
        self.parent = parent


    async def interaction_check(self, interaction: Interaction[Client]) -> Coroutine[Any, Any, bool]:
        if interaction.user.id != self.parent.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return False
        return await super().interaction_check(interaction)


    @ui.button(label="Back", style=ButtonStyle.gray)
    async def back(self, interaction: Interaction, _: ui.Button):
        await interaction.response.edit_message(view=self.parent)


class CRegionSelect(ui.Select['CRegionSelectionScreen']):

    def __init__(self, placeholder: str, options: list[SelectOption]):
        super().__init__(placeholder=placeholder, options=options)


    async def callback(self, interaction: Interaction):
        self.view.parent.lobby.region = MatchmakingRegionEnum(int(self.values[0]))
        await interaction.response.edit_message(embed=self.view.parent.lobby.render_info(), view=self.view.parent)


class CMapSelectionScreen(View):

    def __init__(self, parent: CustomLobbyScreen):
        super().__init__(timeout=None)
        self.selector = CMapSelect('Select a map', [SelectOption(label=item['title'], value=item['nid']) for item in parent.maps])
        self.add_item(self.selector)
        self.parent = parent


    async def interaction_check(self, interaction: Interaction[Client]) -> Coroutine[Any, Any, bool]:
        if interaction.user.id != self.parent.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return False
        return await super().interaction_check(interaction)


    @ui.button(label="Back", style=ButtonStyle.gray)
    async def back(self, interaction: Interaction, _: ui.Button):
        await interaction.response.edit_message(view=self.parent)


class CMapSelect(ui.Select['CMapSelectionScreen']):

    def __init__(self, placeholder: str, options: list[SelectOption]):
        super().__init__(placeholder=placeholder, options=options)


    async def callback(self, interaction: Interaction):
        self.view.parent.lobby.map = next((map for map in self.view.parent.maps if map['nid'] == int(self.values[0])), None)
        await interaction.response.edit_message(embed=self.view.parent.lobby.render_info(), view=self.view.parent)


class ConnectScreen(View):

    def __init__(self, match_id: str):
        super().__init__(timeout=None)
        self.add_item(ui.Button(label='Connect', url=f'https://ev.io/?match={match_id}'))

# ---------------------

class HistoryScreen(View):

    def __init__(self, db: EvioDB, creator: User, maps: list[EvioMap]):
        super().__init__(timeout=None)
        self.db = db
        self.creator = creator
        self.matches = self.db.get_player_match_history(creator.id)
        self.maps = maps
        self.pos = 0


    # async def interaction_check(self, interaction: Interaction[Client]) -> Coroutine[Any, Any, bool]:
    #     if interaction.user.id != self.creator.id:
    #         await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
    #         return False
    #     return await super().interaction_check(interaction)


    def render_kda(self, player: dict) -> str:
        # TODO: Need to fix to make consistent
        if 'kills' not in player or 'deaths' not in player or 'assists' not in player:
            return ''
        return f" [{player['kills']}/{player['deaths']}/{player['assists']}]"

    def render_info(self, match: DBHistoricalMatch) -> Embed:
        mode = GameMode(match['mode_id'])
        league = League(match['league_id'])
        teams: list[dict] = loads(match['teams'])
        config: dict = loads(match['config'])
        created_at = datetime.fromtimestamp(match['created_at'])

        region = MatchmakingRegionEnum(match['region'])
        map = next((map for map in self.maps if map['nid'] == match['map']), None)

        team_red_players = teams[0]['players']
        team_blue_players = teams[1]['players']

        status = MatchStatusEnum(match['status'])
        if status is MatchStatusEnum.COMPLETE:
            draw = int(teams[0]['placement'] == teams[1]['placement'])
            won = int(teams[0]['placement'] > teams[1]['placement'])
            winner = None if draw else won

            if mode is GameMode.Competitive:
                teams_info = f'Team Red ({get_avg_team_mmr(team_red_players)}) vs Team Blue ({get_avg_team_mmr(team_blue_players)})'
            else:
                teams_info = 'Team Red vs Team Blue'
            embed = Embed(title=f'{mode.name} ev.io {league.name.lower()} match', description=f'{teams_info}\nMatch is: {MatchStatusEnum.label(status)}', color=Color.darker_grey())
            if mode is GameMode.Competitive:
                embed.add_field(name=f'Team Red{" 🏆" * (winner == 0)}', value='\n'.join([f"{player['name']} ({player['mmr']}){self.render_kda(player)}" for player in team_red_players]), inline=True)
                embed.add_field(name=f'Team Blue{" 🏆" * (winner == 1)}', value='\n'.join([f"{player['name']} ({player['mmr']}){self.render_kda(player)}" for player in team_blue_players]), inline=True)
            else:
                embed.add_field(name=f'Team Red{" 🏆" * (winner == 0)}', value='\n'.join([f"{player['name']}{self.render_kda(player)}" for player in team_red_players]), inline=True)
                embed.add_field(name=f'Team Blue{" 🏆" * (winner == 1)}', value='\n'.join([f"{player['name']}{self.render_kda(player)}" for player in team_blue_players]), inline=True)
        else:
            embed = Embed(title=f'{mode.name} ev.io {league.name.lower()} match', description=f'Team Red vs Team Blue\nMatch is: {MatchStatusEnum.label(status)}', color=Color.darker_grey())
            embed.add_field(name=f'Team Red', value='\n'.join([f"{player['name']}{self.render_kda(player)}" for player in team_red_players]), inline=True)
            embed.add_field(name=f'Team Blue', value='\n'.join([f"{player['name']}{self.render_kda(player)}" for player in team_blue_players]), inline=True)

        embed.add_field(name='Region', value=MatchmakingRegionEnum.label(region), inline=False)
        embed.add_field(name='Map', value=map['title'], inline=False)
        embed.add_field(name='Configuration', value='\n'.join([f'{MATCH_INFO_MAP[key]}: {value}' for key, value in config.items()]), inline=False)
        embed.add_field(name='Match comment', value=match['comment'])
        # embed.set_author(name=self.creator.name, icon_url=self.creator.avatar.url)
        embed.set_image(url=f"https://ev.io/{map['field_large_image']}")
        embed.set_footer(text=f'Match ID: {match["match_id"]}\nRecorded at: {created_at.isoformat()}')
        return embed


    @ui.button(label="Previous", style=ButtonStyle.gray)
    async def previous(self, interaction: Interaction, _: ui.Button):
        pos = self.pos - 1
        if pos < 0:
            await interaction.response.edit_message(content='Cannot navigate past the first page.')
            return
        self.pos = pos
        await interaction.response.edit_message(content=None, embed=self.render_info(self.matches[pos]))


    @ui.button(label="Next", style=ButtonStyle.gray)
    async def next(self, interaction: Interaction, _: ui.Button):
        pos = self.pos + 1
        if pos >= len(self.matches):
            await interaction.response.edit_message(content='Cannot navigate past the last page.')
            return
        self.pos = pos
        await interaction.response.edit_message(content=None, embed=self.render_info(self.matches[pos]))

# ---------------

class LeaderboardScreen(View):

    def __init__(self, db: EvioDB, creator: User, league: League):
        super().__init__(timeout=None)
        self.db = db
        self.creator = creator
        self.league = league
        self.pos = 0


    async def interaction_check(self, interaction: Interaction[Client]) -> Coroutine[Any, Any, bool]:
        if interaction.user.id != self.creator.id:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return False
        return await super().interaction_check(interaction)


    def render_kda(self, player: dict) -> str:
        # TODO: Need to fix to make consistent
        if 'kills' not in player or 'deaths' not in player or 'assists' not in player:
            return ''
        return f" [{player['kills']}/{player['deaths']}/{player['assists']}]"

    def render_info(self, data: list[dict]) -> Embed:
        table = table2ascii(
            header=('#', 'Name', 'MMR', 'K', 'D', 'A'),
            body=[(player['pos'], player['name'], player['mmr'], player['kills'], player['deaths'], player['assists']) for player in data]
        )
        return Embed(title=f'Leaderboard for {self.league.name} league', description=f'```{table}```', color=Color.darker_grey())


    @ui.button(label="Previous", style=ButtonStyle.gray)
    async def previous(self, interaction: Interaction, _: ui.Button):
        pos = self.pos - 1
        if pos < 0:
            await interaction.response.edit_message(content='Cannot navigate past the first page.')
            return
        self.pos = pos
        data = self.db.get_top_10_players(self.league.value, pos, 'p.name', 's.mmr', 's.kills', 's.deaths', 's.assists', 's.won', 's.draw', 's.lost')
        await interaction.response.edit_message(content=None, embed=self.render_info(data))


    @ui.button(label="Next", style=ButtonStyle.gray)
    async def next(self, interaction: Interaction, _: ui.Button):
        pos = self.pos + 1
        data = self.db.get_top_10_players(self.league.value, pos, 'p.name', 's.mmr', 's.kills', 's.deaths', 's.assists', 's.won', 's.draw', 's.lost')
        if not data:
            await interaction.response.edit_message(content='Cannot navigate past the last page.')
            return
        self.pos = pos
        await interaction.response.edit_message(content=None, embed=self.render_info(data))


    @ui.button(label="Close", style=ButtonStyle.red)
    async def close(self, interaction: Interaction, _: ui.Button):
        await interaction.response.defer()
        await interaction.delete_original_response()


# -------------

class VerifyView(View):

    def __init__(self, db: EvioDB, player: EvioUserInfo, is_created: bool):
        super().__init__(timeout=None)
        self.db = db
        self.player = player
        self.is_created = is_created


    @ui.button(label="Verify", style=ButtonStyle.gray)
    async def verify(self, interaction: Interaction, _: ui.Button):
        await interaction.response.send_modal(VerifyModal(self.db, self.player, self.is_created))


class VerifyModal(ui.Modal):

    def __init__(self, db: EvioDB, player: EvioUserInfo, is_created: bool):
        super().__init__(title='Verification')

        self.player = player
        self.db = db
        self.is_created = is_created
        self.party_code = ui.TextInput(label='Create a party and enter your party code', required=True)
        self.add_item(self.party_code)


    async def on_submit(self, interaction: Interaction):
        data = {
            "namespace": "prod2",
            "clientMetadata": "",
            "joinKind": {
                "type": "Existing",
                "partyQuery": {
                    "type": "Alias",
                    "alias": self.party_code.value
                },
                "createPlayerToken": False
            }
        }
        url = f'wss://matchmaker2.ev.io/party/ws?req={quote(dumps(data))}'
        async with websockets.client.connect(url) as ws:
            msg: dict = loads(await ws.recv())
            msg_type = msg['type']
            if msg_type == 'Error':
                await interaction.response.edit_message(content=f'Failed to join party. Reason: {msg["message"]}', view=None)
            elif msg_type == 'Init':
                leader = next((True for member in msg['state']['members'] if member['isLeader'] and self.player['name'][0]['value'] == loads(member['serverMetadata'])['username']), None)
                if leader is None:
                    await interaction.response.edit_message(content="Leader of the lobby does not match the specified username.", view=None)
                    return

                if self.is_created:
                    try:
                        self.db.update_player_registration(self.player['uid'][0]['value'], interaction.user.id)
                        await interaction.response.edit_message(content="You've been registered successfully.", view=None)
                    except:
                        logging.error(format_exc())
                        await interaction.response.edit_message(content="Something went wrong when trying to register.", view=None)
                    return

                try:
                    self.db.register_player(self.player, interaction.user.id)
                    await interaction.response.edit_message(content="You've been registered successfully.", view=None)
                except:
                    logging.error(format_exc())
                    await interaction.response.edit_message(content="Something went wrong when trying to register.", view=None)
            else:
                logging.error(f'Unexpected message received from ev.io. Raw message dump: {msg}')
                await interaction.response.edit_message(content='Unexpected message received from ev.io.', view=None)


class Evio(commands.Cog):

    def __init__(self, bot: MatchmakingBot, client: ClientSession, credentials: BasicAuth, callback_url: str):
        self.bot = bot
        self.api = EvioApiClient(client, credentials)
        self.db = EvioDB(self.bot.db)
        self.callback_url = callback_url
        self.bot.loop.create_task(self.load_maps())


    async def load_maps(self):
        self.maps = tuple(map for map in await self.api.get_maps() if map['nid'] in MAPS_POOL)


    @app_commands.command(name='history')
    async def history(self, interaction: Interaction):
        """View your match history"""

        player = self.db.get_player_by_discord_id(interaction.user.id, 'COUNT(1) as count')
        if not player['count']:
            await interaction.response.send_message('You must register first.', ephemeral=True)
            return
        view = HistoryScreen(self.db, interaction.user, self.maps)
        if not view.matches:
            await interaction.response.send_message('You have no played matches yet.', ephemeral=True)
            return
        await interaction.response.send_message(embed=view.render_info(view.matches[0]), view=view, ephemeral=True)


    @app_commands.command(name='leaderboard')
    @app_commands.choices(league=[app_commands.Choice(name=e.name, value=e.value) for e in League if e is not League.Custom])
    async def leaderboard(self, interaction: Interaction, league: app_commands.Choice[int]):
        """View leaderboard"""

        league = League(league.value)
        view = LeaderboardScreen(self.db, interaction.user, league)
        data = self.db.get_top_10_players(league.value, 0, 'p.name', 's.mmr', 's.kills', 's.deaths', 's.assists', 's.won', 's.draw', 's.lost')
        await interaction.response.send_message(view=view, embed=view.render_info(data))


    @app_commands.command(name='stats')
    @app_commands.choices(league=[app_commands.Choice(name=e.name, value=e.value) for e in League])
    async def stats(self, interaction: Interaction, league: app_commands.Choice[int]):
        """View your statistics"""

        player = self.db.get_player_with_stats(interaction.user.id, league.value, 'p.name', 's.mmr', 's.kills', 's.deaths', 's.assists', 's.won', 's.draw', 's.lost')
        if not player:
            await interaction.response.send_message('You must register first.', ephemeral=True)
            return

        avatar = interaction.user.avatar.url if interaction.user.avatar else None
        embed = Embed(color=Color.brand_green())
        embed.set_author(name=player['name'], icon_url=avatar)
        embed.add_field(name='Rating', value=player['mmr'], inline=False)
        embed.add_field(name='Won', value=player['won'], inline=True)
        embed.add_field(name='Draw', value=player['draw'], inline=True)
        embed.add_field(name='Lost', value=player['lost'], inline=True)
        embed.add_field(name='Kills', value=player['kills'], inline=True)
        embed.add_field(name='Deaths', value=player['deaths'], inline=True)
        embed.add_field(name='Assists', value=player['assists'], inline=True)
        embed.add_field(name='K/D', value=f'{player["kills"] / (player["deaths"] + int(player["deaths"] == 0))}', inline=True)
        await interaction.response.send_message(embed=embed)


    # TODO: Add rules
    @app_commands.command(name='rules')
    async def rules(self, interaction: Interaction):
        """View matchmaking rules"""

        await interaction.response.send_message('Not implemented yet', ephemeral=True)


    @app_commands.command(name='find_match')
    @app_commands.choices(
        league=[app_commands.Choice(name=e.name, value=e.value) for e in League if e is not League.Custom],
        mode=[app_commands.Choice(name=e.name, value=e.value) for e in GameMode]
    )
    async def find_match(self, interaction: Interaction, league: app_commands.Choice[int], mode: app_commands.Choice[int]):
        """Enqueue and search for the match"""

        if any(interaction.user.id in lobby.discord_player_map for lobby in self.bot.lobbies.values()) \
            or any(interaction.user.id in lobby.discord_player_map for lobby in self.bot.matches.values()):
            await interaction.response.send_message("You are already playing in another lobby.", ephemeral=True)
            return
        player = self.db.get_player_by_discord_id(interaction.user.id, 'p.user_id')
        if not player:
            await interaction.response.send_message('You must register first.', ephemeral=True)
            return

        await interaction.response.send_message('See the message below', ephemeral=True, silent=True, delete_after=0)

        player_settings = self.db.get_player_settings(player['user_id'], 'regions', 'maps')
        view = MatchmakingLobbyScreen(self.bot, self.api, self.db, interaction.user, self.maps, league.value, mode.value, self.callback_url, player_settings)
        view.discord_message = await interaction.channel.send(embed=view.render_info(), view=view)


    # TODO: Maybe need to restrict custom lobbies to Casual-only. Keeping Competitive for testing only.
    @app_commands.command(name='create_lobby')
    @app_commands.choices(
        league=[app_commands.Choice(name=e.name, value=e.value) for e in League],
        # mode=[app_commands.Choice(name=e.name, value=e.value) for e in GameMode]
    )
    @app_commands.guild_only()
    async def create_lobby(self, interaction: Interaction, league: app_commands.Choice[int]):
        """Create a local lobby"""

        player = self.db.get_player_with_stats(interaction.user.id, league.value, 'p.user_id', 'p.name', 's.mmr')
        if player is None:
            await interaction.response.send_message('You must register first.', ephemeral=True)
            return

        await interaction.response.send_message('See the message below', ephemeral=True, silent=True, delete_after=0)

        # TODO: Refactor
        lobby = CustomLobby(self.api, self.db, self.maps[0], League(league.value), GameMode.Casual, self.callback_url, interaction.user)
        lobby.join(0, player, interaction.user.id)
        lobby_key = str(uuid4())
        async with self.bot.lobbies_lock:
            self.bot.lobbies[lobby_key] = lobby

        view = CustomLobbyScreen(self.bot, self.db, interaction.user, self.maps, lobby, lobby_key)
        view.discord_message = await interaction.channel.send(embed=view.lobby.render_info(), view=view)

        lobby.user_messages[interaction.user.id] = view.discord_message


    @app_commands.command(name='register')
    async def evio_register(self, interaction: Interaction, *, evio_username: str):
        """Register using your ev.io username"""

        registered_player = self.db.get_player_by_discord_id(interaction.user.id, 'COUNT(1) as count')
        if registered_player['count']:
            await interaction.response.send_message("You're already registered.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        player = await self.api.get_user_info_by_name(evio_username)
        if not player:
            await interaction.followup.send("Couldn't find ev.io user with such username.", ephemeral=True)
            return

        uid = player['uid'][0]['value']
        db_player = self.db.get_player(uid, 'i.discord_id')
        is_created = False
        if db_player is not None:
            discord_id = db_player['discord_id']
            if discord_id is not None and discord_id != interaction.user.id:
                await interaction.followup.send("The player with this username is already registered.", ephemeral=True)
                return
            is_created = True

        await interaction.followup.send(content='Click "Verify" to continue.', view=VerifyView(self.db, player, is_created))


    @app_commands.command(name='unregister')
    async def evio_unregister(self, interaction: Interaction):
        """Unregister in case you specified wrong ev.io username"""
        player = self.db.get_player_by_discord_id(interaction.user.id, 'COUNT(1) as count')
        if not player['count']:
            await interaction.response.send_message('You are not registered.', ephemeral=True)
            return

        await self.leave_lobby(interaction.user.id)
        self.db.remove_player(interaction.user.id)
        await interaction.response.send_message("You've been unregistered successfully.", ephemeral=True)


    @app_commands.command(name='leave')
    async def evio_leave(self, interaction: Interaction):
        """Removes from any lobby you currently participate"""
        player = self.db.get_player_by_discord_id(interaction.user.id, 'COUNT(1) as count')
        if not player['count']:
            await interaction.response.send_message('You are not registered.', ephemeral=True)
            return

        msg = await self.leave_lobby(interaction.user.id)
        await interaction.response.send_message(msg, ephemeral=True)


    async def leave_lobby(self, discord_id: int) -> str:
        kv = next((lobby for lobby in self.bot.lobbies.items() if discord_id in lobby[1].discord_player_map), None)
        if kv is None:
            return 'You are not present in any lobby.'
        lobby_key, lobby = kv
        match lobby:
            case CustomLobby():
                # If lobby creator is interaction user - delete the lobby. Leave otherwise.
                if lobby.creator.id == discord_id:
                    async with self.bot.lobbies_lock:
                        del self.bot.lobbies[lobby_key]
                    try:
                        await lobby.user_messages[discord_id].delete()
                    except:
                        logging.error(format_exc())
                else:
                    lobby.leave(discord_id)
                    # Don't forget to update the lobby message
                    for msg in lobby.user_messages.values():
                        try:
                            await msg.edit(embed=lobby.render_info())
                        except:
                            logging.error(format_exc())
            case MatchmakingLobby():
                # Leave lobby unconditionally
                lobby.leave(discord_id)
                # Matchmaking lobby messages don't need to be updated since they include anonymized information
                # Since matchmaking lobbies don't have a creator, keep them until there are no players
                if len(lobby.discord_player_map) == 0:
                    async with self.bot.lobbies_lock:
                        del self.bot.lobbies[lobby_key]
                try:
                    await lobby.user_messages[discord_id].delete()
                except:
                    logging.error(format_exc())
        return "You've been removed from the lobby."
