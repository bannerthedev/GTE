# Complete bot.py - copy, fill TOKEN and IDs, pip install -U discord.py
import random
import os
import json
import logging
import discord
from discord.ext import commands
from typing import Optional, List, Dict, Set

logging.basicConfig(level=logging.INFO)

# ---------------- CONFIG (fill these) ----------------
TOKEN = "MTQ5NDY3NTE5MTQ1NDg5MjE4NQ.GmSkA5.UKMTknrtLnIfYP4GLLy8yZZBYmej_6QHWFUxP4"
GUILD_ID = 1322372786064195584  # your guild id as int

MATCH_TIMES_CHANNEL_ID = 1377355972116217947
ASSIGNMENTS_CHANNEL_ID = 1495153897168175295
TRANSACTIONS_CHANNEL_ID = 1377355975404818484
MATCH_SCORES_CHANNEL_ID = 1377355970518319134
# ----------------------------------------------------

DEFAULT_REF_PING = ""
DEFAULT_CASTER_PING = ""

FREE_AGENT_ROLE_NAME = "Free Agent"
TEAMS_FILE = "teams.json"

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- helpers ----------------
def is_staff(member: discord.Member) -> bool:
    return bool(getattr(member, "guild_permissions", None) and member.guild_permissions.manage_guild)

def is_captain(member: discord.Member) -> bool:
    for r in member.roles:
        if r.name.lower().startswith("captain |"):
            return True
    return False

def gtag_to_hex(code: str) -> int:
    code = str(code).strip()
    if len(code) != 3 or not code.isdigit():
        raise ValueError("Gorilla Tag code must be 3 digits")
    r = int(code[0]) * 28
    g = int(code[1]) * 28
    b = int(code[2]) * 28
    return (r << 16) + (g << 8) + b

# ---------------- persistence helpers ----------------
def load_teams() -> List[Dict]:
    if not os.path.exists(TEAMS_FILE):
        return []
    try:
        with open(TEAMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logging.exception("Failed to load teams file")
        return []

def save_teams(teams: List[Dict]) -> None:
    try:
        with open(TEAMS_FILE, "w", encoding="utf-8") as f:
            json.dump(teams, f, indent=2)
    except Exception:
        logging.exception("Failed to save teams file")

def get_member_team_name(member: discord.Member) -> Optional[str]:
    for r in member.roles:
        lower = r.name.lower()
        if lower.startswith("captain |") or lower.startswith("co-captain |") or lower.startswith("player |"):
            return r.name.split("|", 1)[1].strip()
    return None

def find_team_entry(teams: List[Dict], team_name: str) -> Optional[Dict]:
    team_name_lower = team_name.strip().lower()
    for t in teams:
        if t.get("name", "").strip().lower() == team_name_lower:
            return t
    return None

def get_team_roster_counts(guild: discord.Guild, team_name: str) -> Dict[str, int]:
    captain_role = discord.utils.get(guild.roles, name=f"Captain | {team_name}")
    cocap_role = discord.utils.get(guild.roles, name=f"Co-Captain | {team_name}")
    player_role = discord.utils.get(guild.roles, name=f"Player | {team_name}")
    counts = {
        "captain": len(captain_role.members) if captain_role else 0,
        "co_captain": len(cocap_role.members) if cocap_role else 0,
        "player": len(player_role.members) if player_role else 0,
    }
    return counts

# transactions logging helper
async def log_transaction(guild: discord.Guild, message: str):
    try:
        tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
        if tx_ch and tx_ch.guild and tx_ch.guild.id == guild.id:
            await tx_ch.send(message)
    except Exception:
        logging.exception("Failed to send transaction log")

async def log_invite_accepted(guild: discord.Guild, user: discord.Member, team_name: str):
    await log_transaction(guild, f"{user.mention} has Accepted The Invite for {team_name}")

# ---------------- UI classes ----------------
class TargetModal(discord.ui.Modal, title="Transaction"):
    target = discord.ui.TextInput(label="Target (mention or name)", required=True, max_length=200)
    reason = discord.ui.TextInput(label="Reason (optional)", required=False, max_length=500)
    def __init__(self, action: str, actor: discord.Member):
        super().__init__()
        self.action = action
        self.actor = actor
    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        tgt = self.target.value.strip()
        reason = self.reason.value.strip()
        target_member = None
        if guild and tgt.startswith("<@"):
            try:
                uid = int(tgt.strip("<@!>"))
                target_member = guild.get_member(uid)
            except Exception:
                target_member = None
        if guild and target_member is None:
            matches = [m for m in guild.members if m.display_name == tgt or m.name == tgt]
            target_member = matches[0] if matches else None
        display = target_member.mention if target_member else tgt
        entry = f"{display} — {self.action} by {self.actor.mention}"
        if reason:
            entry += f" — {reason}"
        tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
        if tx_ch:
            await tx_ch.send(entry)
        await interaction.response.send_message("Transaction recorded.", ephemeral=True)

class InviteUserSelect(discord.ui.UserSelect):
    def __init__(self, inviter: discord.Member):
        self.inviter = inviter
        super().__init__(placeholder="Who do you invite to your team?", min_values=1, max_values=1)
    def _get_team_name_from_inviter(self):
        for role in self.inviter.roles:
            lower = role.name.lower()
            if "captain |" in lower or "co-captain |" in lower:
                return role.name.split("|", 1)[1].strip()
        return None
    async def callback(self, interaction: discord.Interaction):
        target: discord.Member = self.values[0]
        team_name = self._get_team_name_from_inviter()
        if not team_name:
            await interaction.response.send_message("Could not determine your team name.", ephemeral=True); return
        teams = load_teams()
        entry = find_team_entry(teams, team_name)
        if entry and entry.get("roster_locked"):
            await interaction.response.send_message(f"Team **{team_name}** is under roster lock.", ephemeral=True); return
        # Check roster cap
        guild = interaction.guild
        if guild:
            counts = get_team_roster_counts(guild, team_name)
            if counts["player"] >= 12:
                await interaction.response.send_message(f"Team **{team_name}** is at maximum capacity (12 players).", ephemeral=True); return
        role_names = [r.name.lower() for r in getattr(target, "roles", [])]
        has_free_agent = any(FREE_AGENT_ROLE_NAME.lower() in rn for rn in role_names)
        if not has_free_agent:
            await interaction.response.send_message(f"{target.mention} is not a Free Agent.", ephemeral=True); return
        pending_invites.setdefault(target.id, []).append({"inviter_id": self.inviter.id, "team_name": team_name})
        view: InviteSelectView = self.view  # type: ignore
        for child in view.children:
            child.disabled = True
        await interaction.response.edit_message(content="Invite created. Player must run /check_invites.", view=view)

class InviteSelectView(discord.ui.View):
    def __init__(self, inviter: discord.Member):
        super().__init__(timeout=60)
        self.add_item(InviteUserSelect(inviter))

class InviteDecisionView(discord.ui.View):
    def __init__(self, user_id: int, invite_index: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.invite_index = invite_index
    def _get_invite(self):
        user_invites = pending_invites.get(self.user_id, [])
        if 0 <= self.invite_index < len(user_invites):
            return user_invites[self.invite_index]
        return None
    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This invite is not for you.", ephemeral=True); return
        invite = self._get_invite()
        if not invite:
            await interaction.response.send_message("Invite no longer available.", ephemeral=True); return
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        team_name = invite.get("team_name", "Team")
        team_role = discord.utils.get(guild.roles, name=team_name)
        player_role = discord.utils.get(guild.roles, name=f"Player | {team_name}")
        roles_to_add = [r for r in (team_role, player_role) if r]
        if roles_to_add:
            await interaction.user.add_roles(*roles_to_add, reason="Accepted team invite")
        await log_invite_accepted(guild, interaction.user, team_name)
        user_invites = pending_invites.get(self.user_id, [])
        if 0 <= self.invite_index < len(user_invites):
            user_invites.pop(self.invite_index)
        pending_invites[self.user_id] = user_invites
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="You accepted this invite.", view=self)
    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This invite is not for you.", ephemeral=True); return
        user_invites = pending_invites.get(self.user_id, [])
        if 0 <= self.invite_index < len(user_invites):
            user_invites.pop(self.invite_index)
        pending_invites[self.user_id] = user_invites
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="You declined this invite.", view=self)

