from __future__ import annotations

import asyncio
import logging

import discord

from .blitz_options import VOICE_LABELS, duration_label, page_items
from .core.options import RATINGS, TOPICS
from .core.domain import UserError
from .core.presentation import embed, safe

log = logging.getLogger(__name__)
TOPIC_CHOICES = [("any", "Any topic"), ("random", "Random topic")] + [(t, t) for t in TOPICS]
RATING_CHOICES = [("auto", "Automatic (near the lowest-rated player)")] + [(str(r), str(r)) for r in RATINGS]


class MenuSelect(discord.ui.Select):
    def __init__(self, menu, field, choices, page=0, row=0, disabled=False):
        values, _, pages = page_items(choices, page)
        selected = getattr(menu, field)
        multi = field == "topic"
        super().__init__(
            placeholder=f"{field.title()} · page {page + 1}/{pages}", row=row, disabled=disabled,
            min_values=0 if multi else 1, max_values=len(values) if multi else 1,
            options=[discord.SelectOption(label=label, value=value,
                                          default=value in menu.topics and value not in {"any", "random"}
                                          if multi else selected == value)
                     for value, label in values],
        )
        self.menu = menu
        self.field = field

    async def callback(self, interaction):
        await self.menu.choose(interaction, self.field, list(self.values) if self.field == "topic" else self.values[0])


class MenuButton(discord.ui.Button):
    def __init__(self, menu, action, label, row, style=discord.ButtonStyle.secondary, disabled=False):
        super().__init__(label=label, row=row, style=style, disabled=disabled)
        self.menu = menu
        self.action_name = action

    async def callback(self, interaction):
        await self.menu.action(interaction, self.action_name)


class BlitzTime(discord.ui.Modal, title="Blitz time limit"):
    minutes = discord.ui.TextInput(label="Minutes (1–180); leave empty for unlimited", required=False, max_length=3)

    def __init__(self, menu):
        super().__init__()
        self.menu = menu
        self.minutes.default = str(menu.options.get("minutes") or "")

    async def on_submit(self, interaction):
        if not await self.menu.interaction_check(interaction):
            return
        raw = self.minutes.value.strip()
        if raw and (not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= 180):
            await interaction.response.send_message("Enter 1–180 minutes, or leave it empty for unlimited.", ephemeral=True)
            return
        async with self.menu.lock:
            if self.menu.started:
                await interaction.response.send_message("This menu has already closed.", ephemeral=True)
                return
            self.menu.options["minutes"] = int(raw) if raw else None
            await interaction.response.edit_message(embed=self.menu.summary(), view=self.menu)


