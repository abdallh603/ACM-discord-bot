from __future__ import annotations

import asyncio
import logging

import discord

from .blitz_options import RATINGS, TOPICS, VOICE_LABELS, duration_label, page_items
from .domain import UserError
from .presentation import embed, safe

log = logging.getLogger(__name__)
TOPIC_CHOICES = [("any", "Any topic"), ("random", "Random topic")] + [(t, t) for t in TOPICS]
RATING_CHOICES = [("auto", "Automatic (near the lowest-rated player)")] + [(str(r), str(r)) for r in RATINGS]


class MenuSelect(discord.ui.Select):
    def __init__(self, menu, field, choices, page=0, row=0, disabled=False):
        values, _, pages = page_items(choices, page)
        selected = getattr(menu, field)
        super().__init__(
            placeholder=f"{field.title()} · page {page + 1}/{pages}", row=row, disabled=disabled,
            options=[discord.SelectOption(label=label, value=value, default=selected == value)
                     for value, label in values],
        )
        self.menu = menu
        self.field = field

    async def callback(self, interaction):
        await self.menu.choose(interaction, self.field, self.values[0])


class MenuButton(discord.ui.Button):
    def __init__(self, menu, action, label, row, style=discord.ButtonStyle.secondary, disabled=False):
        super().__init__(label=label, row=row, style=style, disabled=disabled)
        self.menu = menu
        self.action_name = action

    async def callback(self, interaction):
        await self.menu.action(interaction, self.action_name)


class BlitzMenu(discord.ui.View):
    def __init__(self, bot, owner_id, guild_id, opponents, options):
        super().__init__(timeout=600)
        self.bot = bot
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.opponents = opponents
        self.options = options
        self.topic = "any"
        self.rating = "auto"
        self.voice = "mute"
        self.topic_page = 0
        self.rating_page = 0
        self.started = False
        self.lock = asyncio.Lock()
        self.message = None
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        manual = bool(self.options.get("manual"))
        self.add_item(MenuSelect(self, "topic", TOPIC_CHOICES, self.topic_page, 0, manual))
        self.add_item(MenuSelect(self, "rating", RATING_CHOICES, self.rating_page, 1, manual))
        self.add_item(MenuSelect(self, "voice", list(VOICE_LABELS.items()), row=2))
        self.add_item(MenuButton(self, "topics", "More topics", 3, disabled=manual))
        self.add_item(MenuButton(self, "ratings", "More ratings", 3, disabled=manual))
        self.add_item(MenuButton(self, "start", "Start solo test" if not self.opponents else "Create lobby", 4,
                                 discord.ButtonStyle.success))
        self.add_item(MenuButton(self, "cancel", "Cancel", 4, discord.ButtonStyle.danger))

    def summary(self):
        topic = dict(TOPIC_CHOICES)[self.topic]
        rating = dict(RATING_CHOICES)[self.rating]
        manual = self.options.get("manual")
        filters = f"Manual problem: **{safe(manual)}**" if manual else f"Topic: **{topic}**\nRating: **{rating}**"
        players = self.opponents if self.options.get("hosted") else [self.owner_id, *self.opponents]
        roster = " ".join(f"<@{user}>" for user in players)
        return embed(
            "Set up RoundForge Blitz",
            f"{filters}\nVoice: **{VOICE_LABELS[self.voice]}**\n"
            f"Time: **{duration_label(self.options.get('minutes'))}**\n"
            f"Rule: **{self.options.get('end_rule', 'first_solve')}**\nPlayers: {roster}\n\n"
            f"Host: <@{self.owner_id}> · Manager: <@{self.options.get('manager_id') or self.owner_id}>\n"
            "Choose from the menus below. More topics/ratings opens the next page. "
            "Creating a multiplayer lobby asks every invited player to accept before starting. "
            "No time limit is added when minutes is omitted. Menu expires after 10 minutes.",
        )

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Only the person who opened this menu can change it.", ephemeral=True)
            return False
        if self.started:
            await interaction.response.send_message("This menu has already been submitted or closed.", ephemeral=True)
            return False
        return True

    async def choose(self, interaction, field, value):
        allowed = {"topic": dict(TOPIC_CHOICES), "rating": dict(RATING_CHOICES), "voice": VOICE_LABELS}
        if field not in allowed or value not in allowed[field]:
            raise UserError("Invalid menu choice. Open /blitz menu again.")
        if self.lock.locked():
            await interaction.response.send_message("Another menu action is in progress.", ephemeral=True)
            return
        async with self.lock:
            setattr(self, field, value)
            self.rebuild()
            await interaction.response.edit_message(embed=self.summary(), view=self)

    def match_options(self):
        options = {**self.options, "voice_mode": self.voice}
        if not options.get("manual"):
            options.update(tag=None if self.topic in {"any", "random"} else self.topic,
                           random_topic=self.topic == "random")
            rating = None if self.rating == "auto" else int(self.rating)
            options.update(rating_min=rating, rating_max=rating)
        return options

    async def action(self, interaction, action):
        if self.lock.locked():
            await interaction.response.send_message("Another menu action is in progress.", ephemeral=True)
            return
        async with self.lock:
            if action in {"topics", "ratings"}:
                attr, choices = ("topic_page", TOPIC_CHOICES) if action == "topics" else ("rating_page", RATING_CHOICES)
                _, page, _ = page_items(choices, getattr(self, attr) + 1)
                setattr(self, attr, page)
                self.rebuild()
                await interaction.response.edit_message(embed=self.summary(), view=self)
                return
            if action == "cancel":
                self.started = True
                self.stop()
                await interaction.response.edit_message(content="Blitz setup cancelled.", embed=None, view=None)
                return
            if action != "start" or self.started:
                raise UserError("This menu is no longer available.")
            if not self.opponents and not interaction.permissions.manage_guild:
                raise UserError("Solo tests need Manage Server permission.")
            self.started = True
            await interaction.response.defer()
            try:
                options = self.match_options()
                if self.opponents:
                    create = self.bot.blitz.host if options.get("hosted") else self.bot.blitz.challenge
                    match = await create(self.guild_id, self.owner_id, self.opponents, options)
                else:
                    match = await self.bot.blitz.solo(self.guild_id, self.owner_id, options)
            except (UserError, discord.HTTPException) as exc:
                self.started = False
                self.rebuild()
                await interaction.edit_original_response(embed=self.summary(), view=self)
                await interaction.followup.send(f"Could not start blitz: {safe(exc, 1000)}", ephemeral=True)
                return
            from .activity_commands import blitz_embed
            await interaction.edit_original_response(content="Blitz setup submitted.", embed=blitz_embed(self.bot, match), view=None)
            self.stop()
            if self.opponents:
                await interaction.channel.send(
                    content="Invited: " + " ".join(f"<@{user}>" for user in self.opponents),
                    embed=blitz_embed(self.bot, match),
                    allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=user) for user in self.opponents]),
                )

    async def on_timeout(self):
        self.started = True
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(content="Menu expired. Open /blitz menu again.", view=self)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        log.error("Blitz menu failed", exc_info=(type(error), error, error.__traceback__))
        message = safe(error, 1000) if isinstance(error, UserError) else "Could not update the menu. Try /blitz menu again."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