class RosterSelect(discord.ui.Select):
    def __init__(self, teams: List[Dict]):
        options = [discord.SelectOption(label=t["name"], description="View this team roster") for t in teams if "name" in t]
        super().__init__(placeholder="Select a team...", min_values=1, max_values=1, options=options)
    async def callback(self, interaction: discord.Interaction):
        team_name = self.values[0]
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Must be used in a server.", ephemeral=True); return
        captain_role = discord.utils.get(guild.roles, name=f"Captain | {team_name}")
        cocap_role = discord.utils.get(guild.roles, name=f"Co-Captain | {team_name}")
        player_role = discord.utils.get(guild.roles, name=f"Player | {team_name}")
        captain = captain_role.members[0] if captain_role and captain_role.members else None
        cocap = cocap_role.members[0] if cocap_role and cocap_role.members else None
        players = list(player_role.members) if player_role else []
        lines = [f"**Team: {team_name}**", f"captain: {captain.mention if captain else 'None'}", f"co-captain: {cocap.mention if cocap else 'None'}", "players:"]
        if players:
            for idx, m in enumerate(players, start=1):
                lines.append(f"{idx}. {m.mention}")
        else:
            lines.append("No players found.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

class RosterView(discord.ui.View):
    def __init__(self, teams: List[Dict]):
        super().__init__(timeout=60)
        self.add_item(RosterSelect(teams))

# ---- New UI for additional commands ----
class AcceptView(discord.ui.View):
    def __init__(self, match_key: str, text: str, week: str, team1: str, team2: str):
        super().__init__(timeout=None)
        self.match_key = match_key
        self.text = text
        self.week = week
        self.team1 = team1
        self.team2 = team2
        self.accepted_for: Set[str] = set()

    def _fmt_accepts(self):
        a1 = "✅" if self.team1 in self.accepted_for else "❌"
        a2 = "✅" if self.team2 in self.accepted_for else "❌"
        return f"{self.team1}: {a1}\n{self.team2}: {a2}"

    async def _update_message(self, message: discord.Message):
        content = f"WEEK {self.week}\n\n{self.text}\n\nAccept status:\n{self._fmt_accepts()}"
        try:
            await message.edit(content=content, view=self)
        except Exception:
            pass

    def _is_captain(self, member: discord.Member) -> bool:
        for r in getattr(member, "roles", []):
            name = r.name.lower()
            if name.startswith("captain |") or name.startswith("co-captain |"):
                return True
        return False

    async def _handle_accept(self, interaction: discord.Interaction, target_team: str):
        user = interaction.user

        if not self._is_captain(user):
            await interaction.response.send_message(
                "Only a captain or co-captain may accept.",
                ephemeral=True
            )
            return

        if target_team in self.accepted_for:
            await interaction.response.send_message(
                f"{target_team} has already accepted.",
                ephemeral=True
            )
            return

        self.accepted_for.add(target_team)

        try:
            await self._update_message(interaction.message)
        except Exception:
            pass

        await interaction.response.send_message(
            f"Your acceptance has been recorded for {target_team}.",
            ephemeral=True
        )

        if self.team1 in self.accepted_for and self.team2 in self.accepted_for:
            match_channel = bot.get_channel(MATCH_TIMES_CHANNEL_ID)
            assign_channel = bot.get_channel(ASSIGNMENTS_CHANNEL_ID)
            match_msg = None
            if match_channel:
                match_msg = await match_channel.send(self.text)
            assignments[self.match_key] = {
                "caster": "TBD",
                "ref": "TBD",
                "commentator": [],
                "match_channel_id": match_channel.id if match_channel else None
            }
            assign_text = (
                f"Match: {self.match_key}\n"
                f"Week: {self.week}\n"
                f"Caster: TBD\n"
                f"Ref: TBD\n"
                f"Commentator: TBD"
            )
            if assign_channel:
                await assign_channel.send(assign_text, view=AssignmentView(self.match_key, match_msg))
            try:
                await interaction.followup.send(
                    "Both captains accepted — match posted and assignments created.",
                    ephemeral=True
                )
            except Exception:
                pass

    @discord.ui.button(label="Accept for Team 1", style=discord.ButtonStyle.success)
    async def accept_team1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_accept(interaction, self.team1)

    @discord.ui.button(label="Accept for Team 2", style=discord.ButtonStyle.primary)
    async def accept_team2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_accept(interaction, self.team2)

class TransactionActionView(discord.ui.View):
    def __init__(self, actor: discord.Member):
        super().__init__(timeout=120)
        self.actor = actor

    @discord.ui.select(
        placeholder="Choose action",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="invite", description="Invite a player"),
            discord.SelectOption(label="kick", description="Kick a player"),
            discord.SelectOption(label="+co-captain", description="Promote to co-captain"),
            discord.SelectOption(label="-co-captain", description="Demote co-captain"),
            discord.SelectOption(label="leave", description="Leave the team"),
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        action = select.values[0]

        member = interaction.user
        role_names = [r.name.lower() for r in getattr(member, "roles", [])]
        perms = getattr(member, "guild_permissions", None)
        is_admin = perms and (perms.administrator or perms.manage_guild)
        is_captain = any("captain |" in rn for rn in role_names)
        is_cocaptain = any("co-captain |" in rn for rn in role_names) or is_captain

        if action == "kick" and not (is_cocaptain or is_admin):
            await interaction.response.send_message("Only co-captains and above can use kick.", ephemeral=True)
            return
        if action in ("+co-captain", "-co-captain") and not (is_captain or is_admin):
            await interaction.response.send_message("Only captains and above can use this action.", ephemeral=True)
            return

        if action == "+co-captain":
            # Get team name
            team_name = None
            for r in member.roles:
                lower = r.name.lower()
                if lower.startswith("captain |"):
                    team_name = r.name.split("|", 1)[1].strip()
                    break
            if team_name and interaction.guild:
                counts = get_team_roster_counts(interaction.guild, team_name)
                if counts["co_captain"] >= 2:
                    await interaction.response.send_message(f"Team **{team_name}** already has 2 co-captains.", ephemeral=True)
                    return

        if action == "invite":
            view = InviteSelectView(member)
            await interaction.response.send_message("Who do you invite to your team?", view=view, ephemeral=True)
            return

        if action == "leave":
            team_name = None
            for r in member.roles:
                if "player |" in r.name.lower():
                    team_name = r.name.split("|", 1)[1].strip()
                    break
            entry = f"{member.mention} has left the team"
            tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
            if tx_ch:
                label = team_name if team_name else "Team"
                await tx_ch.send(f"[{label}] {entry}")
            if team_name and interaction.guild:
                roles_to_remove = [
                    discord.utils.get(interaction.guild.roles, name=team_name),
                    discord.utils.get(interaction.guild.roles, name=f"Player | {team_name}"),
                    discord.utils.get(interaction.guild.roles, name=f"Co-Captain | {team_name}")
                ]
                roles_to_remove = [r for r in roles_to_remove if r]
                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove, reason="Left the team")
            await interaction.response.send_message("Leave recorded.", ephemeral=True)
            return

        await interaction.response.send_modal(TargetModal(action, member))