class BlitzMenu(discord.ui.View):
    def __init__(self, bot, owner_id, guild_id, opponents, options):
        super().__init__(timeout=600)
        self.bot = bot
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.opponents = opponents
        self.options = options
        self.topic = "any"
        self.topics = {"any"}
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
        self.add_item(MenuButton(self, "rule", "Everyone finishes: " +
                                 ("ON" if self.options.get("end_rule") == "all_solve" else "OFF"), 3))
        self.add_item(MenuButton(self, "standings", "Standings: " +
                                 ("ON" if self.options.get("show_standings", True) else "OFF"), 3))
        self.add_item(MenuButton(self, "start", "Start solo test" if not self.opponents else "Create lobby", 4,
                                 discord.ButtonStyle.success))
        self.add_item(MenuButton(self, "cancel", "Cancel", 4, discord.ButtonStyle.danger))
        self.add_item(MenuButton(self, "time", "Set time limit", 4))

    def summary(self):
        topic = ", ".join(dict(TOPIC_CHOICES)[t] for t in sorted(self.topics))
        rating = dict(RATING_CHOICES)[self.rating]
        manual = self.options.get("manual")
        filters = f"Manual problem: **{safe(manual)}**" if manual else f"Topic: **{topic}**\nRating: **{rating}**"
        players = self.opponents if self.options.get("hosted") else [self.owner_id, *self.opponents]
        roster = " ".join(f"<@{user}>" for user in players)
        return embed(
            "Set up RoundForge Blitz",
            f"{filters}\nVoice: **{VOICE_LABELS[self.voice]}**\n"
            f"Time: **{duration_label(self.options.get('minutes'))}**\n"
            f"Everyone finishes: **{'ON' if self.options.get('end_rule') == 'all_solve' else 'OFF (first solve wins)'}**\n"
            f"Standings: **{'ON' if self.options.get('show_standings', True) else 'OFF'}**\nPlayers: {roster}\n\n"
            f"Host: <@{self.owner_id}> · Manager: <@{self.options.get('manager_id') or self.owner_id}>\n"
            "Select multiple topics to match ANY selected topic; choices carry across pages. "
            "Use Any topic or Random topic on their own to reset the selection. More topics/ratings opens the next page. "
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
        values = value if isinstance(value, list) else [value]
        if field not in allowed or any(v not in allowed[field] for v in values):
            raise UserError("Invalid menu choice. Open /blitz menu again.")
        if field == "topic" and len(values) > 1 and {"any", "random"}.intersection(values):
            raise UserError("Choose Any topic or Random topic alone, or select several named topics.")
        if self.lock.locked():
            await interaction.response.send_message("Another menu action is in progress.", ephemeral=True)
            return
        async with self.lock:
            if self.started:
                raise UserError("This menu has already closed.")
            if field == "topic":
                page, _, _ = page_items(TOPIC_CHOICES, self.topic_page)
                remaining = self.topics - {t for t, _ in page} - {"any", "random"}
                self.topics = (remaining | set(values)) if isinstance(value, list) and not {"any", "random"}.intersection(values) else set(values)
                self.topics = self.topics or {"any"}
                self.topic = next(iter(self.topics)) if len(self.topics) == 1 else "multiple"
            else:
                setattr(self, field, value)
            self.rebuild()
            await interaction.response.edit_message(embed=self.summary(), view=self)

    def match_options(self):
        options = {**self.options, "voice_mode": self.voice}
        if not options.get("manual"):
            tags = sorted(self.topics - {"any", "random"})
            options.update(tag=tags[0] if len(tags) == 1 else None, tags=tags,
                           random_topic=self.topics == {"random"})
            rating = None if self.rating == "auto" else int(self.rating)
            options.update(rating_min=rating, rating_max=rating)
        return options

    async def action(self, interaction, action):
        if self.lock.locked():
            await interaction.response.send_message("Another menu action is in progress.", ephemeral=True)
            return
        async with self.lock:
            if self.started:
                raise UserError("This menu has already closed.")
            if action == "time":
                await interaction.response.send_modal(BlitzTime(self))
                return
            if action in {"rule", "standings"}:
                if action == "rule":
                    self.options["end_rule"] = "first_solve" if self.options.get("end_rule") == "all_solve" else "all_solve"
                else:
                    self.options["show_standings"] = not self.options.get("show_standings", True)
                self.rebuild()
                await interaction.response.edit_message(embed=self.summary(), view=self)
                return
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
                from .blitz_views import send_invite
                await send_invite(self.bot, match, interaction.channel.send, self.opponents)

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


class BlitzPlayers(discord.ui.View):
    """The no-arguments entry point: pick people, then configure the game."""
    def __init__(self, bot, owner_id, guild_id, options):
        super().__init__(timeout=600)
        self.bot, self.owner_id, self.guild_id = bot, owner_id, guild_id
        self.options, self.message = options, None
        self.started = False
        self.lock = asyncio.Lock()
        self.picker = discord.ui.UserSelect(placeholder="Select players for your blitz", min_values=1,
                                            max_values=8 if options.get("hosted") else 7)
        self.picker.callback = self.selected
        self.add_item(self.picker)

    def summary(self):
        return embed("Start a Blitz", "Choose the players from the dropdown below. "
                     + ("Pick 2–8 players; you host without automatically joining. " if self.options.get("hosted")
                        else "Pick 1–7 opponents; you join automatically. ")
                     + "Everyone needs a verified Codeforces handle. Next, choose topics, rating, time, and voice controls. "
                     "For a solo organizer test, use `/blitz solo`.")

    async def interaction_check(self, interaction):
        if interaction.guild_id != self.guild_id or interaction.user.id != self.owner_id or self.started:
            await interaction.response.send_message("Only the person who opened this menu can use it while it is open.", ephemeral=True)
            return False
        return True

    async def selected(self, interaction):
        async with self.lock:
            if self.started:
                return
            await interaction.response.defer()
            users = [user.id for user in self.picker.values]
            if not self.options.get("hosted") and self.owner_id in users:
                raise UserError("Select opponents only; you are already included.")
            if self.options.get("hosted") and len(users) < 2:
                raise UserError("Select at least two players for a hosted game.")
            for user in users:
                member = await interaction.guild.fetch_member(user)
                if member.bot:
                    raise UserError("Choose human players, not bots.")
                self.bot.community.require_member(self.guild_id, user)
            self.started = True
            view = BlitzMenu(self.bot, self.owner_id, self.guild_id, users, self.options)
            view.message = self.message
            await interaction.edit_original_response(embed=view.summary(), view=view)
            self.stop()

    async def on_timeout(self):
        self.started = True
        self.picker.disabled = True
        if self.message:
            try:
                await self.message.edit(content="Menu expired. Open /blitz menu again.", view=self)
            except discord.HTTPException:
                pass

    on_error = BlitzMenu.on_error