# ---- CaptainPanelView ----
class CaptainPanelView(discord.ui.View):
    def __init__(self, team_name: str):
        super().__init__(timeout=None)
        self.team_name = team_name

    @discord.ui.button(label="Open Captain Actions", style=discord.ButtonStyle.primary)
    async def open_actions(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        role_names = [r.name.lower() for r in getattr(member, "roles", [])]
        is_captain = any("captain" in rn for rn in role_names)
        perms = getattr(member, "guild_permissions", None)
        has_priv = perms and (perms.administrator or perms.manage_guild)

        if not (is_captain or has_priv):
            await interaction.response.send_message(
                "Only captains, co-captains, or admins can use this panel.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Select a captain action:",
            view=TransactionActionView(member),
            ephemeral=True
        )

# ---- CoCaptainPanelView ----
class CoCaptainPanelView(discord.ui.View):
    def __init__(self, team_name: str):
        super().__init__(timeout=None)
        self.team_name = team_name

    @discord.ui.button(label="Invite Player", style=discord.ButtonStyle.primary)
    async def invite_player(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        # Check if co-captain
        role_names = [r.name.lower() for r in getattr(member, "roles", [])]
        is_cocaptain = any("co-captain |" in rn for rn in role_names)
        if not is_cocaptain:
            await interaction.response.send_message("Only co-captains can use this.", ephemeral=True)
            return
        view = InviteSelectView(member)
        await interaction.response.send_message("Who do you invite to your team?", view=view, ephemeral=True)

    @discord.ui.button(label="Kick Player", style=discord.ButtonStyle.danger)
    async def kick_player(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        # Check if co-captain
        role_names = [r.name.lower() for r in getattr(member, "roles", [])]
        is_cocaptain = any("co-captain |" in rn for rn in role_names)
        if not is_cocaptain:
            await interaction.response.send_message("Only co-captains can use this.", ephemeral=True)
            return
        await interaction.response.send_modal(TargetModal("kick", member))

# ---- state
assignments: Dict[str, Dict] = {}
pending_invites: Dict[int, List[Dict]] = {}

# ---- Assignment view (claiming)
class AssignmentView(discord.ui.View):
    def __init__(self, match_key: str, match_message: Optional[discord.Message]):
        super().__init__(timeout=None)
        self.match_key = match_key
        self.match_message = match_message
    def _fmt(self, v):
        return v
    async def update_messages(self, interaction: discord.Interaction):
        data = assignments.get(self.match_key)
        if not data:
            return
        ref_text = self._fmt(data.get("ref", "TBD"))
        caster_text = self._fmt(data.get("caster", "TBD"))
        text = (
            f"> **{self.match_key}\n"
            f"> Time: {data.get('time', 'TBD')}\n"
            f"> Referee: {ref_text}\n"
            f"> Caster: {caster_text} **"
        )
        try:
            await interaction.message.edit(content=text, view=self)
        except Exception:
            pass
        if self.match_message:
            try:
                await self.match_message.edit(content=text)
            except Exception:
                pass
    @discord.ui.button(label="Claim Caster", style=discord.ButtonStyle.primary)
    async def caster(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = assignments.get(self.match_key)
        if not data:
            await interaction.response.send_message("Assignment not found.", ephemeral=True); return
        if data.get("caster") != "TBD":
            await interaction.response.send_message("Caster already taken.", ephemeral=True); return
        data["caster"] = interaction.user.mention
        await self.update_messages(interaction)
        await interaction.response.send_message("You are now the caster.", ephemeral=True)
    @discord.ui.button(label="Claim Referee", style=discord.ButtonStyle.secondary)
    async def ref(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = assignments.get(self.match_key)
        if not data:
            await interaction.response.send_message("Assignment not found.", ephemeral=True); return
        if data.get("ref") != "TBD":
            await interaction.response.send_message("Referee already taken.", ephemeral=True); return
        data["ref"] = interaction.user.mention
        await self.update_messages(interaction)
        await interaction.response.send_message("You are now the referee.", ephemeral=True)
    @discord.ui.button(label="Unclaim", style=discord.ButtonStyle.danger)
    async def unclaim(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = assignments.get(self.match_key)
        if not data:
            await interaction.response.send_message("Assignment not found.", ephemeral=True); return
        u = interaction.user.mention
        changed = False
        if data.get("caster") == u:
            data["caster"] = "TBD"; changed = True
        elif data.get("ref") == u:
            data["ref"] = "TBD"; changed = True
        if not changed:
            await interaction.response.send_message("You have nothing to unclaim.", ephemeral=True); return
        await self.update_messages(interaction)
        await interaction.response.send_message("You unclaimed your role.", ephemeral=True)

# ---- Accept view (fixed)
class AcceptView(discord.ui.View):
    def __init__(self, match_key: str, time_str: str, week: str, team1: str, team2: str):
        super().__init__(timeout=None)
        self.match_key = match_key
        self.time_str = time_str
        self.week = week
        self.team1 = team1
        self.team2 = team2
        self.accepted_for: Set[str] = set()

    def _fmt_accepts(self):
        a1 = "✅" if self.team1 in self.accepted_for else "❌"
        a2 = "✅" if self.team2 in self.accepted_for else "❌"
        return f"{self.team1}: {a1}\n{self.team2}: {a2}"

    async def _update_message(self, message: discord.Message):
        content = f"WEEK {self.week}\n\nAccept status:\n{self._fmt_accepts()}"
        try:
            await message.edit(content=content, view=self)
        except Exception:
            pass

    def _is_captain(self, member: discord.Member) -> bool:
        for r in getattr(member, "roles", []):
            name = r.name.lower()
            if name.startswith("captain |") or name.startswith("co-captain |"):
                return True
        return False

    async def _handle_accept(self, interaction: discord.Interaction, target_team: str):
        user = interaction.user
        if not self._is_captain(user):
            await interaction.response.send_message("Only a captain or co-captain may accept.", ephemeral=True)
            return
        if target_team in self.accepted_for:
            await interaction.response.send_message(f"{target_team} has already accepted.", ephemeral=True)
            return
        self.accepted_for.add(target_team)
        try:
            await self._update_message(interaction.message)
        except Exception:
            pass
        await interaction.response.send_message(f"You accepted for {target_team}.", ephemeral=True)

        if self.team1 in self.accepted_for and self.team2 in self.accepted_for:
            match_channel = bot.get_channel(MATCH_TIMES_CHANNEL_ID)
            assign_channel = bot.get_channel(ASSIGNMENTS_CHANNEL_ID)
            ref_text = DEFAULT_REF_PING if DEFAULT_REF_PING else "TBD"
            caster_text = DEFAULT_CASTER_PING if DEFAULT_CASTER_PING else "TBD"
            base_text = (
                f"> **{self.match_key}\n"
                f"> Time: {self.time_str}\n"
                f"> Referee: {ref_text}\n"
                f"> Caster: {caster_text} **"
            )
            try:
                if match_channel:
                    await match_channel.send(base_text)
                if assign_channel:
                    await assign_channel.send(base_text)
            except Exception:
                logging.exception("Failed to post match/assignment")
            assignments[self.match_key] = {"time": self.time_str, "ref": ref_text, "caster": caster_text}
            try:
                await interaction.followup.send("Both teams accepted. Match posted.", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="Accept for Team 1", style=discord.ButtonStyle.primary)
    async def accept_team1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_accept(interaction, self.team1)

    @discord.ui.button(label="Accept for Team 2", style=discord.ButtonStyle.primary)
    async def accept_team2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_accept(interaction, self.team2)

# ---------------- Slash commands / utility commands ----------------

# ---------- FIXED create_team command (single implementation) ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="create_team", description="Create a new team (staff only)")
@discord.app_commands.describe(team_name="Name of the team", captain="Captain user", color_code="Color code (3 digits)")
async def create_team(interaction: discord.Interaction, team_name: str, captain: discord.Member, color_code: str):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Only staff can use this.", ephemeral=True)
        return

    team_name = team_name.strip()
    if not team_name:
        await interaction.response.send_message("Team name cannot be empty.", ephemeral=True)
        return

    if len(color_code) != 3 or not color_code.isdigit():
        await interaction.response.send_message("Color code must be 3 digits.", ephemeral=True)
        return
    try:
        hex_color = gtag_to_hex(color_code)
    except Exception:
        await interaction.response.send_message("Invalid color code.", ephemeral=True)
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    teams = load_teams()
    if find_team_entry(teams, team_name):
        await interaction.response.send_message(f"Team '{team_name}' already exists.", ephemeral=True)
        return

    color_obj = discord.Color(hex_color)

    try:
        team_role = discord.utils.get(guild.roles, name=team_name) or await guild.create_role(name=team_name, color=color_obj)
        player_role = discord.utils.get(guild.roles, name=f"Player | {team_name}") or await guild.create_role(name=f"Player | {team_name}", color=color_obj)
        captain_role = discord.utils.get(guild.roles, name=f"Captain | {team_name}") or await guild.create_role(name=f"Captain | {team_name}", color=color_obj)
        cocap_role = discord.utils.get(guild.roles, name=f"Co-Captain | {team_name}") or await guild.create_role(name=f"Co-Captain | {team_name}", color=color_obj)
    except Exception:
        logging.exception("Failed creating roles")
        await interaction.response.send_message("Failed to create roles.", ephemeral=True)
        return

    try:
        if captain_role not in captain.roles:
            await captain.add_roles(captain_role, team_role)
    except Exception:
        logging.exception("Failed assigning captain role")

    teams.append({"name": team_name, "color": hex_color, "captain": captain.id, "roster_locked": False})
    save_teams(teams)

    await log_transaction(guild, f"**New Team Created!**\n• Team Name: {team_role.mention}\n• Team Captain: {captain.mention}")
    await interaction.response.send_message(f"Team {team_name} created with captain {captain.mention}.", ephemeral=True)
# ---------- end create_team ----------

# ---------- submit_time command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="submit_time", description="Submit a match time")
async def submit_time(interaction: discord.Interaction, team1: str, team2: str, week: str, time: str):
    match_key = f"{team1} vs {team2}"
    channel = bot.get_channel(MATCH_TIMES_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("Match times channel not configured.", ephemeral=True)
        return
    guild = interaction.guild
    role1 = discord.utils.get(guild.roles, name=team1) if guild else None
    role2 = discord.utils.get(guild.roles, name=team2) if guild else None
    mention1 = role1.mention if role1 else team1
    mention2 = role2.mention if role2 else team2
    text = f"**{team1} vs {team2}**\n{time}\n\nCaptains, please accept to confirm this match."
    content = (
        f"WEEK {week}\n\n"
        f"{mention1} vs {mention2}\n"
        f"{time}\n\n"
        f"Accept status:\n{team1}: ❌\n{team2}: ❌"
    )
    view = AcceptView(match_key, text, week, team1, team2)
    await channel.send(content, view=view)
    await interaction.response.send_message("Match time submitted.", ephemeral=True)
# ---------- end submit_time ----------

# ---------- add_team command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="add_team", description="Add a player to a team (assign roles)")
async def add_team(interaction: discord.Interaction, member: discord.Member, team_name: str):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Must be used in guild.", ephemeral=True)
        return
    team_role = discord.utils.get(guild.roles, name=team_name)
    player_role = discord.utils.get(guild.roles, name=f"Player | {team_name}")
    if not team_role and not player_role:
        await interaction.response.send_message("Team roles not found.", ephemeral=True)
        return
    # Check roster cap
    counts = get_team_roster_counts(guild, team_name)
    if counts["player"] >= 12:
        await interaction.response.send_message(f"Team **{team_name}** is at maximum capacity (12 players).", ephemeral=True)
        return
    roles_to_add = [r for r in (team_role, player_role) if r]
    await member.add_roles(*roles_to_add, reason="Added to team")
    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(f"[{team_name}] {member.mention} has been added to the team by {interaction.user.mention}")
    await interaction.response.send_message("Player added to team.", ephemeral=True)
# ---------- end add_team ----------

# ---------- submit_score command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="submit_score", description="Submit a scrim match score")
async def submit_score(interaction: discord.Interaction, team: str, score: str, note: str = ""):
    scores_channel = bot.get_channel(MATCH_SCORES_CHANNEL_ID)
    if scores_channel is None:
        await interaction.response.send_message("Match scores channel not configured.", ephemeral=True)
        return
    await scores_channel.send(f"score: ||{score}|| ||{team}||\nnote: ||{note}||")
    await interaction.response.send_message("Score submitted.", ephemeral=True)
# ---------- end submit_score ----------

# ---------- check_invites command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="check_invites", description="Check pending team invites")
async def check_invites(interaction: discord.Interaction):
    user_invites = pending_invites.get(interaction.user.id, [])
    if not user_invites:
        await interaction.response.send_message("You have no pending invites.", ephemeral=True)
        return
    invite = user_invites[0]
    inviter = interaction.guild.get_member(invite["inviter_id"]) if interaction.guild else None
    inviter_name = inviter.display_name if inviter else "Unknown"
    content = f"Invite to join {invite.get('team_name','Team')} from {inviter_name}"
    await interaction.response.send_message(content, view=InviteDecisionView(interaction.user.id, 0), ephemeral=True)
# ---------- end check_invites ----------

# ---------- roster command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="roster", description="View team rosters from the system")
async def roster(interaction: discord.Interaction):
    teams = load_teams()
    if not teams:
        await interaction.response.send_message("No teams in the system.", ephemeral=True)
        return

    view = RosterView(teams)
    await interaction.response.send_message("Select a team to view its roster:", view=view, ephemeral=True)
# ---------- end roster ----------

# ---------- disban command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="disban", description="Disband a team (captain can disband their own; staff can disband any)")
async def disban(interaction: discord.Interaction, team_name: str = None):
    member = interaction.user
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return

    staff = is_staff(member)
    if team_name is None:
        team_name = get_member_team_name(member)
        if not team_name:
            await interaction.response.send_message(
                "You must specify a team name, or be a Captain/Co-Captain/Player of a team.",
                ephemeral=True
            )
            return
    else:
        if not staff:
            own_team = get_member_team_name(member)
            if not own_team or own_team.lower() != team_name.strip().lower():
                await interaction.response.send_message(
                    "Only staff can disband other teams. Captains may only disband their own team.",
                    ephemeral=True
                )
                return

    team_name = team_name.strip()
    team_role = discord.utils.get(guild.roles, name=team_name)
    captain_role = discord.utils.get(guild.roles, name=f"Captain | {team_name}")
    cocap_role = discord.utils.get(guild.roles, name=f"Co-Captain | {team_name}")
    player_role = discord.utils.get(guild.roles, name=f"Player | {team_name}")
    roles = [r for r in (team_role, captain_role, cocap_role, player_role) if r]

    for role in roles:
        for m in list(role.members):
            try:
                await m.remove_roles(role, reason=f"Team {team_name} disbanded")
            except Exception:
                pass
        try:
            await role.delete(reason=f"Team {team_name} disbanded")
        except Exception:
            pass

    teams = load_teams()
    teams = [t for t in teams if t.get("name", "").lower() != team_name.lower()]
    save_teams(teams)

    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(f"**{team_name}** has been disbanded by {member.mention}")

    await interaction.response.send_message(
        f"Team **{team_name}** has been disbanded.",
        ephemeral=True
    )
# ---------- end disban ----------

# ---------- disban_all command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="disban_all", description="Disband all teams in the system (staff only)")
async def disban_all(interaction: discord.Interaction):
    member = interaction.user
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return

    if not is_staff(member):
        await interaction.response.send_message("Only staff can disband all teams.", ephemeral=True)
        return

    teams = load_teams()
    for t in teams:
        team_name = t.get("name")
        if not team_name:
            continue
        team_role = discord.utils.get(guild.roles, name=team_name)
        captain_role = discord.utils.get(guild.roles, name=f"Captain | {team_name}")
        cocap_role = discord.utils.get(guild.roles, name=f"Co-Captain | {team_name}")
        player_role = discord.utils.get(guild.roles, name=f"Player | {team_name}")
        roles = [r for r in (team_role, captain_role, cocap_role, player_role) if r]
        for role in roles:
            for m in list(role.members):
                try:
                    await m.remove_roles(role, reason="All teams disbanded")
                except Exception:
                    pass
            try:
                await role.delete(reason="All teams disbanded")
            except Exception:
                pass

    save_teams([])

    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(f"All teams have been disbanded by {member.mention}")

    await interaction.response.send_message(
        "All teams in the system have been disbanded.",
        ephemeral=True
    )
# ---------- end disban_all ----------

# ---------- roster_lock command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="roster_lock", description="Enable roster lock on a team (staff only)")
async def roster_lock(interaction: discord.Interaction, team_name: str):
    member = interaction.user
    if not is_staff(member):
        await interaction.response.send_message("Only staff can roster lock teams.", ephemeral=True)
        return

    teams = load_teams()
    entry = find_team_entry(teams, team_name)
    if not entry:
        await interaction.response.send_message("Team not found in system.", ephemeral=True)
        return

    entry["roster_locked"] = True
    save_teams(teams)

    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(
            f"Roster lock has been enabled on **{entry['name']}** by {member.mention}"
        )

    await interaction.response.send_message(
        f"Roster lock enabled on **{entry['name']}**.",
        ephemeral=True
    )
# ---------- end roster_lock ----------

# ---------- roster_lock_all command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="roster_lock_all", description="Enable roster lock on all teams (staff only)")
async def roster_lock_all(interaction: discord.Interaction):
    member = interaction.user
    if not is_staff(member):
        await interaction.response.send_message("Only staff can roster lock all teams.", ephemeral=True)
        return

    teams = load_teams()
    if not teams:
        await interaction.response.send_message("No teams found in the system.", ephemeral=True)
        return

    for t in teams:
        t["roster_locked"] = True
    save_teams(teams)

    tx_ch = bot.get_channel(TRANSACTIONS_CHANNEL_ID)
    if tx_ch:
        await tx_ch.send(
            f"Roster lock has been enabled on **all teams** by {member.mention}"
        )

    await interaction.response.send_message(
        "Roster lock enabled on all teams.",
        ephemeral=True
    )
# ---------- end roster_lock_all ----------

# ---------- captain_panel command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="captain_panel", description="Show the captain panel (captains only)")
async def captain_panel(interaction: discord.Interaction):
    member = interaction.user
    guild = interaction.guild

    if not guild:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return

    # Find this user's team via Captain | TeamName or Co-Captain | TeamName
    team_name = None
    for r in member.roles:
        lower = r.name.lower()
        if lower.startswith("captain |") or lower.startswith("co-captain |"):
            team_name = r.name.split("|", 1)[1].strip()
            break

    if not team_name:
        await interaction.response.send_message(
            "You must be a Captain or Co-Captain of a team (Captain | TeamName or Co-Captain | TeamName) to use this.",
            ephemeral=True
        )
        return

    # Build roster
    captain_role = discord.utils.get(guild.roles, name=f"Captain | {team_name}")
    cocap_role = discord.utils.get(guild.roles, name=f"Co-Captain | {team_name}")
    player_role = discord.utils.get(guild.roles, name=f"Player | {team_name}")

    captain = captain_role.members[0] if captain_role and captain_role.members else None
    cocaps = cocap_role.members if cocap_role else []
    players = list(player_role.members) if player_role else []

    co_caps_text = ", ".join(m.mention for m in cocaps) if cocaps else "None"
    players_text = ", ".join(m.mention for m in players) if players else "No players found."

    desc = (
        "Review your roster, leadership, and team identity below.\n\n"
        "Use the buttons to manage invites, kicks, and leadership."
    )

    embed = discord.Embed(
        title=f"GTE Captain Panel – {team_name}",
        description=desc,
        colour=captain_role.colour if captain_role else discord.Colour.blurple()
    )
    embed.add_field(name="👑 Captain", value=captain.mention if captain else "None", inline=False)
    embed.add_field(name="🤝 Co-Captains", value=co_caps_text, inline=False)
    embed.add_field(name="🧑‍🤝‍🧑 Team Members", value=players_text, inline=False)
    embed.set_footer(text="GTE Roster Management System")

    view = CaptainPanelView(team_name)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
# ---------- end captain_panel ----------

# ---------- co-captain_panel command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="co-captain_panel", description="Show the co-captain panel (co-captains only)")
async def co_captain_panel(interaction: discord.Interaction):
    member = interaction.user
    guild = interaction.guild

    if not guild:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return

    # Find this user's team via Co-Captain | TeamName
    team_name = None
    for r in member.roles:
        lower = r.name.lower()
        if lower.startswith("co-captain |"):
            team_name = r.name.split("|", 1)[1].strip()
            break

    if not team_name:
        await interaction.response.send_message(
            "You must be a Co-Captain of a team (Co-Captain | TeamName) to use this.",
            ephemeral=True
        )
        return

    # Build roster
    captain_role = discord.utils.get(guild.roles, name=f"Captain | {team_name}")
    cocap_role = discord.utils.get(guild.roles, name=f"Co-Captain | {team_name}")
    player_role = discord.utils.get(guild.roles, name=f"Player | {team_name}")

    captain = captain_role.members[0] if captain_role and captain_role.members else None
    cocaps = cocap_role.members if cocap_role else []
    players = list(player_role.members) if player_role else []

    co_caps_text = ", ".join(m.mention for m in cocaps) if cocaps else "None"
    players_text = ", ".join(m.mention for m in players) if players else "No players found."

    desc = (
        "Review your roster, leadership, and team identity below.\n\n"
        "Use the buttons to invite or kick players."
    )

    embed = discord.Embed(
        title=f"GTE Co-Captain Panel – {team_name}",
        description=desc,
        colour=cocap_role.colour if cocap_role else discord.Colour.blurple()
    )
    embed.add_field(name="👑 Captain", value=captain.mention if captain else "None", inline=False)
    embed.add_field(name="🤝 Co-Captains", value=co_caps_text, inline=False)
    embed.add_field(name="🧑‍🤝‍🧑 Team Members", value=players_text, inline=False)
    embed.set_footer(text="GTE Roster Management System")

    view = CoCaptainPanelView(team_name)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
# ---------- end co-captain_panel ----------

# ---------- addscrim command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="addscrim", description="Create a scrim channel for two teams (staff only)")
async def addscrim(interaction: discord.Interaction, team1: discord.Role, team2: discord.Role):
    member = interaction.user
    perms = getattr(member, "guild_permissions", None)
    if not (perms and (perms.administrator or perms.manage_guild)):
        await interaction.response.send_message(
            "Only administrators or managers can use this command.",
            ephemeral=True
        )
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    # Channel name: scrim-team1-vs-team2 (lowercase, spaces -> dashes)
    ch_name = f"scrim-{team1.name}-vs-{team2.name}".lower().replace(" ", "-")

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        team1: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        team2: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }

    channel = await guild.create_text_channel(
        name=ch_name,
        overwrites=overwrites,
        reason=f"Scrim created by {member}"
    )

    await channel.send(
        f"⚔️ **Scrim Created**\n\n"
        f"{team1.mention} vs {team2.mention}\n\n"
        f"Welcome to GTE Bracket.\n"
        f"🗓️You guys will have 3 days to schedule your match.\n"
        f"⚔️And 4 days to play\n"
        f"GOOD LUCK TEAMS (you’ll need it😈)"
    )

    await interaction.response.send_message(
        f"Created {channel.mention}",
        ephemeral=True
    )
# ---------- end addscrim ----------

# ---------- code command ----------
@bot.tree.command(guild=discord.Object(id=GUILD_ID), name="code", description="Generate a random code for two teams (staff only)")
async def code(interaction: discord.Interaction, team1: discord.Role, team2: discord.Role):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Only staff can use this.", ephemeral=True)
        return

    # Generate random code: GTE + 4 digits
    import random
    code = f"GTE{random.randint(1000, 9999)}"

    channel = interaction.channel
    if not channel:
        await interaction.response.send_message("Cannot determine channel.", ephemeral=True)
        return

    message = f"{team1.mention} and {team2.mention} code is: ||{code}||"
    await channel.send(message)
    await interaction.response.send_message(f"Code generated and posted: {code}", ephemeral=True)
# ---------- end code ----------

# ---------------- on_ready and run ----------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print(f"Bot is in guilds: {[g.id for g in bot.guilds]}")
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        print(f"Bot is not in guild {GUILD_ID}. Please invite the bot to the server with the correct permissions.")
        return
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"Synced {len(synced)} command(s) to guild {GUILD_ID}")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    await print_guild_commands()

async def print_guild_commands():
    try:
        guild_obj = discord.Object(id=GUILD_ID)
        cmds = await bot.tree.fetch_commands(guild=guild_obj)
        logging.info(f"Guild-registered commands: {[c.name for c in cmds]}")
    except Exception:
        logging.exception("Failed to fetch guild commands")

if __name__ == "__main__":
    bot.run(TOKEN)