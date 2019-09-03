import math
import operator
import os
from PIL import Image, ImageDraw, ImageFont, ImageColor, ImageOps, ImageFilter
import platform
from pymongo import MongoClient
import random
import re
from collections import OrderedDict
from datetime import timedelta
from io import BytesIO
from typing import Union
import scipy
import scipy.cluster
import string
import textwrap
import time
from asyncio import TimeoutError
from pathlib import Path
from io import BytesIO

import aiohttp
import discord
from discord.utils import find
from redbot.core import bank, checks, commands, Config
from redbot.core.data_manager import bundled_data_path, cog_data_path
from redbot.core.utils.chat_formatting import pagify, box
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS
from redbot.core.utils.predicates import MessagePredicate

client = MongoClient()
db = client["leveler"]


async def non_global_bank(ctx):
    return not await bank.is_global()


class Leveler(commands.Cog):
    """Système de niveaux avec génération d'images."""

    def __init__(self, bot):
        self.bot = bot
        # fonts
        self.font_file = f"{bundled_data_path(self)}/font.ttf"
        self.font_bold_file = f"{bundled_data_path(self)}/font_bold.ttf"
        self.font_unicode_file = f"{bundled_data_path(self)}/unicode.ttf"
        self.config = Config.get_conf(self, identifier=2733301001)
        default_global = {
            "bg_price": 0,
            "badge_type": "circles",
            "message_length": 10,
            "removed_backgrounds": {"profile": [], "rank": [], "levelup": []},
            "backgrounds": {"profile": {}, "rank": {}, "levelup": {}},
            "xp": [25, 30],

            "default_profile": "https://i.imgur.com/AbZnoLE.png",
            "default_rank": "https://i.imgur.com/AbZnoLE.png",
            "default_levelup": "https://i.imgur.com/AbZnoLE.png",
            "rep_price": 0,
        }
        default_guild = {
            "disabled": False,
            "lvl_msg": True,
            "mentions": True,
            "text_only": False,
            "private_lvl_message": False,
            "lvl_msg_lock": None,
            "msg_credits": 0,
            "ignored_channels": [
                "601315913614098433",
                "598865135976710159",
                "601313885412392960",
            ],
        }
        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
        self.session = aiohttp.ClientSession(loop=self.bot.loop)

    def cog_unload(self):
        self.bot.loop.create_task(self.session.close())

    @property
    def DEFAULT_BGS(self):
        return {
            "profile": {
                "NG1": "https://i.imgur.com/AbZnoLE.png",
                },
            "rank": {
                "NG1": "https://i.imgur.com/AbZnoLE.png",
            },
            "levelup": {
                "NG1": "https://i.imgur.com/AbZnoLE.png",
        }
    }

    async def get_backgrounds(self):
        ret = self.DEFAULT_BGS
        removal_dict = await self.config.removed_backgrounds()

        for fonds_type, removals in removal_dict.items():
            for rem in removals:
                ret[fonds_type].pop(rem, None)

        user_backgrounds = await self.config.backgrounds()

        for fonds_type, update_with in user_backgrounds.items():
            ret[fonds_type].update(update_with)

        return ret

    async def delete_background(self, fonds_type: str, bg_name: str):

        found = False
        async with self.config.backgrounds() as bgs:
            if bg_name in bgs[fonds_type]:
                found = True
                del bgs[fonds_type][bg_name]

        try:
            _k = self.DEFAULT_BGS[fonds_type][bg_name]
        except KeyError:
            if not found:
                raise
        else:
            async with self.config.removed_backgrounds() as rms:
                if bg_name not in rms[fonds_type]:
                    rms[fonds_type].append(bg_name)

    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.command(name="profile")
    @commands.guild_only()
    async def profile(self, ctx, *, user: discord.Member = None):
        """Afficher la carte de profil d'un utilisateur."""
        if user is None:
            user = ctx.message.author
        channel = ctx.message.channel
        server = user.guild
        curr_time = time.time()

        # creates user if doesn't exist
        await self._create_user(user, server)
        userinfo = db.users.find_one({"user_id": str(user.id)})

        # check if disabled
        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("**Toutes les commandes pour le système de niveau sont désactivées !**")
            return
        if user.bot:
            await ctx.send("**Un robot ne peut pas avoir de profil voyons !**")
            return
        # no cooldown for text only
        if await self.config.guild(ctx.guild).text_only():
            em = await self.profile_text(user, server, userinfo)
            await channel.send(embed=em)
        else:
            async with ctx.channel.typing():
                await self.draw_profile(user, server)
                file = discord.File(
                    f"{cog_data_path(self)}/{user.id}_profile.png", filename="profile.png"
                )
                await channel.send(
                    "**Profil de {}**".format(await self._is_mention(user)), file=file
                )
            db.users.update_one(
                {"user_id": str(user.id)}, {"$set": {"profile_block": curr_time}}, upsert=True
            )
            try:
                os.remove(f"{cog_data_path(self)}/{user.id}_profile.png")
            except:
                pass

    async def profile_text(self, user, server, userinfo):
        def test_empty(text):
            if not text:
                return "None"
            else:
                return text

        em = discord.Embed(colour=user.colour)
        em.add_field(name="Titre:", value=test_empty(userinfo["title"]))
        em.add_field(name="Reps:", value=userinfo["rep"])
        em.add_field(name="Rank global:", value="#{}".format(await self._find_global_rank(user)))
        em.add_field(
            name="Rank du serveur:", value="#{}".format(await self._find_server_rank(user, server))
        )
        em.add_field(
            name="Niveau:", value=format(userinfo["servers"][str(server.id)]["level"])
        )
        em.add_field(name="XP Total:", value=userinfo["total_exp"])
        em.add_field(name="XP:", value=await self._find_server_exp(user, server))
        u_credits = await bank.get_balance(user)
        em.add_field(name="Crédits: ", value="${}".format(u_credits))
        em.add_field(name="Info: ", value=test_empty(userinfo["info"]))
        em.add_field(
            name="Badges: ", value=test_empty(", ".join(userinfo["badges"])).replace("_", " ")
        )
        em.set_author(name="Carte de profil de {}".format(user.name), url=user.avatar_url)
        em.set_thumbnail(url=user.avatar_url)
        return em

    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.command()
    @commands.guild_only()
    async def rank(self, ctx, user: discord.Member = None):
        """Afficher la carte de rank d'un membre."""
        if user is None:
            user = ctx.message.author
        channel = ctx.message.channel
        server = user.guild
        curr_time = time.time()

        # creates user if doesn't exist
        await self._create_user(user, server)
        userinfo = db.users.find_one({"user_id": str(user.id)})

        # check if disabled
        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("**Toutes les commandes pour le système de niveau sont désactivées !**")
            return
        if user.bot:
            await ctx.send("**Un robot ne peut pas avoir de rank voyons !**")
            return
        # no cooldown for text only
        if await self.config.guild(server).text_only():
            em = await self.rank_text(user, server, userinfo)
            await channel.send("", embed=em)
        else:
            async with ctx.typing():
                await self.draw_rank(user, server)
                file = discord.File(
                    f"{cog_data_path(self)}/{user.id}_rank.png", filename="rank.png"
                )
                await ctx.send(
                    "**Carte de Rank de {}**".format(await self._is_mention(user)),
                    file=file,
                )
            db.users.update_one(
                {"user_id": str(user.id)},
                {"$set": {"rank_block".format(server.id): curr_time}},
                upsert=True,
            )
            try:
                os.remove(f"{cog_data_path(self)}/{user.id}_rank.png")
            except:
                pass

    async def rank_text(self, user, server, userinfo):
        em = discord.Embed(colour=user.colour)
        em.add_field(
            name="Rank du serveur", value="#{}".format(await self._find_server_rank(user, server))
        )
        em.add_field(name="Reps", value=userinfo["rep"])
        em.add_field(name="Niveau", value=userinfo["servers"][str(server.id)]["level"])
        em.add_field(name="XP", value=await self._find_server_exp(user, server))
        em.set_author(name="Rank et Statistiques de {}".format(user.name), url=user.avatar_url)
        em.set_thumbnail(url=user.avatar_url)
        return em

    # should the user be mentioned based on settings?
    async def _is_mention(self, user):
        if await self.config.guild(user.guild).mentions():
            return user.mention
        else:
            return user.name

    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.command()
    @commands.guild_only()
    async def ldb(self, ctx, *options):
        """Leaderboard du serveur."""
        if not ctx.message.channel.permissions_for(ctx.guild.me).embed_links:
            return await ctx.send(
                "**Je n'ai pas les permissions nécéssaires pour réaliser celà.**"
            )

        server = ctx.guild
        user = ctx.author

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("**Toutes les commandes pour le système de niveau sont désactivées !**")
            return

        users = []
        user_stat = None
        if "-rep" in options and "-global" in options:
            title = "Leaderboard global de reps des serveurs qui ont {}\n".format(self.bot.user.name)
            for userinfo in db.users.find({}):
                try:
                    users.append((userinfo["username"], userinfo["rep"]))
                except:
                    users.append((userinfo["user_id"], userinfo["rep"]))

                if str(user.id) == userinfo["user_id"]:
                    user_stat = userinfo["rep"]

            board_type = "Reps:"
            footer_text = "Votre Rank: {}                  {}: {}".format(
                await self._find_global_rep_rank(user), board_type, user_stat
            )
            icon_url = self.bot.user.avatar_url
        elif "-global" in options:
            title = "Leaderboard de reps des serveurs qui ont {}\n".format(self.bot.user.name)
            for userinfo in db.users.find({}):
                try:
                    users.append((userinfo["username"], userinfo["total_exp"]))
                except:
                    users.append((userinfo["user_id"], userinfo["total_exp"]))

                if str(user.id) == userinfo["user_id"]:
                    user_stat = userinfo["total_exp"]

            board_type = "Points:"
            footer_text = "Votre Rank: {}                  {}: {}".format(
                await self._find_global_rank(user), board_type, user_stat
            )
            icon_url = self.bot.user.avatar_url
        elif "-rep" in options:
            title = "Leaderboard du serveur\n"
            for userinfo in db.users.find({}):
                if "servers" in userinfo and str(server.id) in userinfo["servers"]:
                    try:
                        users.append((userinfo["username"], userinfo["rep"]))
                    except:
                        users.append((userinfo["user_id"], userinfo["rep"]))

                if str(user.id) == userinfo["user_id"]:
                    user_stat = userinfo["rep"]

            board_type = "Reps:"
            footer_text = "Votre Rank: {}                  {}: {}".format(
                await self._find_server_rep_rank(user, server), board_type, user_stat
            )
            icon_url = server.icon_url
        else:
            title = "Leaderboard d'XP du serveur\n"
            for userinfo in db.users.find({}):
                try:
                    if "servers" in userinfo and str(server.id) in userinfo["servers"]:
                        server_exp = 0
                        for i in range(userinfo["servers"][str(server.id)]["level"]):
                            server_exp += self._required_exp(i)
                        server_exp += userinfo["servers"][str(server.id)]["current_exp"]
                        try:
                            users.append((userinfo["username"], server_exp))
                        except:
                            users.append((userinfo["user_id"], server_exp))
                except Exception as e:
                    print(e)
            board_type = "Points:"
            footer_text = "Votre Rank: {}                  {}: {}".format(
                await self._find_server_rank(user, server),
                board_type,
                await self._find_server_exp(user, server),
            )
            icon_url = server.icon_url
        sorted_list = sorted(users, key=operator.itemgetter(1), reverse=True)

        # multiple page support
        page = 1
        per_page = 15
        pages = math.ceil(len(sorted_list) / per_page)
        for option in options:
            if str(option).isdigit():
                if page >= 1 and int(option) <= pages:
                    page = int(str(option))
                else:
                    await ctx.send(
                        "**Merci d'entrer un numéro de page valide ! (1 - {})**".format(str(pages))
                    )
                    return
                break

        msg = ""
        msg += "Rank     Nom                   (Page {}/{})     \n\n".format(page, pages)
        rank = 1 + per_page * (page - 1)
        start_index = per_page * page - per_page
        end_index = per_page * page

        default_label = "   "
        special_labels = ["♔", "♕", "♖", "♗", "♘", "♙"]

        for single_user in sorted_list[start_index:end_index]:
            if rank - 1 < len(special_labels):
                label = special_labels[rank - 1]
            else:
                label = default_label

            msg += "{:<2}{:<2}{:<2} # {:<11}".format(
                rank, label, "➤", self._truncate_text(single_user[0], 11)
            )
            msg += "{:>5}{:<2}{:<2}{:<5}\n".format(
                " ", " ", " ", " {}: ".format(board_type) + str(single_user[1])
            )
            rank += 1
        msg += "--------------------------------------------            \n"
        msg += "{}".format(footer_text)

        em = discord.Embed(description="", colour=user.colour)
        em.set_author(name=title, icon_url=icon_url)
        em.description = box(msg)

        await ctx.send(embed=em)

    @commands.cooldown(1, 30, commands.BucketType.user)
    @commands.command()
    @commands.guild_only()
    async def rep(self, ctx, user: discord.Member = None):
        """Donne une réputation à un membre spécifique."""
        org_user = ctx.author
        server = ctx.guild
        # creates user if doesn't exist
        await self._create_user(org_user, server)
        if user:
            await self._create_user(user, server)
        org_userinfo = db.users.find_one({"user_id": str(org_user.id)})
        curr_time = time.time()

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("**Toutes les commandes pour le système de niveau sont désactivées !**")
            return
        if user and user.id == org_user.id:
            await ctx.send("**Vous ne pouvez pas vous donner une rep à vous-même voyons !**")
            return
        if user and user.bot:
            await ctx.send("**Vous ne pouvez pas donner une rep à un robot, lmao...**")
            return
        if "rep_block" not in org_userinfo:
            org_userinfo["rep_block"] = 0

        delta = float(curr_time) - float(org_userinfo["rep_block"])
        if user and delta >= 43200.0 and delta > 0:
            userinfo = db.users.find_one({"user_id": str(user.id)})
            db.users.update_one({"user_id": str(org_user.id)}, {"$set": {"rep_block": curr_time}})
            db.users.update_one({"user_id": str(user.id)}, {"$set": {"rep": userinfo["rep"] + 1}})
            await ctx.send(
                "**Vous avez donné à {} un point de réputation ! Vous êtes super gentil(le) ! u///u**".format(
                    await self._is_mention(user)
                )
            )
        else:
            # calulate time left
            seconds = 43200 - delta
            if seconds < 0:
                await ctx.send("**Vous pouvez donner un point de réputation à quelqu'un !**")
                return

            m, s = divmod(seconds, 60)
            h, m = divmod(m, 60)
            await ctx.send(
                "**Vous devez attendre {} heures, {} minutes et {} secondes avant de pouvoir redonner un point de réputation à un autre membre !**".format(
                    int(h), int(m), int(s)
                )
            )
    @commands.cooldown(1, 30, commands.BucketType.user)
    @commands.command()
    @commands.guild_only()
    async def rep(self, ctx, user: discord.Member = None):
        """Donner une réputation à un membre spécifique."""
        org_user = ctx.author
        server = ctx.guild
        # creates user if doesn't exist
        await self._create_user(org_user, server)
        if user:
            await self._create_user(user, server)
        org_userinfo = db.users.find_one({"user_id": str(org_user.id)})
        curr_time = time.time()

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("**Toutes les commandes pour le système de niveau sont désactivées !**")
            return
        if user and user.id == org_user.id:
            await ctx.send("**Vous ne pouvez pas vous donner une rep à vous-même voyons ! :'3**")
            return
        if user and user.bot:
            await ctx.send("**Vous ne pouvez pas donner une rep à un robot, lmao...**")
            return
        if "rep_block" not in org_userinfo:
            org_userinfo["rep_block"] = 0

        delta = float(curr_time) - float(org_userinfo["rep_block"])
        if user and delta >= 43200.0 and delta > 0:
            userinfo = db.users.find_one({"user_id": str(user.id)})
            db.users.update_one({"user_id": str(org_user.id)}, {"$set": {"rep_block": curr_time}})
            db.users.update_one({"user_id": str(user.id)}, {"$set": {"rep": userinfo["rep"] + 1}})
            await ctx.send(
                "**Vous avez donné à {} un point de réputation ! Vous êtes super gentil(le) ! u///u**".format(
                    await self._is_mention(user)
                )
            )
        else:
            # calulate time left
            seconds = 43200 - delta
            if seconds < 0:
                await ctx.send("**Vous pouvez donner un point de réputation à quelqu'un !**")
                return

            m, s = divmod(seconds, 60)
            h, m = divmod(m, 60)
            await ctx.send(
                "**Vous devez attendre {} heures, {} minutes et {} secondes avant de pouvoir redonner un point de réputation à un autre membre !**".format(
                    int(h), int(m), int(s)
                )
            )

    @commands.cooldown(1, 30, commands.BucketType.user)
    @commands.command()
    @commands.guild_only()
    async def represet(self, ctx):
        """Effacer en payant le cooldown pour donner une rep."""
        if await self.config.guild(ctx.guild).disabled():
            return await ctx.send("**Toutes les commandes pour le système de niveau sont désactivées !**")

        rep_price = await self.config.rep_price()
        if rep_price == 0:
            return await ctx.send(
                "**Le module de rénitialisation du cooldown lié aux reps en payant n'a pas été activé. Demandez à Florian de le faire ?**"
            )

        user = ctx.author
        server = ctx.guild
        # creates user if doesn't exist
        await self._create_user(user, server)

        userinfo = db.users.find_one({"user_id": str(user.id)})
        if "rep_block" not in userinfo:
            userinfo["rep_block"] = 0

        curr_time = time.time()
        delta = float(curr_time) - float(userinfo["rep_block"])
        if delta >= 43200.0 and delta > 0:
            return await ctx.send("**Eh ! Vous n'avez pas besoin d'effacer le cooldown tout simplement car vous n'en avez pas et vous pouvez donner un point de réputation à quelqu'un.**")

        if not await bank.can_spend(user, rep_price):
            await ctx.send("**Vous n'avez pas assez de crédits. Cette action coûte {} crédits.**".format(rep_price))
        else:
            currency_name = await bank.get_currency_name(ctx.guild)
            await ctx.send(
                "**{}, vous vous apprêtez à rénitialiser le cooldown en payant {} {}. Confirmez en tapant `yes` sinon tapez `no`.**".format(
                    await self._is_mention(user), rep_price, currency_name
                )
            )
            pred = MessagePredicate.yes_or_no(ctx)
            try:
                await self.bot.wait_for("message", check=pred, timeout=15)
            except TimeoutError:
                return await ctx.send("**Action annulée.**")
            if not pred.result:
                return await ctx.send("**Action annulée.**")

            await bank.withdraw_credits(user, rep_price)
            db.users.update_one(
                {"user_id": str(user.id)}, {"$set": {"rep_block": (float(curr_time) - 43201.0)}}
            )
            await ctx.send("**Merci! Le cooldown a été rénitialiser, vous pouvez donner une rep à nouveau !**")
    
    @commands.command()
    @commands.guild_only()
    async def membreinfo(self, ctx, user: discord.Member = None):
        """Afficher le profil détaillé d'un membre."""
        if not ctx.message.channel.permissions_for(ctx.guild.me).embed_links:
            return await ctx.send(
                "**Je n'ai pas les permissions nécéssaires pour réaliser celà.**"
            )
        if not user:
            user = ctx.author
        server = ctx.guild
        userinfo = db.users.find_one({"user_id": str(user.id)})

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("**Toutes les commandes pour le système de niveau sont désactivées !**")
            return
        if user.bot:
             return
        # creates user if doesn't exist
        await self._create_user(user, server)
        msg = ""
        msg += "Nom: {}\n".format(user.name)
        msg += "Titre de profil: {}\n".format(userinfo["title"])
        msg += "Reps: {}\n".format(userinfo["rep"])
        msg += "Niveau du serveur: {}\n".format(userinfo["servers"][str(server.id)]["level"])
        total_server_exp = 0
        for i in range(userinfo["servers"][str(server.id)]["level"]):
            total_server_exp += self._required_exp(i)
        total_server_exp += userinfo["servers"][str(server.id)]["current_exp"]
        msg += "XP: {}\n".format(total_server_exp)
        msg += "XP max: {}\n".format(userinfo["total_exp"])
        msg += "Info.: {}\n".format(userinfo["info"])
        msg += "Fond de la carte de profil: {}\n".format(userinfo["profile_background"])
        msg += "Fond de la carte de rank: {}\n".format(userinfo["rank_background"])
        msg += "Fond de lvl-up: {}\n".format(userinfo["levelup_background"])
        if "profile_info_color" in userinfo.keys() and userinfo["profile_info_color"]:
            msg += "Information de couleur (profil): {}\n".format(
                self._rgb_to_hex(userinfo["profile_info_color"])
            )
        if "profile_exp_color" in userinfo.keys() and userinfo["profile_exp_color"]:
            msg += "Couleur de l'XP (profil): {}\n".format(
                self._rgb_to_hex(userinfo["profile_exp_color"])
            )
        if "rep_color" in userinfo.keys() and userinfo["rep_color"]:
            msg += "Couleur de la section des reps (profil): {}\n".format(self._rgb_to_hex(userinfo["rep_color"]))
        if "badge_col_color" in userinfo.keys() and userinfo["badge_col_color"]:
            msg += "Couleur de la section des badges (profil) {}\n".format(
                self._rgb_to_hex(userinfo["badge_col_color"])
            )
        if "rank_info_color" in userinfo.keys() and userinfo["rank_info_color"]:
            msg += "Information de couleur (rank): {}\n".format(self._rgb_to_hex(userinfo["rank_info_color"]))
        if "rank_exp_color" in userinfo.keys() and userinfo["rank_exp_color"]:
            msg += "Couleur de l'XP (rank): {}\n".format(self._rgb_to_hex(userinfo["rank_exp_color"]))
        if "levelup_info_color" in userinfo.keys() and userinfo["levelup_info_color"]:
            msg += "Information de couleur (lvl-up) {}\n".format(
                self._rgb_to_hex(userinfo["levelup_info_color"])
            )
        msg += "Badges: "
        msg += ", ".join(userinfo["badges"])

        em = discord.Embed(description=msg, colour=user.colour)
        em.set_author(
            name="Profil détaillé de {}".format(user.name), icon_url=user.avatar_url
        )
        await ctx.send(embed=em)

    def _rgb_to_hex(self, rgb):
        rgb = tuple(rgb[:3])
        return "#%02x%02x%02x" % rgb

    @commands.group(name="gestion", pass_context=True)
    async def gestion(self, ctx):
        """Gérer vos infos/fonds/badges."""
        pass

    @gestion.group(name="profile", pass_context=True)
    async def profileset(self, ctx):
        """Gestion de la carte de profil."""
        pass

    @gestion.group(name="rank", pass_context=True)
    async def rankset(self, ctx):
        """Gestion de la carte de rank."""
        pass

    @gestion.group(name="levelup", pass_context=True)
    async def levelupset(self, ctx):
        """Gestion de Lvl-up."""
        pass

    @gestion.group(name="badge")
    async def changebadge0(self, ctx):
        """Gestion des badges."""
        pass
    @gestion.group()
    async def btk(self, ctx):
        """Boutique de badges/fonds."""
        pass
        
    @changebadge0.command()
    @commands.guild_only()
    async def set(self, ctx, name: str, priority_num: int):
        """Mettre un badge sur votre profil.\n Arguments possibles :\n`-1` (permet de cacher le badge) ; `0` (pas visible sur le profil) ; `1-5000` (Un badge avec une faible valeur de priorité comme 1 apparaitra toujours avant les autres badges!)"""
        user = ctx.author
        server = ctx.guild
        await self._create_user(user, server)

        userinfo = db.users.find_one({"user_id": str(user.id)})
        userinfo = self._badge_convert_dict(userinfo)

        if priority_num < -1 or priority_num > 5000:
            await ctx.send("**Numéro de priorité invalide ! Arguments possibles : -1 (permet de cacher le badge) ; `0` (pas visible sur le profil) ; `1-5000` (Un badge avec une faible valeur de priorité comme 1 apparaitra toujours avant les autres badges!)**")
            return

        for badge in userinfo["badges"]:
            if userinfo["badges"][badge]["badge_name"] == name:
                userinfo["badges"][badge]["priority_num"] = priority_num
                db.users.update_one(
                    {"user_id": userinfo["user_id"]}, {"$set": {"badges": userinfo["badges"]}}
                )
                await ctx.send(
                    "**La valeur de priorité du badge `{}` a été appliquée à `{}`!**".format(
                        userinfo["badges"][badge]["badge_name"], priority_num
                    )
                )
                break
        else:
            await ctx.send("**Vous n'avez pas ce badge !**")

    def _badge_convert_dict(self, userinfo):
        if "badges" not in userinfo or not isinstance(userinfo["badges"], dict):
            db.users.update_one({"user_id": userinfo["user_id"]}, {"$set": {"badges": {}}})
        return db.users.find_one({"user_id": userinfo["user_id"]})

    @profileset.command(name="clr", pass_context=True, no_pm=True)
    async def profilecolors(self, ctx, section: str, color: str):
        """Changer les couleurs de certaines section de votre carte de profil.
        Exemple: /gestion profile clr [xp|rep|badge|info|all] [default|white|hex|auto]"""
        user = ctx.author
        server = ctx.guild
        # creates user if doesn't exist
        await self._create_user(user, server)
        userinfo = db.users.find_one({"user_id": str(user.id)})

        section = section.lower()
        default_info_color = (30, 30, 30, 200)
        white_info_color = (150, 150, 150, 180)
        default_rep = (92, 130, 203, 230)
        default_badge = (128, 151, 165, 230)
        default_exp = (255, 255, 255, 230)
        default_a = 200

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("**Toutes les commandes pour le système de niveau sont désactivées !**")
            return

        if await self.config.guild(ctx.guild).text_only():
            await ctx.send("**Red a dit qu'il n'autorisé que le texte.**")
            return

        # get correct section for db query
        if section == "rep":
            section_name = "rep_color"
        elif section == "xp":
            section_name = "profile_exp_color"
        elif section == "badge":
            section_name = "badge_col_color"
        elif section == "info":
            section_name = "profile_info_color"
        elif section == "all":
            section_name = "all"
        else:
            await ctx.send("**Ce n'est pas un argument valide. Veuillez indiqué un argument parmis la liste entre parenthèse (rep, xp, badge, info, all).**")
            return

        # get correct color choice
        if color == "auto":
            if section == "xp":
                color_ranks = [random.randint(2, 3)]
            elif section == "rep":
                color_ranks = [random.randint(2, 3)]
            elif section == "badge":
                color_ranks = [0]  # most prominent color
            elif section == "info":
                color_ranks = [random.randint(0, 1)]
            elif section == "all":
                color_ranks = [random.randint(2, 3), random.randint(2, 3), 0, random.randint(0, 2)]

            hex_colors = await self._auto_color(ctx, userinfo["profile_background"], color_ranks)
            set_color = []
            for hex_color in hex_colors:
                color_temp = self._hex_to_rgb(hex_color, default_a)
                set_color.append(color_temp)

        elif color == "white":
            set_color = [white_info_color]
        elif color == "default":
            if section == "xp":
                set_color = [default_exp]
            elif section == "rep":
                set_color = [default_rep]
            elif section == "badge":
                set_color = [default_badge]
            elif section == "info":
                set_color = [default_info_color]
            elif section == "all":
                set_color = [default_exp, default_rep, default_badge, default_info_color]
        elif self._is_hex(color):
            set_color = [self._hex_to_rgb(color, default_a)]
        else:
            await ctx.send("**Ce n'est pas un argument valide. Veuillez indiqué un argument parmis la liste entre parenthèse (default, hex, white, auto).**")
            return

        if section == "all":
            if len(set_color) == 1:
                db.users.update_one(
                    {"user_id": str(user.id)},
                    {
                        "$set": {
                            "profile_exp_color": set_color[0],
                            "rep_color": set_color[0],
                            "badge_col_color": set_color[0],
                            "profile_info_color": set_color[0],
                        }
                    },
                )
            elif color == "default":
                db.users.update_one(
                    {"user_id": str(user.id)},
                    {
                        "$set": {
                            "profile_exp_color": default_exp,
                            "rep_color": default_rep,
                            "badge_col_color": default_badge,
                            "profile_info_color": default_info_color,
                        }
                    },
                )
            elif color == "auto":
                db.users.update_one(
                    {"user_id": str(user.id)},
                    {
                        "$set": {
                            "profile_exp_color": set_color[0],
                            "rep_color": set_color[1],
                            "badge_col_color": set_color[2],
                            "profile_info_color": set_color[3],
                        }
                    },
                )
            await ctx.send("**Les couleurs ont bien été changées sur la carte de profil.**")
        else:
            # print("update one")
            db.users.update_one({"user_id": str(user.id)}, {"$set": {section_name: set_color[0]}})
            await ctx.send("**La couleur de la section `{}` a bien été appliquée sur la carte de profil.**".format(section))

    @rankset.command(name="clr")
    @commands.guild_only()
    async def rankcolors(self, ctx, section: str, color: str = None):
        """Changer les couleurs de votre carte de rank.
        Exemple : /gestion rank clr [xp|info] [default|white|hex|auto]"""
        user = ctx.author
        server = ctx.guild
        # creates user if doesn't exist
        await self._create_user(user, server)
        userinfo = db.users.find_one({"user_id": str(user.id)})

        section = section.lower()
        default_info_color = (30, 30, 30, 200)
        white_info_color = (150, 150, 150, 180)
        default_exp = (255, 255, 255, 230)
        default_rep = (92, 130, 203, 230)
        default_badge = (128, 151, 165, 230)
        default_a = 200

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("**Toutes les commandes pour le système de niveau sont désactivées !**")
            return

        if await self.config.guild(ctx.guild).text_only():
            await ctx.send("**Red a dit qu'il n'autorisé que le texte.**")
            return

        # get correct section for db query
        if section == "xp":
            section_name = "rank_exp_color"
        elif section == "info":
            section_name = "rank_info_color"
        elif section == "all":
            section_name = "all"
        else:
            await ctx.send("**Ce n'est pas un argument valide. Veuillez indiqué un argument parmis la liste entre parenthèse (exp, info, all).**")
            return

        # get correct color choice
        if color == "auto":
            if section == "xp":
                color_ranks = [random.randint(2, 3)]
            elif section == "info":
                color_ranks = [random.randint(0, 1)]
            elif section == "all":
                color_ranks = [random.randint(2, 3), random.randint(0, 1)]

            hex_colors = await self._auto_color(ctx, userinfo["rank_background"], color_ranks)
            set_color = []
            for hex_color in hex_colors:
                color_temp = self._hex_to_rgb(hex_color, default_a)
                set_color.append(color_temp)
        elif color == "white":
            set_color = [white_info_color]
        elif color == "default":
            if section == "xp":
                set_color = [default_exp]
            elif section == "info":
                set_color = [default_info_color]
            elif section == "all":
                set_color = [default_exp, default_rep, default_badge, default_info_color]
        elif self._is_hex(color):
            set_color = [self._hex_to_rgb(color, default_a)]
        else:
            await ctx.send("**Ce n'est pas un argument valide. Veuillez indiqué un argument parmis la liste entre parenthèse (default, hex, white, auto).**")
            return

        if section == "all":
            if len(set_color) == 1:
                db.users.update_one(
                    {"user_id": str(user.id)},
                    {"$set": {"rank_exp_color": set_color[0], "rank_info_color": set_color[0]}},
                )
            elif color == "default":
                db.users.update_one(
                    {"user_id": str(user.id)},
                    {
                        "$set": {
                            "rank_exp_color": default_exp,
                            "rank_info_color": default_info_color,
                        }
                    },
                )
            elif color == "auto":
                db.users.update_one(
                    {"user_id": str(user.id)},
                    {"$set": {"rank_exp_color": set_color[0], "rank_info_color": set_color[1]}},
                )
            await ctx.send("**Les couleurs ont bien été changées sur la carte de rank.**")
        else:
            db.users.update_one({"user_id": str(user.id)}, {"$set": {section_name: set_color[0]}})
            await ctx.send("**La couleur de la section `{}` a bien été appliquée à la carte de rank.**".format(section))

    @levelupset.command(name="clr")
    @commands.guild_only()
    async def levelupcolors(self, ctx, section: str, color: str = None):
        """Changer les couleurs de certaines sections de votre image de lvl-up. 
        Exemple : /gestion levelup clr info [default|white|hex|auto]"""
        user = ctx.author
        server = ctx.guild
        # creates user if doesn't exist
        await self._create_user(user, server)
        userinfo = db.users.find_one({"user_id": str(user.id)})

        section = section.lower()
        default_info_color = (30, 30, 30, 200)
        white_info_color = (150, 150, 150, 180)
        default_a = 200

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("**Toutes les commandes pour le système de niveau sont désactivées !**")
            return

        if await self.config.guild(ctx.guild).text_only():
            await ctx.send("**Red a dit qu'il n'autorisé que le texte.**")
            return

        # get correct section for db query
        if section == "info":
            section_name = "levelup_info_color"
        else:
            await ctx.send("**Ce n'est pas un argument valide. Veuillez indiqué un argument parmis la liste entre parenthèse (info).**")
            return

        # get correct color choice
        if color == "auto":
            if section == "info":
                color_ranks = [random.randint(0, 1)]
            hex_colors = await self._auto_color(ctx, userinfo["levelup_background"], color_ranks)
            set_color = []
            for hex_color in hex_colors:
                color_temp = self._hex_to_rgb(hex_color, default_a)
                set_color.append(color_temp)
        elif color == "white":
            set_color = [white_info_color]
        elif color == "default":
            if section == "info":
                set_color = [default_info_color]
        elif self._is_hex(color):
            set_color = [self._hex_to_rgb(color, default_a)]
        else:
            await ctx.send("**Ce n'est pas un argument valide. Veuillez indiqué un argument parmis la liste entre parenthèse (default, hex, white, auto).**")
            return

        db.users.update_one({"user_id": str(user.id)}, {"$set": {section_name: set_color[0]}})
        await ctx.send("**La couleur de la section `{}` pour l'image de lvl-up a été appliquée.**".format(section))

    # uses k-means algorithm to find color from bg, rank is abundance of color, descending
    async def _auto_color(self, ctx, url: str, ranks):
        phrases = ["Calcul des couleurs...", "Calcul de diverses fonctions..."]  # in case I want more
        await ctx.send("**{}**".format(random.choice(phrases)))
        clusters = 10

        async with self.session.get(url) as r:
            image = await r.content.read()
        with open(f"{cog_data_path(self)}/temp_auto.png", "wb") as f:
            f.write(image)

        im = Image.open(f"{cog_data_path(self)}/temp_auto.png").convert("RGBA")
        im = im.resize((290, 290))  # resized to reduce time
        ar = numpy.asarray(im)
        shape = ar.shape
        ar = ar.reshape(scipy.product(shape[:2]), shape[2])

        codes, dist = scipy.cluster.vq.kmeans(ar.astype(float), clusters)
        vecs, dist = scipy.cluster.vq.vq(ar, codes)  # assign codes
        counts, bins = scipy.histogram(vecs, len(codes))  # count occurrences

        
        # sort counts
        freq_index = []
        index = 0
        for count in counts:
            freq_index.append((index, count))
            index += 1
        sorted_list = sorted(freq_index, key=operator.itemgetter(1), reverse=True)

        colors = []
        for rank in ranks:
            color_index = min(rank, len(codes))
            peak = codes[sorted_list[color_index][0]]  # gets the original index
            peak = peak.astype(int)

            colors.append("".join(format(c, "02x") for c in peak))
        return colors  # returns array

    # converts hex to rgb
    def _hex_to_rgb(self, hex_num: str, a: int):
        h = hex_num.lstrip("#")

        # if only 3 characters are given
        if len(str(h)) == 3:
            expand = "".join([x * 2 for x in str(h)])
            h = expand

        colors = [int(h[i : i + 2], 16) for i in (0, 2, 4)]
        colors.append(a)
        return tuple(colors)

    # dampens the color given a parameter
    def _moderate_color(self, rgb, a, moderate_num):
        new_colors = []
        for color in rgb[:3]:
            if color > 128:
                color -= moderate_num
            else:
                color += moderate_num
            new_colors.append(color)
        new_colors.append(230)

        return tuple(new_colors)

    @profileset.command()
    @commands.guild_only()
    async def info(self, ctx, *, info):
        """Changer votre infobox de profil."""
        user = ctx.author
        server = ctx.guild
        # creates user if doesn't exist
        await self._create_user(user, server)
        max_char = 150

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("Toutes les commandes pour le système de niveau sont désactivées !")
            return

        if len(info) < max_char:
            db.users.update_one({"user_id": str(user.id)}, {"$set": {"info": info}})
            await ctx.send("**Votre infobox a correctement été mise à jour.**")
        else:
            await ctx.send(
                "**Eh oh! Votre infobox comporte trop de caractères ! Le nombre maximum de caractères est de {} !**".format(max_char)
            )

    @levelupset.command(name="fond")
    @commands.guild_only()
    async def levelbg(self, ctx, *, image_name: str):
        """Changer le fond de votre image de lvl-up."""
        user = ctx.author
        server = ctx.guild
        backgrounds = await self.get_backgrounds()
        # creates user if doesn't exist
        await self._create_user(user, server)

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("Toutes les commandes pour le système de niveau sont désactivées !")
            return

        if await self.config.guild(ctx.guild).text_only():
            await ctx.send("**Red a dit qu'il n'autorisé que le texte.**")
            return

        if image_name in backgrounds["levelup"].keys():
            if await self._process_purchase(ctx):
                db.users.update_one(
                    {"user_id": str(user.id)},
                    {"$set": {"levelup_background": backgrounds["levelup"][image_name]}},
                )
                await ctx.send(
                    "**Le nouveau fond de l'image de lvl-up a correctement été appliqué !".format(
                        ctx.prefix
                    )
                )
        else:
            await ctx.send(
                f"Désolé ce n'est pas un background valide. Vous pouvez obtenir les fonds dispos en tapant : `{ctx.prefix}gestion btk fondslist levelup`"
            )

    @btk.command(name="buyfond")
    @commands.guild_only()
    async def buyanotherfond(self, ctx, *, image_name: str):
        """Acheter un fond."""
        user = ctx.author
        server = ctx.guild
        backgrounds = await self.get_backgrounds()
        # creates user if doesn't exist
        await self._create_user(user, server)

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("Toutes les commandes pour le système de niveau sont désactivées !")
            return

        if await self.config.guild(ctx.guild).text_only():
            await ctx.send("**Red a dit qu'il n'autorisé que le texte.**")
            return

        if image_name in backgrounds["profile"].keys():
            if await self._process_purchase(ctx):
                db.users.update_one(
                    {"user_id": str(user.id)},
                    {"$set": {"profile_background": backgrounds["profile"][image_name]}},
                )
                await ctx.send(
                    "**Le nouveau fond de votre carte de profil a correctement été appliqué !".format(
                        ctx.prefix
                    )
                )
        else:
            await ctx.send(
                f"Désolé ce n'est pas un background valide. Vous pouvez obtenir les bgs dispos en tapant : `{ctx.prefix}gestion btk fondslist profile`"
            )
    @profileset.command(name="fond")
    @commands.guild_only()
    async def profilebg(self, ctx, *, image_name: str):
        """Changer le fond de votre carte de profil."""
        user = ctx.author
        server = ctx.guild
        backgrounds = await self.get_backgrounds()
        # creates user if doesn't exist
        await self._create_user(user, server)

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("Toutes les commandes pour le système de niveau sont désactivées !")
            return

        if await self.config.guild(ctx.guild).text_only():
            await ctx.send("**Red a dit qu'il n'autorisé que le texte.**")
            return

        if image_name in backgrounds["profile"].keys():
            if await self._process_purchase(ctx):
                db.users.update_one(
                    {"user_id": str(user.id)},
                    {"$set": {"profile_background": backgrounds["profile"][image_name]}},
                )
                await ctx.send(
                    "**Le nouveau fond de votre carte de profil a correctement été appliqué !".format(
                        ctx.prefix
                    )
                )
        else:
            await ctx.send(
                f"Désolé ce n'est pas un background valide. Vous pouvez obtenir les bgs dispos en tapant : `{ctx.prefix}gestion btk fondslist profile`"
            )

    @rankset.command(name="fond")
    @commands.guild_only()
    async def rankbg(self, ctx, *, image_name: str):
        """Changer le fond de votre carte de rank."""
        user = ctx.author
        server = ctx.guild
        backgrounds = await self.get_backgrounds()
        # creates user if doesn't exist
        await self._create_user(user, server)

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("Toutes les commandes pour le système de niveau sont désactivées !")
            return

        if await self.config.guild(ctx.guild).text_only():
            await ctx.send("**Red a dit qu'il n'autorisé que le texte.**")
            return

        if image_name in backgrounds["rank"].keys():
            if await self._process_purchase(ctx):
                db.users.update_one(
                    {"user_id": str(user.id)},
                    {"$set": {"rank_background": backgrounds["rank"][image_name]}},
                )
                await ctx.send(
                    "**Le nouveau fond de votre carte de rank a correctement été appliqué !".format(
                        ctx.prefix
                    )
                )
        else:
            await ctx.send(
                f"Désolé ce n'est pas un background valide. Vous pouvez obtenir les bgs dispos en tapant : `{ctx.prefix}gestion btk fondslist rank`"
            )

    @profileset.command()
    @commands.guild_only()
    async def title(self, ctx, *, title):
        """Changer votre titre de profil."""
        user = ctx.author
        server = ctx.guild
        # creates user if doesn't exist
        await self._create_user(user, server)
        userinfo = db.users.find_one({"user_id": str(user.id)})
        max_char = 20

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("Toutes les commandes pour le système de niveau sont désactivées !")
            return

        if len(title) < max_char:
            userinfo["title"] = title
            db.users.update_one({"user_id": str(user.id)}, {"$set": {"title": title}})
            await ctx.send("**Votre titre de profil a correctement été mis à jour !**")
        else:
            await ctx.send("**Votre titre de profil a trop de caractères. Max: {}**".format(max_char))

    @checks.admin_or_permissions(manage_guild=True)
    @commands.group()
    @commands.guild_only()
    async def sysadm(self, ctx):
        """Paramètres d'administration."""
        pass

    @checks.admin_or_permissions(manage_guild=True)
    @sysadm.group(invoke_without_command=True)
    async def overview(self, ctx):
        """Une liste de paramètres cools."""
        if not ctx.message.channel.permissions_for(ctx.guild.me).embed_links:
            return await ctx.send(
                "**Je n'ai pas les permissions nécéssaires pour réaliser celà.**"
            )

        user = ctx.author
        disabled_servers = []
        private_levels = []
        disabled_levels = []
        locked_channels = []

        for guild in self.bot.guilds:
            if await self.config.guild(guild).disabled():
                disabled_servers.append(guild.name)
            if await self.config.guild(guild).lvl_msg_lock():
                locked_channels.append(
                    "\n{} → #{}".format(
                        guild.name,
                        guild.get_channel(await self.config.guild(guild).lvl_msg_lock()),
                    )
                )
            if await self.config.guild(guild).lvl_msg():
                disabled_levels.append(guild.name)
            if await self.config.guild(guild).private_lvl_message():
                private_levels.append(guild.name)

        num_users = len(list(db.users.find({})))

        default_profile = await self.config.default_profile()
        default_rank = await self.config.default_rank()
        default_levelup = await self.config.default_levelup()

        msg = ""
        msg += "**Serveurs:** {}\n".format(len(self.bot.guilds))
        msg += "**Utilisateurs spéciaux:** {}\n".format(num_users)
        msg += "**Mentions activées sur {}:** {}\n".format(
            ctx.guild.name, await self.config.guild(guild).mentions()
        )
        msg += "**Prix des fonds:** {}\n".format(await self.config.bg_price())
        msg += "**Prix de rénitialisation du délai de rep:** {}\n".format(await self.config.rep_price())
        msg += "**Type de badge:** {}\n".format(await self.config.badge_type())
        msg += "**Serveurs blacklist:** {}\n".format(", ".join(disabled_servers))
        msg += "**Message de montée de niveau:** {}\n".format(", ".join(disabled_levels))
        msg += "**Message de montée de niveau (mp)** {}\n".format(", ".join(private_levels))
        msg += "**Salons verouillés:** {}\n".format(", ".join(locked_channels))
        msg += "**Fond de la carte de profil par défaut:** {}\n".format(default_profile)
        msg += "**Fond de la carte de rank par défaut:** {}\n".format(default_rank)
        msg += "**Fond de l'image de lvl-up par défaut:** {}\n".format(default_levelup)
        em = discord.Embed(description=msg, colour=await ctx.embed_color())
        em.set_author(name="Aperçu des paramètres du serveur")
        await ctx.send(embed=em)

    @sysadm.command()
    @checks.is_owner()
    @commands.check(non_global_bank)
    @commands.guild_only()
    async def msgcredits(self, ctx, currency: int = 0):
        """Crédits par message. Par défaut = 0"""
        channel = ctx.channel
        server = ctx.guild

        if currency < 0 or currency > 1000:
            await ctx.send("**Merci d'entrer un numéro valide (0 - 1000)**".format(channel.name))
            return

        await self.config.guild(server).msg_credits.set(currency)
        await ctx.send("**Crédits par message appliqué à {} crédits.**".format(currency))

    @sysadm.command()
    @commands.guild_only()
    async def ignorechannel(self, ctx, channel: discord.TextChannel = None):
        """Bloque le gain XP dans un salon.

        Utiliser la commande sans mentionner de salon vous envoi la liste des salons ignorés."""
        server = ctx.guild
        if channel is None:
            channels = [
                server.get_channel(c) and server.get_channel(c).mention or c
                for c in await self.config.guild(server).ignored_channels()
                if server.get_channel(c)
            ]
            await ctx.send(
                "**Salons ignorés:** \n" + ("\n".join(channels) or "Aucun salon ignoré.")
            )
            return
        if channel.id in await self.config.guild(server).ignored_channels():
            async with self.config.guild(server).ignored_channels() as channels:
                channels.remove(channel.id)
            await ctx.send(f"**Les messages dans le salon {channel.mention} vont donner de l'XP maintenant !**")
        else:
            async with self.config.guild(server).ignored_channels() as channels:
                channels.append(channel.id)
            await ctx.send(f"**Les messages dans le salon {channel.mention} ne donneront plus d'XP !**")

    @sysadm.command(name="lock")
    @commands.guild_only()
    async def lvlmsglock(self, ctx, channel: discord.TextChannel = None):
        """Verouiller les annonces de niveaux dans un salon spécifique.
        Utiliser cette commande sans mentionner de salon désactive cette option."""
        server = ctx.guild

        if not channel:
            await self.config.guild(server).lvl_msg_lock.set(None)
            await ctx.send("**Les annonces de montées de niveaux ont été désactivées !**")
        else:
            await self.config.guild(server).lvl_msg_lock.set(channel.id)
            await ctx.send("**Les annonces de montées de niveaux ont été verouillées dans le salon `#{}`**".format(channel.name))
    async def _process_purchase(self, ctx):
        user = ctx.author
        server = ctx.guild
        bg_price = await self.config.bg_price()
        if bg_price != 0:
            if not await bank.can_spend(user, bg_price):
                await ctx.send(
                    "**Vous n'avez pas assez de crédits. Cette action coûte {} crédits.**".format(bg_price)
                )
                return False
            else:
                await ctx.send(
                    "**{}, vous êtes sur le point d'acheter un nouveau fond pour `{}` crédits. Confirmez en tapant `yes`, sinon tapez `no`.**".format(
                        await self._is_mention(user), bg_price
                    )
                )
                pred = MessagePredicate.yes_or_no(ctx)
                try:
                    await self.bot.wait_for("message", check=pred, timeout=15)
                except TimeoutError:
                    await ctx.send("**Action annulée.**")
                    return False
                if pred.result is True:
                    await bank.withdraw_credits(user, bg_price)
                    return True
                else:
                    await ctx.send("**Action annulée.**")
                    return False
        else:
            return True

    async def _give_chat_credit(self, user, server):
        msg_credits = await self.config.guild(server).msg_credits()
        if msg_credits and not await bank.is_global():
            await bank.deposit_credits(user, msg_credits)

    @checks.is_owner()
    @sysadm.command()
    @commands.guild_only()
    async def setbgprice(self, ctx, price: int):
        """Définir un prix pour changer de fond (profil/rank/lvl-up)."""
        if price < 0:
            await ctx.send("**Ce n'est pas un prix valide.**")
        else:
            await self.config.bg_price.set(price)
            await ctx.send(f"**Le prix de changement de fond a été appliqué à `{price}` crédits !**")

    @checks.is_owner()
    @sysadm.command()
    @commands.guild_only()
    async def setrepprice(self, ctx, price: int):
        """Définir un prix pour rénitialiser le délai d'attente de rep."""
        if price < 0:
            await ctx.send("**Ce n'est pas un prix valide.**")
        else:
            await self.config.rep_price.set(price)
            await ctx.send(f"**Le prix de rénitialisation du délai d'attente de rep a été appliqué à : `{price}` crédits !**")

    @checks.is_owner()
    @sysadm.command()
    @commands.guild_only()
    async def setlevel(self, ctx, user: discord.Member, level: int):
        """Définir le niveau d'un utilisateur spécifique."""
        server = user.guild
        channel = ctx.channel
        # creates user if doesn't exist
        await self._create_user(user, server)
        userinfo = db.users.find_one({"user_id": str(user.id)})

        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("Toutes les commandes pour le système de niveau sont désactivées !")
            return

        if level < 0:
            await ctx.send("**Merci d'entrer un nombre positif. Qui aime être dans le négatif sérieux ?**")
            return

        # get rid of old level exp
        old_server_exp = 0
        for i in range(userinfo["servers"][str(server.id)]["level"]):
            old_server_exp += self._required_exp(i)
        userinfo["total_exp"] -= old_server_exp
        userinfo["total_exp"] -= userinfo["servers"][str(server.id)]["current_exp"]

        # add in new exp
        total_exp = self._level_exp(level)
        userinfo["servers"][str(server.id)]["current_exp"] = 0
        userinfo["servers"][str(server.id)]["level"] = level
        userinfo["total_exp"] += total_exp

        db.users.update_one(
            {"user_id": str(user.id)},
            {
                "$set": {
                    "servers.{}.level".format(server.id): level,
                    "servers.{}.current_exp".format(server.id): 0,
                    "total_exp": userinfo["total_exp"],
                }
            },
        )
        await ctx.send(
            "**Le niveau de {} a été mit à `{}`. Chanceux(se) !**".format(await self._is_mention(user), level)
        )
        await self._handle_levelup(user, userinfo, server, channel)

    @checks.is_owner()
    @sysadm.command()
    @commands.guild_only()
    async def mention(self, ctx):
        """Activer les mentions sur les messages."""
        if await self.config.guild(ctx.guild).mentions():
            await self.config.guild(ctx.guild).mentions.set(False)
            await ctx.send("**Mentions désactivées.**")
        else:
            await self.config.guild(ctx.guild).mentions.set(True)
            await ctx.send("**Mentions activées.**")

    async def _valid_image_url(self, url):
        try:
            async with self.session.get(url) as r:
                image = await r.content.read()
            with open(f"{cog_data_path(self)}/test.png", "wb") as f:
                f.write(image)
            image = Image.open(f"{cog_data_path(self)}/test.png").convert("RGBA")
            os.remove(f"{cog_data_path(self)}/test.png")
            return True
        except:
            return False

    @checks.admin_or_permissions(manage_guild=True)
    @sysadm.command()
    @commands.guild_only()
    async def toggle(self, ctx):
        """Activer la plupart des modules du système de niveau."""
        server = ctx.guild
        if await self.config.guild(server).disabled():
            await self.config.guild(server).disabled.set(False)
            await ctx.send("**Les modules du système de niveau ont été activés sur le serveur {} !**".format(server.name))
        else:
            await self.config.guild(server).disabled.set(True)
            await ctx.send("**Les modules du système de niveau ont été désactivés sur le serveur {} !**".format(server.name))

    @checks.admin_or_permissions(manage_guild=True)
    @sysadm.command()
    @commands.guild_only()
    async def textonly(self, ctx):
        """Autoriser seulement les messages 'textes' pour le gain d'XP."""
        server = ctx.guild
        if await self.config.guild(server).text_only():
            await self.config.guild(server).text_only.set(False)
            await ctx.send("**Tous les messages rapporteront de l'XP dans le serveur {}.**".format(server.name))
        else:
            await self.config.guild(server).text_only.set(True)
            await ctx.send("**Seul les messages 'textes' rapporteront de l'XP dans le serveur {}.**".format(server.name))

    @checks.admin_or_permissions(manage_guild=True)
    @sysadm.command(name="alerts")
    @commands.guild_only()
    async def lvlalert(self, ctx):
        """Activer les annonces de niveau."""
        server = ctx.guild
        user = ctx.author

        if await self.config.guild(server).lvl_msg():
            await self.config.guild(server).lvl_msg.set(False)
            await ctx.send("**Annonces de niveau désactivé sur le serveur {}.**".format(server.name))
        else:
            await self.config.guild(server).lvl_msg.set(True)
            await ctx.send("**Annonces de niveau activé sur le serveur {}.**".format(server.name))

    @checks.admin_or_permissions(manage_guild=True)
    @sysadm.command(name="private")
    @commands.guild_only()
    async def lvlprivate(self, ctx):
        """Activer l'envoi de message privé lorsqu'un utilisateur monte de niveau."""
        server = ctx.guild
        if await self.config.guild(server).private_lvl_message():
            await self.config.guild(server).private_lvl_message.set(False)
            await ctx.send("**Alerte de montée de niveau en message privé désactivée sur le serveur {}.**".format(server.name))
        else:
            await self.config.guild(server).private_lvl_message.set(True)
            await ctx.send("**Alerte de montée de niveau en message privé activée sur le serveur {}.**".format(server.name))

    @sysadm.command()
    @checks.is_owner()
    async def xp(self, ctx, min_xp: int = None, max_xp: int = None):
        """Définir les gains d'XP entre deux valeurs.
        Ne pas préciser de valeurs remet les valeurs par défaut (15-20xp)."""
        if not (min_xp and max_xp):
            await self.config.xp.set([15, 20])
            return await ctx.send(
                "Les gains d'XP ont été remit par défaut (15-20)."
            )
        elif not max_xp:
            return await ctx.send(f"Entrez deux valeurs pour les gains d'XP `{ctx.prefix}sysadm xp 15 20`")
        elif (max_xp or min_xp) > 1000:
            return await ctx.send(
                "C'est trop. Essayez quelque chose en dessous de 1000."
            )
        elif min_xp >= max_xp:
            return await ctx.send(
                "La valeur du gain d'XP minimale doit être inférieure à la valeur maximale."
            )
        elif (min_xp or max_xp) <= 0:
            return await ctx.send("Les gains d'XP ne peuvent pas être =< 0.")
        else:
            await self.config.xp.set([min_xp, max_xp])
            await ctx.send(
                f"Les gains d'XP seront compris entre {min_xp} et {max_xp} maintenant !."
            )
    @sysadm.command()
    @checks.is_owner()
    async def length(self, ctx, message_length: int = 10):
        """Définir la longueur minimale du message pour les gains d'xp.

        PS: Les images, fichiers ne comptent pas."""
        if message_length < 0:
            raise commands.BadArgument
        await self.config.message_length.set(message_length)
        await ctx.tick()
    @sysadm.group()
    async def badge(self, ctx):
        """Options de configurations de badge."""
        pass

    @btk.command(name="badgeslist")
    @commands.guild_only()
    async def badgeslist(self, ctx):
        """Liste les badges disponibles du serveur."""
        if not ctx.message.channel.permissions_for(ctx.guild.me).embed_links:
            return await ctx.send(
                "**Je n'ai pas les permissions nécéssaires pour réaliser celà.**"
            )

        user = ctx.author
        server = ctx.guild

        # get server stuff
        ids = [
            ("global", "Global", self.bot.user.avatar_url),
            (server.id, server.name, server.icon_url),
        ]

        title_text = "**Badges disponibles**"
        index = 0
        for serverid, servername, icon_url in ids:
            em = discord.Embed(colour=await ctx.embed_color())
            em.set_author(name="{}".format(servername), icon_url=icon_url)
            msg = ""
            server_badge_info = db.badges.find_one({"server_id": str(serverid)})
            if server_badge_info:
                server_badges = server_badge_info["badges"]
                if len(server_badges) >= 1:
                    for badgename in server_badges:
                        badgeinfo = server_badges[badgename]
                        if badgeinfo["price"] == -1:
                            price = "EXCLUSIF"
                        elif badgeinfo["price"] == 0:
                            price = "GRATUIT"
                        else:
                            price = badgeinfo["price"]

                        msg += "**• {}** ({}) - {}\n".format(
                            badgename, price, badgeinfo["description"]
                        )
                else:
                    msg = "-"
            else:
                msg = "-"

            em.description = msg

            total_pages = len(list(pagify(msg)))

            counter = 1
            for page in pagify(msg, ["\n"]):
                if index == 0:
                    await ctx.send(title_text, embed=em)
                else:
                    await ctx.send(embed=em)
                index += 1

                em.set_footer(text="Page {} sur {}".format(counter, total_pages))
                counter += 1

    @changebadge0.command(name="clc")
    @commands.guild_only()
    async def listuserbadges(self, ctx, user: discord.Member = None):
        """Liste la collection de badges d'un utilisateur."""
        if not ctx.message.channel.permissions_for(ctx.guild.me).embed_links:
            return await ctx.send(
                "**Je n'ai pas les permissions nécéssaires pour réaliser celà.**"
            )

        if user is None:
            user = ctx.author
        server = ctx.guild
        await self._create_user(user, server)
        userinfo = db.users.find_one({"user_id": str(user.id)})
        userinfo = self._badge_convert_dict(userinfo)

        # sort
        priority_badges = []
        for badgename in userinfo["badges"].keys():
            badge = userinfo["badges"][badgename]
            priority_num = badge["priority_num"]
            if priority_num != -1:
                priority_badges.append((badge, priority_num))
        sorted_badges = sorted(priority_badges, key=operator.itemgetter(1), reverse=True)

        badge_ranks = ""
        counter = 1
        for badge, priority_num in sorted_badges[:12]:
            badge_ranks += "**{}. {}** ({}) [{}] **—** {}\n".format(
                counter,
                badge["badge_name"],
                badge["server_name"],
                priority_num,
                badge["description"],
            )
            counter += 1
        if not badge_ranks:
            badge_ranks = "-"

        em = discord.Embed(colour=user.colour)

        total_pages = len(list(pagify(badge_ranks)))
        embeds = []

        counter = 1
        for page in pagify(badge_ranks, ["\n"]):
            em.description = page
            em.set_author(name="Badges de {}".format(user.name), icon_url=user.avatar_url)
            em.set_footer(text="Page {} sur {}".format(counter, total_pages))
            embeds.append(em)
            counter += 1
        await menu(ctx, embeds, DEFAULT_CONTROLS)

    @btk.command(name="buybadge")
    @commands.guild_only()
    async def buy(self, ctx, name: str, global_badge: str = None):
        """Acheter un badge."""
        user = ctx.author
        server = ctx.guild
        if global_badge == "-global":
            serverid = "global"
        else:
            serverid = server.id
        await self._create_user(user, server)
        userinfo = db.users.find_one({"user_id": str(user.id)})
        userinfo = self._badge_convert_dict(userinfo)
        server_badge_info = db.badges.find_one({"server_id": str(serverid)})

        if server_badge_info:
            server_badges = server_badge_info["badges"]
            if name in server_badges:

                if "{}_{}".format(name, str(serverid)) not in userinfo["badges"].keys():
                    badge_info = server_badges[name]
                    if badge_info["price"] == -1:
                        await ctx.send("**Désolé, ce badge ne peut pas être acheté.**".format(name))
                    elif badge_info["price"] == 0:
                        userinfo["badges"]["{}_{}".format(name, str(serverid))] = server_badges[
                            name
                        ]
                        db.users.update_one(
                            {"user_id": userinfo["user_id"]},
                            {"$set": {"badges": userinfo["badges"]}},
                        )
                        await ctx.send("**Le badge {} a été obtenu.**".format(name))
                    else:
                        await ctx.send(
                            "**{}, vous êtes sur le point d'acheter le badge `{}` pour `{}` crédits. Confirmez votre choix en tapant `yes` sinon tapez `no`.**".format(
                                await self._is_mention(user), name, badge_info["price"]
                            )
                        )
                        pred = MessagePredicate.yes_or_no(ctx)
                        try:
                            await self.bot.wait_for("message", check=pred, timeout=15)
                        except TimeoutError:
                            return await ctx.send("**Action annulée.**")
                        if pred.result is False:
                            await ctx.send("**Action annulée.**")
                            return
                        else:
                            if badge_info["price"] <= await bank.get_balance(user):
                                await bank.withdraw_credits(user, badge_info["price"])
                                userinfo["badges"][
                                    "{}_{}".format(name, str(serverid))
                                ] = server_badges[name]
                                db.users.update_one(
                                    {"user_id": userinfo["user_id"]},
                                    {"$set": {"badges": userinfo["badges"]}},
                                )
                                await ctx.send(
                                    "**Vous avez acheté le badge `{}` pour `{}` crédits.**".format(
                                        name, badge_info["price"]
                                    )
                                )
                            elif await bank.get_balance(user) < badge_info["price"]:
                                await ctx.send(
                                    "**Pas assez de crédits désolé ! ewe Il vous manque `{}`.**".format(
                                        badge_info["price"] - await bank.get_balance(user)
                                    )
                                )
                else:
                    await ctx.send("**{}, vous avez déjà ce badge !**".format(user.name))
            else:
                await ctx.send(
                    "**Le badge `{}` n'existe pas. Obtenez la liste des badges dispos en tapant `{}gestion btk badgeslist`)**".format(
                        name, ctx.prefix
                    )
                )
        else:
            await ctx.send(
                "**Aucun badge n'est disponible. Wouah, où suis-je tombé ?!**".format(
                    ctx.prefix
                )
            )

    @checks.mod_or_permissions(manage_roles=True)
    @badge.command(name="add")
    @commands.guild_only()
    async def addbadge(self, ctx, name: str, bg_img: str, border_color: str, price: int, *, description: str):
        """Ajouter un badge. 
        Nom = "NOMDUBADGE", Colors = #hex. bg_img = url, prix = -1(exclusif-non-achetable), 0(gratuit)."""
        user = ctx.author
        server = ctx.guild
        # check members
        required_members = 35
        members = len([member for member in server.members if not member.bot])

        if user.id == self.bot.owner_id:
            pass
        elif members < required_members:
            await ctx.send(
                "**Woups. Vous pouvez ajouter des badges à partir de {} membres !**".format(
                    required_members
                )
            )
            return

        if "-global" in description and user.id == self.bot.owner_id:
            description = description.replace("-global", "")
            serverid = "global"
            servername = "global"
        else:
            serverid = server.id
            servername = server.name

        if "." in name:
            await ctx.send("**Le nom ne peut pas contenir de `.`**")
            return

        if not await self._valid_image_url(bg_img):
            await ctx.send("**Le fond n'est pas valide. Tapez un code #hex ou une url d'image.**")
            return

        if not self._is_hex(border_color):
            await ctx.send("**La couleur du contour n'est pas valide.**")
            return

        if price < -1:
            await ctx.send("**Le prix n'est pas valide.**")
            return

        if len(description.split(" ")) > 40:
            await ctx.send("**La description est trop longue ! Le maximum de caractères est de 40.**")
            return

        badges = db.badges.find_one({"server_id": str(serverid)})
        if not badges:
            db.badges.insert_one({"server_id": str(serverid), "badges": {}})
            badges = db.badges.find_one({"server_id": str(serverid)})

        new_badge = {
            "badge_name": name,
            "bg_img": bg_img,
            "price": price,
            "description": description,
            "border_color": border_color,
            "server_id": str(serverid),
            "server_name": servername,
            "priority_num": 0,
        }

        if name not in badges["badges"].keys():
            # create the badge regardless
            badges["badges"][name] = new_badge
            db.badges.update_one(
                {"server_id": str(serverid)}, {"$set": {"badges": badges["badges"]}}
            )
            await ctx.send("**`{}` badge added in `{}` server.**".format(name, servername))
        else:
            # update badge in the server
            badges["badges"][name] = new_badge
            db.badges.update_one({"server_id": serverid}, {"$set": {"badges": badges["badges"]}})

            # go though all users and update the badge.
            # Doing it this way because dynamic does more accesses when doing profile
            for user in db.users.find({}):
                try:
                    user = self._badge_convert_dict(user)
                    userbadges = user["badges"]
                    badge_name = "{}_{}".format(name, serverid)
                    if badge_name in userbadges.keys():
                        user_priority_num = userbadges[badge_name]["priority_num"]
                        new_badge[
                            "priority_num"
                        ] = user_priority_num  # maintain old priority number set by user
                        userbadges[badge_name] = new_badge
                        db.users.update_one(
                            {"user_id": user["user_id"]}, {"$set": {"badges": userbadges}}
                        )
                except:
                    pass
            await ctx.send("**Le badge `{}` a été mit à jour !**".format(name))

    @checks.is_owner()
    @badge.command()
    @commands.guild_only()
    async def type(self, ctx, name: str):
        """Définir la forme des badges (circles ou bars)."""
        valid_types = ["circles", "bars"]
        if name.lower() not in valid_types:
            await ctx.send("**Ce n'est pas un type de badge valide.**")
            return

        await self.config.badge_type.set(name.lower())
        await ctx.send("**Le type de badge a été définit en `{}`.**".format(name.lower()))

    def _is_hex(self, color: str):
        if color is not None and len(color) != 4 and len(color) != 7:
            return False

        reg_ex = r"^#(?:[0-9a-fA-F]{3}){1,2}$"
        return re.search(reg_ex, str(color))

    @checks.mod_or_permissions(manage_roles=True)
    @badge.command(name="delete")
    @commands.guild_only()
    async def delbadge(self, ctx, *, name: str):
        """Supprimer un badge du serveur."""
        user = ctx.author
        server = ctx.guild

        if "-global" in name and user.id == self.bot.owner_id:
            name = name.replace(" -global", "")
            serverid = "global"
        else:
            serverid = server.id

        # creates user if doesn't exist
        await self._create_user(user, server)

        if await self.config.guild(server).disabled():
            await ctx.send("Toutes les commandes pour le système de niveau sont désactivées !")
            return

        serverbadges = db.badges.find_one({"server_id": str(serverid)})
        if name in serverbadges["badges"].keys():
            del serverbadges["badges"][name]
            db.badges.update_one(
                {"server_id": serverbadges["server_id"]},
                {"$set": {"badges": serverbadges["badges"]}},
            )

            await ctx.send("**The `{}` badge has been removed.**".format(name))
        else:
            await ctx.send("**Ce badge n'existe pas !**")

    @checks.mod_or_permissions(manage_roles=True)
    @badge.command()
    @commands.guild_only()
    async def give(self, ctx, user: discord.Member, name: str, global_badge: str = None):
        """Donner un badge à un utilisateur."""
        org_user = ctx.message.author
        server = ctx.guild

        # creates user if doesn't exist
        await self._create_user(user, server)
        userinfo = db.users.find_one({"user_id": str(user.id)})
        userinfo = self._badge_convert_dict(userinfo)

        if await self.config.guild(server).disabled():
            await ctx.send("Toutes les commandes pour le système de niveau sont désactivées !")
            return
        if user.bot:
            await ctx.send("**Un robot ne peut pas avoir de badge voyons !**")
            return
        if global_badge == "-global":
            badgeserver = "global"
        else:
            badgeserver = ctx.guild.id
        serverbadges = db.badges.find_one({"server_id": str(badgeserver)})
        if serverbadges:
            badges = serverbadges["badges"]
        else:
            badges = None
        badge_name = "{}_{}".format(name, server.id)

        if not badges:
            await ctx.send("**Ce badge n'existe pas dans ce serveur !**")
            return
        elif badge_name in badges.keys():
            await ctx.send("**{} a déjà ce badge !**".format(await self._is_mention(user)))
            return
        else:
            try:
                userinfo["badges"][badge_name] = badges[name]
                db.users.update_one(
                    {"user_id": str(user.id)}, {"$set": {"badges": userinfo["badges"]}}
                )
                await ctx.send(
                    "**{} has just given {} the `{}` badge!**".format(
                        await self._is_mention(org_user), await self._is_mention(user), name
                    )
                )
            except KeyError:
                await ctx.send("**Ce badge n'existe pas dans ce serveur !**")

    @checks.mod_or_permissions(manage_roles=True)
    @badge.command()
    @commands.guild_only()
    async def take(self, ctx, user: discord.Member, name: str):
        """Supprimer un badge à un utilisateur."""
        org_user = ctx.author
        server = ctx.guild
        # creates user if doesn't exist
        await self._create_user(user, server)
        userinfo = db.users.find_one({"user_id": str(user.id)})
        userinfo = self._badge_convert_dict(userinfo)

        if await self.config.guild(server).disabled():
            await ctx.send("Toutes les commandes pour le système de niveau sont désactivées !")
            return
        if user.bot:
            return
        serverbadges = db.badges.find_one({"server_id": str(server.id)})
        badges = serverbadges["badges"]
        badge_name = "{}_{}".format(name, server.id)

        if name not in badges:
            await ctx.send("**Ce badge n'existe pas dans ce serveur !**")
        elif badge_name not in userinfo["badges"]:
            await ctx.send("**{} n'a pas ce badge !**".format(await self._is_mention(user)))
        else:
            del userinfo["badges"][badge_name]
            db.users.update_one(
                {"user_id": str(user.id)}, {"$set": {"badges": userinfo["badges"]}}
            )
            await ctx.send(
                "**{} a retiré le badge `{}` à {}.**".format(
                    await self._is_mention(org_user), name, await self._is_mention(user)
                )
            )

    @checks.mod_or_permissions(manage_roles=True)
    @badge.command(name="link")
    @commands.guild_only()
    async def linkbadge(self, ctx, badge_name: str, level: int):
        """Associer un badge avec un niveau."""
        server = ctx.guild
        serverbadges = db.badges.find_one({"server_id": str(server.id)})

        if serverbadges is None:
            await ctx.send("**Ce serveur n'a aucun badge.**")
            return

        if badge_name not in serverbadges["badges"].keys():
            await ctx.send("**Assurez-vous que le badge `{}` existe !**".format(badge_name))
            return
        else:
            server_linked_badges = db.badgelinks.find_one({"server_id": str(server.id)})
            if not server_linked_badges:
                new_server = {"server_id": str(server.id), "badges": {badge_name: str(level)}}
                db.badgelinks.insert_one(new_server)
            else:
                server_linked_badges["badges"][badge_name] = str(level)
                db.badgelinks.update_one(
                    {"server_id": str(server.id)},
                    {"$set": {"badges": server_linked_badges["badges"]}},
                )
            await ctx.send(
                "**Le badge `{}` a été lié au niveau `{}`.**".format(badge_name, level)
            )

    @checks.admin_or_permissions(manage_roles=True)
    @badge.command(name="unlink")
    @commands.guild_only()
    async def unlinkbadge(self, ctx, *, badge_name: str):
        """Délier un badge d'un niveau."""
        server = ctx.guild

        server_linked_badges = db.badgelinks.find_one({"server_id": str(server.id)})
        badge_links = server_linked_badges["badges"]

        if badge_name in badge_links.keys():
            await ctx.send(
                "**L'association Badge/Niveau `{}`/`{}` a été supprimée.**".format(
                    badge_name, badge_links[badge_name]
                )
            )
            del badge_links[badge_name]
            db.badgelinks.update_one(
                {"server_id": str(server.id)}, {"$set": {"badges": badge_links}}
            )
        else:
            await ctx.send("**Le badge `{}` n'est lié à aucun niveau !**".format(badge_name))

    @checks.mod_or_permissions(manage_roles=True)
    @badge.command(name="listlinks")
    @commands.guild_only()
    async def listbadge(self, ctx):
        """Liste les associations actuelles de badges/niveaux."""
        if not ctx.message.channel.permissions_for(ctx.guild.me).embed_links:
            return await ctx.send(
                "**Je n'ai pas les permissions nécéssaires pour réaliser celà.**"
            )

        server = ctx.guild

        server_badges = db.badgelinks.find_one({"server_id": str(server.id)})

        em = discord.Embed(colour=await ctx.embed_color())
        em.set_author(
            name="Badges-niveaux actuellement liés sur le serveur", icon_url=server.icon_url
        )

        if server_badges is None or "badges" not in server_badges or server_badges["badges"] == {}:
            msg = "-"
        else:
            badges = server_badges["badges"]
            msg = "**Badge** → Niveau\n"
            for badge in badges.keys():
                msg += "**• {} →** {}\n".format(badge, badges[badge])

        em.description = msg
        await ctx.send(embed=em)

    @checks.mod_or_permissions(manage_roles=True)
    @sysadm.group()
    async def role(self, ctx):
        """Options de configuration des rôles niveaux."""
        pass

    @role.command(name="link")
    @commands.guild_only()
    async def linkrole(self, ctx, role_name: str, level: int, remove_role=None):
        """Associer un rôle avec un niveau.\nPossibilité de retirer un rôle après en avoir gagner un."""
        server = ctx.guild

        role_obj = discord.utils.find(lambda r: r.name == role_name, server.roles)
        remove_role_obj = discord.utils.find(lambda r: r.name == remove_role, server.roles)
        if role_obj is None or (remove_role is not None and remove_role_obj is None):
            if remove_role is None:
                await ctx.send("**Assurez-vous que le rôle `{}` existe !**".format(role_name))
            else:
                await ctx.send(
                    "*Assurez-vous que le rôle `{}` et/ou le rôle `{}` existent !**".format(
                        role_name, remove_role
                    )
                )
        else:
            server_roles = db.roles.find_one({"server_id": str(server.id)})
            if not server_roles:
                new_server = {
                    "server_id": str(server.id),
                    "roles": {role_name: {"level": str(level), "remove_role": remove_role}},
                }
                db.roles.insert_one(new_server)
            else:
                if role_name not in server_roles["roles"]:
                    server_roles["roles"][role_name] = {}

                server_roles["roles"][role_name]["level"] = str(level)
                server_roles["roles"][role_name]["remove_role"] = remove_role
                db.roles.update_one(
                    {"server_id": str(server.id)}, {"$set": {"roles": server_roles["roles"]}}
                )

            if remove_role is None:
                await ctx.send(
                    "**Le rôle `{}` a été lié au niveau `{}`.**".format(role_name, level)
                )
            else:
                await ctx.send(
                    "**Le rôle `{}` a été lié au niveau `{}`. "
                    "Red retirera aussi le rôle `{}` lorsque l'utilisateur arrivera au niveau {} !**".format(role_name, level, remove_role, level)
                )

    @role.command(name="unlink")
    @commands.guild_only()
    async def unlinkrole(self, ctx, *, role_name: str):
        """Délier un rôle d'un niveau."""
        server = ctx.guild

        server_roles = db.roles.find_one({"server_id": str(server.id)})
        roles = server_roles["roles"]

        if role_name in roles:
            await ctx.send(
                "**L'association Rôle/Niveau `{}`/`{}` a été supprimée.**".format(
                    role_name, roles[role_name]["level"]
                )
            )
            del roles[role_name]
            db.roles.update_one({"server_id": str(server.id)}, {"$set": {"roles": roles}})
        else:
            await ctx.send("**Le rôle `{}` n'est lié à aucun niveau !**".format(role_name))

    @role.command(name="listlinks")
    @commands.guild_only()
    async def listrole(self, ctx):
        """Liste les associations actuelles de rôles/niveaux."""
        if not ctx.message.channel.permissions_for(ctx.guild.me).embed_links:
            return await ctx.send(
                "**Je n'ai pas les permissions nécéssaires pour réaliser celà.**"
            )

        server = ctx.guild
        user = ctx.author

        server_roles = db.roles.find_one({"server_id": str(server.id)})

        em = discord.Embed(colour=await ctx.embed_color())
        em.set_author(
            name="Rôles-niveaux actuellement liés sur le serveur", icon_url=server.icon_url
        )

        if server_roles is None or "roles" not in server_roles or server_roles["roles"] == {}:
            msg = "None"
        else:
            roles = server_roles["roles"]
            msg = "**Rôle** → Niveau\n"
            for role in roles:
                if roles[role]["remove_role"] is not None:
                    msg += "**• {} →** {} (Supprime le rôle: {})\n".format(
                        role, roles[role]["level"], roles[role]["remove_role"]
                    )
                else:
                    msg += "**• {} →** {}\n".format(role, roles[role]["level"])

        em.description = msg
        await ctx.send(embed=em)

    @sysadm.group(name="bg")
    async def sysadmbg(self, ctx):
        """Options de configuration des fonds. """
        pass

    @checks.is_owner()
    @sysadmbg.command()
    @commands.guild_only()
    async def addprofilebg(self, ctx, name: str, url: str):
        """Ajouter un nouveau fond de profil (290px x 290px)."""
        backgrounds = await self.get_backgrounds()
        if name in backgrounds["profile"].keys():
            await ctx.send("**Ce fond de profil existe déjà !**")
        elif not await self._valid_image_url(url):
            await ctx.send("**Ce n'est pas une url valide d'image.**")
        else:
            async with self.config.backgrounds() as backgrounds:
                backgrounds["profile"][name] = url
            await ctx.send("**Fond de profil `{}` ajouté à la collection.**".format(name))

    @checks.is_owner()
    @sysadmbg.command()
    @commands.guild_only()
    async def addrankbg(self, ctx, name: str, url: str):
        """Ajouter un nouveau fond de rank (360px x 100px)"""
        backgrounds = await self.get_backgrounds()
        if name in backgrounds["profile"].keys():
            await ctx.send("**Ce fond de rank existe déjà !**")
        elif not await self._valid_image_url(url):
            await ctx.send("**Ce n'est pas une url valide d'image.**")
        else:
            async with self.config.backgrounds() as backgrounds:
                backgrounds["rank"][name] = url
            await ctx.send("**Fond de rank `{}` ajouté à la collection.**".format(name))

    @checks.is_owner()
    @sysadmbg.command()
    @commands.guild_only()
    async def addlevelbg(self, ctx, name: str, url: str):
        """Ajouter un nouveau fond de lvl-up. (85px x 105px)"""
        backgrounds = await self.get_backgrounds()
        if name in backgrounds["levelup"].keys():
            await ctx.send("**Ce fond de lvl-up existe déjà !**")
        elif not await self._valid_image_url(url):
            await ctx.send("**Ce n'est pas une url valide d'image.**")
        else:
            async with self.config.backgrounds() as backgrounds:
                backgrounds["levelup"][name] = url
            await ctx.send("**Fond de lvl-up `{}` ajouté à la collection.**".format(name))
    @checks.is_owner()
    @sysadm.command()
    @commands.guild_only()
    async def xpban(self, ctx, days: int, *, user: Union[discord.Member, int, None]):
        """Restreindre les gains d'XP à un utilisateur."""
        if isinstance(user, int):
            try:
                user = await self.bot.fetch_user(user)
            except (discord.HTTPException, discord.NotFound):
                user = None
        if user is None:
            await ctx.send_help()
            return
        chat_block = time.time() + timedelta(days=days).total_seconds()
        try:
            db.users.update_one(
                {"user_id": str(user.id)}, {"$set": {"chat_block": chat_block}}
            )
        except Exception as exc:
            await ctx.send("Unable to add chat block: {}".format(exc))
        else:
            await ctx.tick()
    @checks.is_owner()
    @sysadmbg.command()
    @commands.guild_only()
    async def setcustombg(self, ctx, fonds_type: str, user_id: str, img_url: str):
        """Appliquer un fond perso de n'importe quel type à un utilisateur."""
        valid_types = ["profile", "rank", "levelup"]
        type_input = fonds_type.lower()

        if type_input not in valid_types:
            await ctx.send("**Merci de choisir un bon type de fond. Type de fond dispos: `profile`, `rank`, `levelup`.")
            return

        # test if valid user_id
        userinfo = db.users.find_one({"user_id": str(user_id)})
        if not userinfo:
            await ctx.send("**Ce n'est pas un ID d'utilisateur valide.**")
            return
        if user.bot:
            await ctx.send("**Un robot ne peut pas avoir de profil voyons !**")
            return
        if not await self._valid_image_url(img_url):
            await ctx.send("**Ce n'est pas une url d'image valide.**")
            return

        db.users.update_one(
            {"user_id": str(user_id)}, {"$set": {"{}_background".format(type_input): img_url}}
        )
        await ctx.send("**Un fond perso de {} a été appliqué à l'utilisateur {}.**".format(fonds_type, user_id))

    @checks.is_owner()
    @sysadmbg.command()
    @commands.guild_only()
    async def defaultprofilebg(self, ctx, name: str):
        """Appliquer un fond de profil par défaut."""
        bgs = await self.get_backgrounds()
        if name in bgs["profile"].keys():
            await self.config.default_profile.set(bgs["profile"][name])
            return await ctx.send(
                "**Le fond de profil `{}` sera appliqué par défaut sur les nouveaux membres !**".format(name)
            )
        else:
            return await ctx.send("**Ce fond n'existe pas.**")

    @checks.is_owner()
    @sysadmbg.command()
    @commands.guild_only()
    async def defaultrankbg(self, ctx, name: str):
        """Appliquer un fond de rank par défaut."""
        bgs = await self.get_backgrounds()
        if name in bgs["rank"].keys():
            await self.config.default_rank.set(bgs["rank"][name])
            return await ctx.send(
                "**Le fond de rank `{}` sera appliqué par défaut sur les nouveaux membres !**".format(name)
            )
        else:
            return await ctx.send("**Ce fond n'existe pas.**")

    @checks.is_owner()
    @sysadmbg.command()
    @commands.guild_only()
    async def defaultlevelbg(self, ctx, name: str):
        """Appliquer un fond de lvl-up par défaut."""
        bgs = await self.get_backgrounds()
        if name in bgs["levelup"].keys():
            await self.config.default_levelup.set(bgs["levelup"][name])
            return await ctx.send(
                "**Le fond de lvl-up `{}` sera appliqué par défaut sur les nouveaux membres !**".format(name)
            )
        else:
            return await ctx.send("**Ce fond n'existe pas.**")

    @checks.is_owner()
    @sysadmbg.command()
    @commands.guild_only()
    async def delprofilebg(self, ctx, name: str):
        """Supprimer un fond de profil."""
        backgrounds = await self.get_backgrounds()
        if len(backgrounds["profile"]) == 1:
            return await ctx.send(
                "**Merci d'ajouter plus de fonds de profil avec la commande** `{}sysadm bg addprofilbg` ** avant de supprimer le dernier qu'il vous reste !**".format(
                    ctx.prefix
                )
            )
        default_profile = await self.config.default_profile()
        try:
            if backgrounds["profile"][name] == default_profile:
                msg = (
                   "**Ce fond de profil est actuellement celui par défaut.**\n"
                    "Utilisez avant `{}sysadm bg defaultprofilebg` pour définir un autre fond par défaut.\n"
                    "Après celà, réessayer `{}sysadm bg delprofilebg {}` pour établir la procédure\n"
                    "de suppresion du fond de profil `{}`."
                ).format(ctx.prefix, ctx.prefix, name, name)
                return await ctx.send(msg)
            else:
                await self.delete_background("profile", name)
        except KeyError:
            return await ctx.send("**Vous ne pouvez pas supprimer ce fond car il n'existe pas.**")
        else:
            return await ctx.send(
                "**Le fond de profil `{}` a été supprimé.**".format(name)
            )

    @checks.is_owner()
    @sysadmbg.command()
    @commands.guild_only()
    async def delrankbg(self, ctx, name: str):
        """Supprimer un fond de rank."""
        backgrounds = await self.get_backgrounds()
        if len(backgrounds["rank"]) == 1:
            return await ctx.send(
                "**Merci d'ajouter plus de fonds de rank avec la commande** `{}sysadm bg addrankbg` ** avant de supprimer le dernier qu'il vous reste !**".format(
                    ctx.prefix
                )
            )
        default_rank = await self.config.default_rank()
        try:
            if backgrounds["rank"][name] == default_rank:
                msg = (
                  "**Ce fond de rank est actuellement celui par défaut.**\n"
                    "Utilisez avant `{}sysadm bg defaultrankbg` pour définir un autre fond par défaut.\n"
                    "Après celà, réessayer `{}sysadm bg delrankbg {}` pour établir la procédure\n"
                    "de suppresion du fond de rank `{}`."
                ).format(ctx.prefix, ctx.prefix, name, name)
                return await ctx.send(msg)
            else:
                await self.delete_background("rank", name)
        except KeyError:
            return await ctx.send("**Vous ne pouvez pas supprimer ce fond car il n'existe pas.**")
        else:
            return await ctx.send(
                "**Le fond de rank `{}` a été supprimé.**".format(name)
            )

    @checks.is_owner()
    @sysadmbg.command()
    @commands.guild_only()
    async def dellevelbg(self, ctx, name: str):
        """Supprimer un fond de lvl-up."""
        backgrounds = await self.get_backgrounds()
        if len(backgrounds["levelup"]) == 1:
            return await ctx.send(
                "**Merci d'ajouter plus de fonds de lvl-up avec la commande** `{}sysadm bg addlevelbg` ** avant de supprimer le dernier qu'il vous reste !**".format(
                    ctx.prefix
                )
            )
        default_levelup = await self.config.default_levelup()
        try:
            if backgrounds["levelup"][name] == default_levelup:
                msg = (
                    "**Ce fond de lvl-up est actuellement celui par défaut.**\n"
                    "Utilisez avant `{}sysadm bg defaultlevelbg` pour définir un autre fond par défaut.\n"
                    "Après celà, réessayer `{}sysadm bg dellevelbg {}` pour établir la procédure\n"
                    "de suppresion du fond de lvl-up `{}`."
                ).format(ctx.prefix, ctx.prefix, name, name)
                return await ctx.send(msg)
            else:
                await self.delete_background("levelup", name)
        except KeyError:
            return await ctx.send("**Vous ne pouvez pas supprimer ce fond car il n'existe pas.**")
        else:
            return await ctx.send(
                "**Le fond de lvl-up `{}` a été supprimé.**".format(name)
            )
    @checks.is_owner()
    @sysadm.command(pass_context=True, no_pm=True)	
    async def removeldbuser (self, ctx, user : discord.Member):
        """Retire un utilisateur du Leaderboard du serveur et reset l'XP. """
        if await self.config.guild(ctx.guild).disabled():
            await ctx.send("**Toutes les commandes pour le système de niveau sont désactivées !**")
            return
        if user.bot:
            return
        server = ctx.guild

       
    @btk.command(name="fondslist")
    @commands.guild_only()
    async def disp_backgrounds(self, ctx, fonds_type):
        """Liste les fonds du serveur.\n\nTypes de fonds :\n```Profile | Rank | Levelup```"""
        if not ctx.message.channel.permissions_for(ctx.guild.me).embed_links:
            return await ctx.send(
                "**Je n'ai pas les permissions nécéssaires pour réaliser celà.**"
            )
        server = ctx.guild
        backgrounds = await self.get_backgrounds()

        if await self.config.guild(server).disabled():
            await ctx.send("**Toutes les commandes pour le système de niveau sont désactivées !**")
            return

        em = discord.Embed(colour=await ctx.embed_color())
        if fonds_type.lower() == "profile":
            em.set_author(
                name="Fonds de profil de {}".format(self.bot.user.name),
                icon_url=self.bot.user.avatar_url,
            )
            bg_key = "profile"
        elif fonds_type.lower() == "rank":
            em.set_author(
                name="Fonds de rank de {}".format(self.bot.user.name),
                icon_url=self.bot.user.avatar_url,
            )
            bg_key = "rank"
        elif fonds_type.lower() == "levelup":
            em.set_author(
                name="Fonds de lvl-up de {}".format(self.bot.user.name),
                icon_url=self.bot.user.avatar_url,
            )
            bg_key = "levelup"
        else:
            bg_key = None

        if bg_key:
            embeds = []
            total = len(backgrounds[bg_key])
            cnt = 1
            for bg in sorted(backgrounds[bg_key].keys()):
                em = discord.Embed(
                    title=bg,
                    color=await ctx.embed_color(),
                    url=backgrounds[bg_key][bg],
                    description=f"Fond {cnt}/{total}",
                )
                em.set_image(url=backgrounds[bg_key][bg])
                embeds.append(em)
                cnt += 1
            await menu(ctx, embeds, DEFAULT_CONTROLS)
        else:
            await ctx.send("**Type de fond invalide. Arguments possibles : `profile`, `rank`, `levelup`**")

    async def draw_profile(self, user, server):
        font_file = f"{bundled_data_path(self)}/font.ttf"
        font_bold_file = f"{bundled_data_path(self)}/font_bold.ttf"
        font_unicode_file = f"{bundled_data_path(self)}/unicode.ttf"
        name_fnt = ImageFont.truetype(font_bold_file, 22, encoding="utf-8")
        header_u_fnt = ImageFont.truetype(font_unicode_file, 18, encoding="utf-8")
        title_fnt = ImageFont.truetype(font_file, 18, encoding="utf-8")
        sub_header_fnt = ImageFont.truetype(font_bold_file, 14, encoding="utf-8")
        badge_fnt = ImageFont.truetype(font_bold_file, 10, encoding="utf-8")
        exp_fnt = ImageFont.truetype(font_bold_file, 14, encoding="utf-8")
        large_fnt = ImageFont.truetype(font_bold_file, 33, encoding="utf-8")
        level_label_fnt = ImageFont.truetype(font_bold_file, 22, encoding="utf-8")
        general_info_fnt = ImageFont.truetype(font_bold_file, 15, encoding="utf-8")
        general_info_u_fnt = ImageFont.truetype(font_unicode_file, 12, encoding="utf-8")
        rep_fnt = ImageFont.truetype(font_bold_file, 26, encoding="utf-8")
        text_fnt = ImageFont.truetype(font_bold_file, 12, encoding="utf-8")
        text_u_fnt = ImageFont.truetype(font_unicode_file, 8, encoding="utf-8")
        credit_fnt = ImageFont.truetype(font_bold_file, 10, encoding="utf-8")

        def _write_unicode(text, init_x, y, font, unicode_font, fill):
            write_pos = init_x

            for char in text:
                if char.isalnum() or char in string.punctuation or char in string.whitespace:
                    draw.text((write_pos, y), "{}".format(char), font=font, fill=fill)
                    write_pos += font.getsize(char)[0]
                else:
                    draw.text((write_pos, y), "{}".format(char), font=unicode_font, fill=fill)
                    write_pos += unicode_font.getsize(char)[0]

        # get urls
        userinfo = db.users.find_one({"user_id": str(user.id)})
        self._badge_convert_dict(userinfo)
        bg_url = userinfo["profile_background"]
        profile_url = user.avatar_url

        # create image objects
        bg_image = Image
        profile_image = Image

        async with self.session.get(bg_url) as r:
            image = await r.content.read()
        profile_background = BytesIO(image)
        profile_avatar = BytesIO()
        await user.avatar_url.save(profile_avatar, seek_begin=True)

        bg_image = Image.open(profile_background).convert("RGBA")
        profile_image = Image.open(profile_avatar).convert("RGBA")

        # set canvas
        bg_color = (255, 255, 255, 0)
        result = Image.new("RGBA", (290, 290), bg_color)
        process = Image.new("RGBA", (290, 290), bg_color)

        # draw
        draw = ImageDraw.Draw(process)

        # puts in background
        bg_image = bg_image.resize((290, 290), Image.ANTIALIAS)
        bg_image = bg_image.crop((0, 0, 290, 290))
        result.paste(bg_image, (0, 0))

        # draw filter
        draw.rectangle([(0, 0), (290, 290)], fill=(0, 0, 0, 10))

        # draw transparent overlay
        vert_pos = 110
        left_pos = 70
        right_pos = 285
        title_height = 22
        gap = 3

        # determines rep section color
        if "rep_color" not in userinfo.keys() or not userinfo["rep_color"]:
            rep_fill = (92, 130, 203, 230)
        else:
            rep_fill = tuple(userinfo["rep_color"])
        # determines badge section color, should be behind the titlebar
        if "badge_col_color" not in userinfo.keys() or not userinfo["badge_col_color"]:
            badge_fill = (30, 30, 30, 230)
        else:
            badge_fill = tuple(userinfo["badge_col_color"])

        if "profile_info_color" in userinfo.keys():
            info_color = tuple(userinfo["profile_info_color"])
        else:
            info_color = (30, 30, 30, 220)

        draw.rectangle(
            [(left_pos - 20, vert_pos + title_height), (right_pos, 156)], fill=info_color
        )  # title box
        draw.rectangle([(100, 159), (285, 212)], fill=info_color)  # general content
        draw.rectangle([(100, 215), (285, 285)], fill=info_color)  # info content

        # stick in credits if needed
        # if bg_url in bg_credits.keys():
        # credit_text = "  ".join("Background by {}".format(bg_credits[bg_url]))
        # credit_init = 290 - credit_fnt.getsize(credit_text)[0]
        # draw.text((credit_init, 0), credit_text,  font=credit_fnt, fill=(0,0,0,100))
        draw.rectangle(
            [(5, vert_pos), (right_pos, vert_pos + title_height)], fill=(30, 30, 30, 230)
        )  # name box in front

        # draw level circle
        multiplier = 8
        lvl_circle_dia = 104
        circle_left = 1
        circle_top = 42
        raw_length = lvl_circle_dia * multiplier

        # create mask
        mask = Image.new("L", (raw_length, raw_length), 0)
        draw_thumb = ImageDraw.Draw(mask)
        draw_thumb.ellipse((0, 0) + (raw_length, raw_length), fill=255, outline=0)

        # drawing level bar calculate angle
        start_angle = -90  # from top instead of 3oclock
        angle = (
            int(
                360
                * (
                    userinfo["servers"][str(server.id)]["current_exp"]
                    / self._required_exp(userinfo["servers"][str(server.id)]["level"])
                )
            )
            + start_angle
        )

        # level outline
        lvl_circle = Image.new("RGBA", (raw_length, raw_length))
        draw_lvl_circle = ImageDraw.Draw(lvl_circle)
        draw_lvl_circle.ellipse(
            [0, 0, raw_length, raw_length],
            fill=(badge_fill[0], badge_fill[1], badge_fill[2], 180),
            outline=(255, 255, 255, 250),
        )
        # determines exp bar color
        if "profile_exp_color" not in userinfo.keys() or not userinfo["profile_exp_color"]:
            exp_fill = (255, 255, 255, 230)
        else:
            exp_fill = tuple(userinfo["profile_exp_color"])
        draw_lvl_circle.pieslice(
            [0, 0, raw_length, raw_length],
            start_angle,
            angle,
            fill=exp_fill,
            outline=(255, 255, 255, 255),
        )
        # put on level bar circle
        lvl_circle = lvl_circle.resize((lvl_circle_dia, lvl_circle_dia), Image.ANTIALIAS)
        lvl_bar_mask = mask.resize((lvl_circle_dia, lvl_circle_dia), Image.ANTIALIAS)
        process.paste(lvl_circle, (circle_left, circle_top), lvl_bar_mask)

        # draws boxes
        draw.rectangle([(5, 133), (100, 285)], fill=badge_fill)  # badges
        draw.rectangle([(10, 138), (95, 168)], fill=rep_fill)  # reps

        total_gap = 10
        border = int(total_gap / 2)
        profile_size = lvl_circle_dia - total_gap
        raw_length = profile_size * multiplier
        # put in profile picture
        total_gap = 6
        border = int(total_gap / 2)
        profile_size = lvl_circle_dia - total_gap
        mask = mask.resize((profile_size, profile_size), Image.ANTIALIAS)
        profile_image = profile_image.resize((profile_size, profile_size), Image.ANTIALIAS)
        process.paste(profile_image, (circle_left + border, circle_top + border), mask)

        # write label text
        white_color = (240, 240, 240, 255)
        light_color = (160, 160, 160, 255)

        head_align = 105
        _write_unicode(
            self._truncate_text(self._name(user, 22), 22),
            head_align,
            vert_pos + 3,
            level_label_fnt,
            header_u_fnt,
            (110, 110, 110, 255),
        )  # NAME
        _write_unicode(
            userinfo["title"], head_align, 136, level_label_fnt, header_u_fnt, white_color
        )

        # draw level box
        level_right = 290
        level_left = level_right - 78
        draw.rectangle(
            [(level_left, 0), (level_right, 21)],
            fill=(badge_fill[0], badge_fill[1], badge_fill[2], 160),
        )  # box
        lvl_text = "NIV. {}".format(userinfo["servers"][str(server.id)]["level"])
        if badge_fill == (128, 151, 165, 230):
            lvl_color = white_color
        else:
            lvl_color = self._contrast(badge_fill, rep_fill, exp_fill)
        draw.text(
            (self._center(level_left + 2, level_right, lvl_text, level_label_fnt), 2),
            lvl_text,
            font=level_label_fnt,
            fill=(lvl_color[0], lvl_color[1], lvl_color[2], 255),
        )  # Level #
        if userinfo["rep"] < 2:
            rep_text = "{} REP".format(userinfo["rep"])
        else:
            rep_text = "{} REPS".format(userinfo["rep"])
        draw.text(
            (self._center(7, 100, rep_text, rep_fnt), 144),
            rep_text,
            font=rep_fnt,
            fill=white_color,
        )

        exp_text = "{}/{}".format(
            userinfo["servers"][str(server.id)]["current_exp"],
            self._required_exp(userinfo["servers"][str(server.id)]["level"]),
        )  # Exp
        exp_color = exp_fill
        draw.text(
            (105, 99), exp_text, font=exp_fnt, fill=(exp_color[0], exp_color[1], exp_color[2], 255)
        )  # Exp Text

        # determine info text color
        dark_text = (35, 35, 35, 230)
        info_text_color = self._contrast(info_color, light_color, dark_text)

        lvl_left = 100
        label_align = 105
        _write_unicode(
            "Rank:", label_align, 165, general_info_fnt, general_info_u_fnt, info_text_color
        )
        draw.text((label_align, 180), "XP:", font=general_info_fnt, fill=info_text_color)  # Exp
        draw.text(
            (label_align, 195), "Crédits:", font=general_info_fnt, fill=info_text_color
        )  # Credits

        # local stats
        num_local_align = 172
        local_symbol = "\U0001F3E0 "
        if "linux" in platform.system().lower():
            local_symbol = "\U0001F3E0 "
        else:
            local_symbol = "S "

        s_rank_txt = local_symbol + self._truncate_text(
            "#{}".format(await self._find_server_rank(user, server)), 8
        )
        _write_unicode(
            s_rank_txt,
            num_local_align - general_info_u_fnt.getsize(local_symbol)[0],
            165,
            general_info_fnt,
            general_info_u_fnt,
            info_text_color,
        )  # Rank

        s_exp_txt = self._truncate_text("{}".format(await self._find_server_exp(user, server)), 8)
        _write_unicode(
            s_exp_txt, num_local_align, 180, general_info_fnt, general_info_u_fnt, info_text_color
        )  # Exp
        credits = await bank.get_balance(user)
        credit_txt = "${}".format(credits)
        draw.text(
            (num_local_align, 195),
            self._truncate_text(credit_txt, 18),
            font=general_info_fnt,
            fill=rep_fill,
        )  # Credits
        global stats
        num_align = 230
        if "linux" in platform.system().lower():
            global_symbol = "\U0001F30E "
            fine_adjust = 1
        else:
            global_symbol = "G "
            fine_adjust = 0

        rank_txt = global_symbol + self._truncate_text(
            "#{}".format(await self._find_global_rank(user)), 8
        )
        exp_txt = self._truncate_text("{}".format(userinfo["total_exp"]), 8)
        _write_unicode(
            rank_txt,
            num_align - general_info_u_fnt.getsize(global_symbol)[0] + fine_adjust,
            165,
            general_info_fnt,
            general_info_u_fnt,
            info_text_color,
        )   #Rank
        _write_unicode(
            exp_txt, num_align, 180, general_info_fnt, general_info_u_fnt, info_text_color
        )   #Exp

        draw.text((105, 220), "Infobox", font=sub_header_fnt, fill=white_color)  # Info Box
        margin = 105
        offset = 238
        for line in textwrap.wrap(userinfo["info"], width=42):
            # draw.text((margin, offset), line, font=text_fnt, fill=(70,70,70,255))
            _write_unicode(line, margin, offset, text_fnt, text_u_fnt, rep_fill)
            offset += text_fnt.getsize(line)[1] + 2

        # sort badges
        priority_badges = []

        for badgename in userinfo["badges"].keys():
            badge = userinfo["badges"][badgename]
            priority_num = badge["priority_num"]
            if priority_num != 0 and priority_num != -1:
                priority_badges.append((badge, priority_num))
        sorted_badges = sorted(priority_badges, key=operator.itemgetter(1), reverse=True)

        # TODO: simplify this. it shouldn't be this complicated... sacrifices conciseness for customizability
        if await self.config.badge_type() == "circles":
            # circles require antialiasing
            vert_pos = 171
            right_shift = 0
            left = 9 + right_shift
            right = 52 + right_shift
            size = 27
            total_gap = 4  # /2
            hor_gap = 3
            vert_gap = 2
            border_width = int(total_gap / 2)
            mult = [
                (0, 0),
                (1, 0),
                (2, 0),
                (0, 1),
                (1, 1),
                (2, 1),
                (0, 2),
                (1, 2),
                (2, 2),
                (0, 3),
                (1, 3),
                (2, 3),
            ]
            i = 0
            for pair in sorted_badges[:12]:
                try:
                    coord = (
                        left + int(mult[i][0]) * int(hor_gap + size),
                        vert_pos + int(mult[i][1]) * int(vert_gap + size),
                    )
                    badge = pair[0]
                    bg_color = badge["bg_img"]
                    border_color = badge["border_color"]
                    multiplier = 6  # for antialiasing
                    raw_length = size * multiplier

                    # draw mask circle
                    mask = Image.new("L", (raw_length, raw_length), 0)
                    draw_thumb = ImageDraw.Draw(mask)
                    draw_thumb.ellipse((0, 0) + (raw_length, raw_length), fill=255, outline=0)

                    # determine image or color for badge bg
                    if await self._valid_image_url(bg_color):
                        # get image
                        async with self.session.get(bg_color) as r:
                            image = await r.content.read()
                        with open(f"{cog_data_path(self)}/{user.id}_temp_badge.png", "wb") as f:
                            f.write(image)
                        badge_image = Image.open(
                            f"{cog_data_path(self)}/{user.id}_temp_badge.png"
                        ).convert("RGBA")
                        badge_image = badge_image.resize((raw_length, raw_length), Image.ANTIALIAS)

                        # structured like this because if border = 0, still leaves outline.
                        if border_color:
                            square = Image.new("RGBA", (raw_length, raw_length), border_color)
                            # put border on ellipse/circle
                            output = ImageOps.fit(
                                square, (raw_length, raw_length), centering=(0.5, 0.5)
                            )
                            output = output.resize((size, size), Image.ANTIALIAS)
                            outer_mask = mask.resize((size, size), Image.ANTIALIAS)
                            process.paste(output, coord, outer_mask)

                            # put on ellipse/circle
                            output = ImageOps.fit(
                                badge_image, (raw_length, raw_length), centering=(0.5, 0.5)
                            )
                            output = output.resize(
                                (size - total_gap, size - total_gap), Image.ANTIALIAS
                            )
                            inner_mask = mask.resize(
                                (size - total_gap, size - total_gap), Image.ANTIALIAS
                            )
                            process.paste(
                                output,
                                (coord[0] + border_width, coord[1] + border_width),
                                inner_mask,
                            )
                        else:
                            # put on ellipse/circle
                            output = ImageOps.fit(
                                badge_image, (raw_length, raw_length), centering=(0.5, 0.5)
                            )
                            output = output.resize((size, size), Image.ANTIALIAS)
                            outer_mask = mask.resize((size, size), Image.ANTIALIAS)
                            process.paste(output, coord, outer_mask)
                except:
                    pass
                # attempt to remove badge image
                try:
                    os.remove(f"{cog_data_path(self)}/{user.id}_temp_badge.png")
                except:
                    pass
                i += 1
        elif await self.config.badge_type() == "bars":
            vert_pos = 187
            i = 0
            for pair in sorted_badges[:5]:
                badge = pair[0]
                bg_color = badge["bg_img"]
                border_color = badge["border_color"]
                left_pos = 10
                right_pos = 95
                total_gap = 4
                border_width = int(total_gap / 2)
                bar_size = (85, 15)

                # determine image or color for badge bg
                if await self._valid_image_url(bg_color):
                    async with self.session.get(bg_color) as r:
                        image = await r.content.read()
                    with open(f"{cog_data_path(self)}/{user.id}_temp_badge.png", "wb") as f:
                        f.write(image)
                    badge_image = Image.open(
                        f"{cog_data_path(self)}/{user.id}_temp_badge.png"
                    ).convert("RGBA")

                    if border_color != None:
                        draw.rectangle(
                            [(left_pos, vert_pos + i * 17), (right_pos, vert_pos + 15 + i * 17)],
                            fill=border_color,
                            outline=border_color,
                        )  # border
                        badge_image = badge_image.resize(
                            (bar_size[0] - total_gap + 1, bar_size[1] - total_gap + 1),
                            Image.ANTIALIAS,
                        )
                        process.paste(
                            badge_image,
                            (left_pos + border_width, vert_pos + border_width + i * 17),
                        )
                    else:
                        badge_image = badge_image.resize(bar_size, Image.ANTIALIAS)
                        process.paste(badge_image, (left_pos, vert_pos + i * 17))
                    try:
                        os.remove(f"{cog_data_path(self)}/{user.id}_temp_badge.png")
                    except:
                        pass

                vert_pos += 3  # spacing
                i += 1

        result = Image.alpha_composite(result, process)
        result.save(f"{cog_data_path(self)}/{user.id}_profile.png", "PNG", quality=100)

        # remove images
        try:
            os.remove(f"{cog_data_path(self)}/{user.id}_temp_profile_bg.png")
        except:
            pass
        try:
            os.remove(f"{cog_data_path(self)}/{user.id}_temp_profile_bg.png")
        except:
            pass

    # returns color that contrasts better in background
    def _contrast(self, bg_color, color1, color2):
        color1_ratio = self._contrast_ratio(bg_color, color1)
        color2_ratio = self._contrast_ratio(bg_color, color2)
        if color1_ratio >= color2_ratio:
            return color1
        else:
            return color2

    def _luminance(self, color):
        # convert to greyscale
        luminance = float((0.2126 * color[0]) + (0.7152 * color[1]) + (0.0722 * color[2]))
        return luminance

    def _contrast_ratio(self, bgcolor, foreground):
        f_lum = float(self._luminance(foreground) + 0.05)
        bg_lum = float(self._luminance(bgcolor) + 0.05)

        if bg_lum > f_lum:
            return bg_lum / f_lum
        else:
            return f_lum / bg_lum

    # returns a string with possibly a nickname
    def _name(self, user, max_length):
        if user.name == user.display_name:
            return user.name
        else:
            return "{} ({})".format(
                user.name,
                self._truncate_text(user.display_name, max_length - len(user.name) - 3),
                max_length,
            )

    async def _add_dropshadow(
        self, image, offset=(4, 4), background=0x000, shadow=0x0F0, border=3, iterations=5
    ):
        totalWidth = image.size[0] + abs(offset[0]) + 2 * border
        totalHeight = image.size[1] + abs(offset[1]) + 2 * border
        back = Image.new(image.mode, (totalWidth, totalHeight), background)

        # Place the shadow, taking into account the offset from the image
        shadowLeft = border + max(offset[0], 0)
        shadowTop = border + max(offset[1], 0)
        back.paste(
            shadow, [shadowLeft, shadowTop, shadowLeft + image.size[0], shadowTop + image.size[1]]
        )

        n = 0
        while n < iterations:
            back = back.filter(ImageFilter.BLUR)
            n += 1

        # Paste the input image onto the shadow backdrop
        imageLeft = border - min(offset[0], 0)
        imageTop = border - min(offset[1], 0)
        back.paste(image, (imageLeft, imageTop))
        return back

    async def draw_rank(self, user, server):
        # fonts
        font_file = f"{bundled_data_path(self)}/font.ttf"
        font_bold_file = f"{bundled_data_path(self)}/font_bold.ttf"
        font_unicode_file = f"{bundled_data_path(self)}/unicode.ttf"
        name_fnt = ImageFont.truetype(font_bold_file, 22)
        header_u_fnt = ImageFont.truetype(font_unicode_file, 18)
        sub_header_fnt = ImageFont.truetype(font_bold_file, 14)
        badge_fnt = ImageFont.truetype(font_bold_file, 12)
        large_fnt = ImageFont.truetype(font_bold_file, 33)
        level_label_fnt = ImageFont.truetype(font_bold_file, 22)
        general_info_fnt = ImageFont.truetype(font_bold_file, 15)
        general_info_u_fnt = ImageFont.truetype(font_unicode_file, 11)
        credit_fnt = ImageFont.truetype(font_bold_file, 10)

        def _write_unicode(text, init_x, y, font, unicode_font, fill):
            write_pos = init_x

            for char in text:
                if char.isalnum() or char in string.punctuation or char in string.whitespace:
                    draw.text((write_pos, y), char, font=font, fill=fill)
                    write_pos += font.getsize(char)[0]
                else:
                    draw.text((write_pos, y), "{}".format(char), font=unicode_font, fill=fill)
                    write_pos += unicode_font.getsize(char)[0]

        userinfo = db.users.find_one({"user_id": str(user.id)})
        # get urls
        bg_url = userinfo["rank_background"]
        server_icon_url = server.icon_url_as(format="png", size=256)

        # guild icon image
        if not server_icon_url._url:
            server_icon_url = "https://i.imgur.com/BDW180Y.png"
            async with self.session.get(server_icon_url) as r:
                server_icon_image = await r.content.read()
                server_icon = BytesIO(server_icon_image)
        else:
            server_icon = BytesIO()
            await server_icon_url.save(server_icon, seek_begin=True)

        # rank bg image
        async with self.session.get(bg_url) as r:
            image = await r.content.read()
        rank_background = BytesIO(image)

        # user icon image
        rank_avatar = BytesIO()
        await user.avatar_url.save(rank_avatar, seek_begin=True)

        # set all to RGBA
        bg_image = Image.open(rank_background).convert("RGBA")
        profile_image = Image.open(rank_avatar).convert("RGBA")
        server_icon_image = Image.open(server_icon).convert("RGBA")

        # set canvas
        width = 360
        height = 100
        bg_color = (255, 255, 255, 0)
        result = Image.new("RGBA", (width, height), bg_color)
        process = Image.new("RGBA", (width, height), bg_color)

        # puts in background
        bg_image = bg_image.resize((width, height), Image.ANTIALIAS)
        bg_image = bg_image.crop((0, 0, width, height))
        result.paste(bg_image, (0, 0))

        # draw
        draw = ImageDraw.Draw(process)

        # draw transparent overlay
        vert_pos = 5
        left_pos = 70
        right_pos = width - vert_pos
        title_height = 22
        gap = 3

        draw.rectangle(
            [(left_pos - 20, vert_pos), (right_pos, vert_pos + title_height)],
            fill=(230, 230, 230, 230),
        )  # title box
        content_top = vert_pos + title_height + gap
        content_bottom = 100 - vert_pos

        if "rank_info_color" in userinfo.keys():
            info_color = tuple(userinfo["rank_info_color"])
            info_color = (
                info_color[0],
                info_color[1],
                info_color[2],
                160,
            )  # increase transparency
        else:
            info_color = (30, 30, 30, 160)
        draw.rectangle(
            [(left_pos - 20, content_top), (right_pos, content_bottom)],
            fill=info_color,
            outline=(180, 180, 180, 180),
        )  # content box

        # stick in credits if needed
        # if bg_url in bg_credits.keys():
        # credit_text = " ".join("{}".format(bg_credits[bg_url]))
        # draw.text((2, 92), credit_text,  font=credit_fnt, fill=(0,0,0,190))

        # draw level circle
        multiplier = 6
        lvl_circle_dia = 94
        circle_left = 15
        circle_top = int((height - lvl_circle_dia) / 2)
        raw_length = lvl_circle_dia * multiplier

        # create mask
        mask = Image.new("L", (raw_length, raw_length), 0)
        draw_thumb = ImageDraw.Draw(mask)
        draw_thumb.ellipse((0, 0) + (raw_length, raw_length), fill=255, outline=0)

        # drawing level bar calculate angle
        start_angle = -90  # from top instead of 3oclock
        angle = (
            int(
                360
                * (
                    userinfo["servers"][str(server.id)]["current_exp"]
                    / self._required_exp(userinfo["servers"][str(server.id)]["level"])
                )
            )
            + start_angle
        )

        lvl_circle = Image.new("RGBA", (raw_length, raw_length))
        draw_lvl_circle = ImageDraw.Draw(lvl_circle)
        draw_lvl_circle.ellipse(
            [0, 0, raw_length, raw_length], fill=(180, 180, 180, 180), outline=(255, 255, 255, 220)
        )
        # determines exp bar color
        if "rank_exp_color" not in userinfo.keys() or not userinfo["rank_exp_color"]:
            exp_fill = (255, 255, 255, 230)
        else:
            exp_fill = tuple(userinfo["rank_exp_color"])
        draw_lvl_circle.pieslice(
            [0, 0, raw_length, raw_length],
            start_angle,
            angle,
            fill=exp_fill,
            outline=(255, 255, 255, 230),
        )
        # put on level bar circle
        lvl_circle = lvl_circle.resize((lvl_circle_dia, lvl_circle_dia), Image.ANTIALIAS)
        lvl_bar_mask = mask.resize((lvl_circle_dia, lvl_circle_dia), Image.ANTIALIAS)
        process.paste(lvl_circle, (circle_left, circle_top), lvl_bar_mask)

        # draws mask
        total_gap = 10
        border = int(total_gap / 2)
        profile_size = lvl_circle_dia - total_gap
        raw_length = profile_size * multiplier
        # put in profile picture
        output = ImageOps.fit(profile_image, (raw_length, raw_length), centering=(0.5, 0.5))
        output.resize((profile_size, profile_size), Image.ANTIALIAS)
        mask = mask.resize((profile_size, profile_size), Image.ANTIALIAS)
        profile_image = profile_image.resize((profile_size, profile_size), Image.ANTIALIAS)
        process.paste(profile_image, (circle_left + border, circle_top + border), mask)

        # draw level box
        level_left = 274
        level_right = right_pos
        draw.rectangle(
            [(level_left, vert_pos), (level_right, vert_pos + title_height)], fill="#AAA"
        )  # box
        lvl_text = "LEVEL {}".format(userinfo["servers"][str(server.id)]["level"])
        draw.text(
            (self._center(level_left, level_right, lvl_text, level_label_fnt), vert_pos + 3),
            lvl_text,
            font=level_label_fnt,
            fill=(110, 110, 110, 255),
        )  # Level #

        # labels text colors
        white_text = (240, 240, 240, 255)
        dark_text = (35, 35, 35, 230)
        label_text_color = self._contrast(info_color, white_text, dark_text)

        # draw text
        grey_color = (110, 110, 110, 255)
        white_color = (230, 230, 230, 255)

        # put in server picture
        server_size = content_bottom - content_top - 10
        server_border_size = server_size + 4
        radius = 20
        light_border = (150, 150, 150, 180)
        dark_border = (90, 90, 90, 180)
        border_color = self._contrast(info_color, light_border, dark_border)

        draw_server_border = Image.new(
            "RGBA",
            (server_border_size * multiplier, server_border_size * multiplier),
            border_color,
        )
        draw_server_border = self._add_corners(draw_server_border, int(radius * multiplier / 2))
        draw_server_border = draw_server_border.resize(
            (server_border_size, server_border_size), Image.ANTIALIAS
        )
        server_icon_image = server_icon_image.resize(
            (server_size * multiplier, server_size * multiplier), Image.ANTIALIAS
        )
        server_icon_image = self._add_corners(server_icon_image, int(radius * multiplier / 2) - 10)
        server_icon_image = server_icon_image.resize((server_size, server_size), Image.ANTIALIAS)
        process.paste(
            draw_server_border,
            (circle_left + profile_size + 2 * border + 8, content_top + 3),
            draw_server_border,
        )
        process.paste(
            server_icon_image,
            (circle_left + profile_size + 2 * border + 10, content_top + 5),
            server_icon_image,
        )

        # name
        left_text_align = 130
        _write_unicode(
            self._truncate_text(self._name(user, 20), 20),
            left_text_align - 12,
            vert_pos + 3,
            name_fnt,
            header_u_fnt,
            grey_color,
        )  # Name

        # divider bar
        draw.rectangle([(187, 45), (188, 85)], fill=(160, 160, 160, 220))

        # labels
        label_align = 200
        draw.text(
            (label_align, 38), "Rank du serveur:", font=general_info_fnt, fill=label_text_color
        )  # Server Rank
        draw.text(
            (label_align, 58), "XP:", font=general_info_fnt, fill=label_text_color
        )  # Server Exp
        draw.text(
            (label_align, 78), "Crédits:", font=general_info_fnt, fill=label_text_color
        )  # Credit
        # info
        right_text_align = 290
        rank_txt = "#{}".format(await self._find_server_rank(user, server))
        draw.text(
            (right_text_align, 38),
            self._truncate_text(rank_txt, 12),
            font=general_info_fnt,
            fill=label_text_color,
        )  # Rank
        exp_txt = "{}".format(await self._find_server_exp(user, server))
        draw.text(
            (right_text_align, 58),
            self._truncate_text(exp_txt, 12),
            font=general_info_fnt,
            fill=label_text_color,
        )  # Exp
        credits = await bank.get_balance(user)
        credit_txt = "${}".format(credits)
        draw.text(
            (right_text_align, 78),
            self._truncate_text(credit_txt, 12),
            font=general_info_fnt,
            fill=label_text_color,
        )  # Credits

        result = Image.alpha_composite(result, process)
        result.save(f"{cog_data_path(self)}/{user.id}_rank.png", "PNG", quality=100)

    def _add_corners(self, im, rad, multiplier=6):
        raw_length = rad * 2 * multiplier
        circle = Image.new("L", (raw_length, raw_length), 0)
        draw = ImageDraw.Draw(circle)
        draw.ellipse((0, 0, raw_length, raw_length), fill=255)
        circle = circle.resize((rad * 2, rad * 2), Image.ANTIALIAS)

        alpha = Image.new("L", im.size, 255)
        w, h = im.size
        alpha.paste(circle.crop((0, 0, rad, rad)), (0, 0))
        alpha.paste(circle.crop((0, rad, rad, rad * 2)), (0, h - rad))
        alpha.paste(circle.crop((rad, 0, rad * 2, rad)), (w - rad, 0))
        alpha.paste(circle.crop((rad, rad, rad * 2, rad * 2)), (w - rad, h - rad))
        im.putalpha(alpha)
        return im

    async def draw_levelup(self, user, server):
        font_bold_file = f"{bundled_data_path(self)}/font_bold.ttf"
        userinfo = db.users.find_one({"user_id": str(user.id)})
        # get urls
        bg_url = userinfo["levelup_background"]
        profile_url = user.avatar_url

        # create image objects
        bg_image = Image
        profile_image = Image

        async with self.session.get(bg_url) as r:
            image = await r.content.read()

        level_background = BytesIO(image)
        level_avatar = BytesIO()
        await user.avatar_url.save(level_avatar, seek_begin=True)

        bg_image = Image.open(level_background).convert("RGBA")
        profile_image = Image.open(level_avatar).convert("RGBA")

        # set canvas
        width = 175
        height = 65
        bg_color = (255, 255, 255, 0)
        result = Image.new("RGBA", (width, height), bg_color)
        process = Image.new("RGBA", (width, height), bg_color)

        # draw
        draw = ImageDraw.Draw(process)

        # puts in background
        bg_image = bg_image.resize((width, height), Image.ANTIALIAS)
        bg_image = bg_image.crop((0, 0, width, height))
        result.paste(bg_image, (0, 0))

        # draw transparent overlay
        if "levelup_info_color" in userinfo.keys():
            info_color = tuple(userinfo["levelup_info_color"])
            info_color = (
                info_color[0],
                info_color[1],
                info_color[2],
                150,
            )  # increase transparency
        else:
            info_color = (30, 30, 30, 150)
        draw.rectangle([(38, 5), (170, 60)], fill=info_color)  # info portion

        # draw level circle
        multiplier = 6
        lvl_circle_dia = 60
        circle_left = 4
        circle_top = int((height - lvl_circle_dia) / 2)
        raw_length = lvl_circle_dia * multiplier

        # create mask
        mask = Image.new("L", (raw_length, raw_length), 0)
        draw_thumb = ImageDraw.Draw(mask)
        draw_thumb.ellipse((0, 0) + (raw_length, raw_length), fill=255, outline=0)

        # drawing level bar calculate angle
        start_angle = -90  # from top instead of 3oclock

        lvl_circle = Image.new("RGBA", (raw_length, raw_length))
        draw_lvl_circle = ImageDraw.Draw(lvl_circle)
        draw_lvl_circle.ellipse(
            [0, 0, raw_length, raw_length], fill=(255, 255, 255, 220), outline=(255, 255, 255, 220)
        )

        # put on level bar circle
        lvl_circle = lvl_circle.resize((lvl_circle_dia, lvl_circle_dia), Image.ANTIALIAS)
        lvl_bar_mask = mask.resize((lvl_circle_dia, lvl_circle_dia), Image.ANTIALIAS)
        process.paste(lvl_circle, (circle_left, circle_top), lvl_bar_mask)

        # draws mask
        total_gap = 6
        border = int(total_gap / 2)
        profile_size = lvl_circle_dia - total_gap
        raw_length = profile_size * multiplier
        # put in profile picture
        output = ImageOps.fit(profile_image, (raw_length, raw_length), centering=(0.5, 0.5))
        output = output.resize((profile_size, profile_size), Image.ANTIALIAS)
        mask = mask.resize((profile_size, profile_size), Image.ANTIALIAS)
        profile_image = profile_image.resize((profile_size, profile_size), Image.ANTIALIAS)
        process.paste(profile_image, (circle_left + border, circle_top + border), mask)

        # fonts
        level_fnt2 = ImageFont.truetype(font_bold_file, 19)
        level_fnt = ImageFont.truetype(font_bold_file, 26)

        # write label text
        white_text = (240, 240, 240, 255)
        dark_text = (35, 35, 35, 230)
        level_up_text = self._contrast(info_color, white_text, dark_text)
        lvl_text = "LEVEL {}".format(userinfo["servers"][str(server.id)]["level"])
        draw.text(
            (self._center(50, 170, lvl_text, level_fnt), 22),
            lvl_text,
            font=level_fnt,
            fill=level_up_text,
        )  # Level Number

        result = Image.alpha_composite(result, process)
        filename = f"{cog_data_path(self)}/{user.id}_level.png"
        result.save(filename, "PNG", quality=100)

    @commands.Cog.listener("on_message_without_command")
    async def _handle_on_message(self, message):
        text = message.content
        channel = message.channel
        server = message.guild
        user = message.author
        prefix = await self.bot.command_prefix(self.bot, message)
        # creates user if doesn't exist, bots are not logged.
        await self._create_user(user, server)
        curr_time = time.time()
        userinfo = db.users.find_one({"user_id": str(user.id)})

        if not server or await self.config.guild(server).disabled():
            return
        if user.bot:
            return

        # check if chat_block exists
        if "chat_block" not in userinfo:
            userinfo["chat_block"] = 0

        if "last_message" not in userinfo:
            userinfo["last_message"] = 0
        if all(
            [
                float(curr_time) - float(userinfo["chat_block"]) >= 120,
                not any(text.startswith(x) for x in prefix),
                len(message.content) > await self.config.message_length() or message.attachments,
                message.content != userinfo["last_message"],
                message.channel.id
                not in await self.config.guild(server).ignored_channels(),
            ]
        ):
            xp = await self.config.xp()
            await self._process_exp(message, userinfo, random.randint(xp[0], xp[1]))
            await self._give_chat_credit(user, server)

    async def _process_exp(self, message, userinfo, exp: int):
        server = message.guild
        channel = message.channel
        user = message.author
        # add to total exp
        required = self._required_exp(userinfo["servers"][str(server.id)]["level"])
        try:
            db.users.update_one(
                {"user_id": str(user.id)}, {"$set": {"total_exp": userinfo["total_exp"] + exp}}
            )
        except:
            pass
        if userinfo["servers"][str(server.id)]["current_exp"] + exp >= required:
            userinfo["servers"][str(server.id)]["level"] += 1
            db.users.update_one(
                {"user_id": str(user.id)},
                {
                    "$set": {
                        "servers.{}.level".format(server.id): userinfo["servers"][str(server.id)][
                            "level"
                        ],
                        "servers.{}.current_exp".format(server.id): userinfo["servers"][
                            str(server.id)
                        ]["current_exp"]
                        + exp
                        - required,
                        "chat_block": time.time(),
                        "last_message": message.content,
                    }
                },
            )
            await self._handle_levelup(user, userinfo, server, channel)
        else:
            db.users.update_one(
                {"user_id": str(user.id)},
                {
                    "$set": {
                        "servers.{}.current_exp".format(server.id): userinfo["servers"][
                            str(server.id)
                        ]["current_exp"]
                        + exp,
                        "chat_block": time.time(),
                        "last_message": message.content,
                    }
                },
            )

    async def _handle_levelup(self, user, userinfo, server, channel):
        if await self.config.guild(server).lvl_msg():  # if lvl msg is enabled
            # channel lock implementation
            channel_id = await self.config.guild(server).lvl_msg_lock()
            if channel_id:
                channel = find(lambda m: m.id == channel_id, server.channels)

            server_identifier = ""  # super hacky
            name = await self._is_mention(user)  # also super hacky
            # private message takes precedent, of course
            if await self.config.guild(server).private_lvl_message():
                server_identifier = f" on {server.name}"
                channel = user
                name = "You"

            new_level = str(userinfo["servers"][str(server.id)]["level"])
            server_roles = db.roles.find_one({"server_id": str(server.id)})
            if server_roles is not None:
                for role in server_roles["roles"].keys():
                    if int(server_roles["roles"][role]["level"]) == int(new_level):
                        add_role = discord.utils.get(server.roles, name=role)
                        if add_role is not None:
                            try:
                                await user.add_roles(add_role, reason="levelup")
                            except discord.Forbidden:
                                await channel.send(
                                    "Le processus pour retirer le rôle a échoué: Permissions manquantes."
                                )
                            except discord.HTTPException:
                                await channel.send("Le processus pour retirer le rôle a échoué")
                        remove_role = discord.utils.get(
                            server.roles, name=server_roles["roles"][role]["remove_role"]
                        )
                        if remove_role is not None:
                            try:
                                await user.remove_roles(remove_role, reason="levelup")
                            except discord.Forbidden:
                                await channel.send(
                                    "Le processus pour retirer le rôle a échoué: Permissions manquantes."
                                )
                            except discord.HTTPException:
                                await channel.send("Le processus pour retirer le rôle a échoué.")
                        # await user.edit(roles=new_roles, reason="levelup")

            # add appropriate badge if necessary
            try:
                server_linked_badges = db.badgelinks.find_one({"server_id": str(server.id)})
                if server_linked_badges is not None:
                    for badge_name in server_linked_badges["badges"]:
                        if int(server_linked_badges["badges"][badge_name]) == int(new_level):
                            server_badges = db.badges.find_one({"server_id": str(server.id)})
                            if (
                                server_badges is not None
                                and badge_name in server_badges["badges"].keys()
                            ):
                                userinfo_db = db.users.find_one({"user_id": str(user.id)})
                                new_badge_name = "{}_{}".format(badge_name, server.id)
                                userinfo_db["badges"][new_badge_name] = server_badges["badges"][
                                    badge_name
                                ]
                                db.users.update_one(
                                    {"user_id": str(user.id)},
                                    {"$set": {"badges": userinfo_db["badges"]}},
                                )
            except:
                await channel.send("Erreur. Le badge n'a pas été donné !")

            if await self.config.guild(server).text_only():
                async with channel.typing():
                    em = discord.Embed(
                        description="**{} est passé au niveau {} !**".format(
                            name, new_level
                        ),
                        colour=user.colour,
                    )
                    await channel.send(embed=em)
            else:
                async with channel.typing():
                    await self.draw_levelup(user, server)
                    file = discord.File(
                        f"{cog_data_path(self)}/{user.id}_level.png", filename="levelup.png"
                    )
                    await channel.send(
                        "**{} est monté au niveau {}, bravo ! n//n**".format(name, new_level), file=file
                    )
            self.bot.dispatch("leveler_levelup", user, new_level)

    async def _find_server_rank(self, user, server):
        targetid = str(user.id)
        users = []

        for userinfo in db.users.find({}):
            try:
                server_exp = 0
                userid = userinfo["user_id"]
                for i in range(userinfo["servers"][str(server.id)]["level"]):
                    server_exp += self._required_exp(i)
                server_exp += userinfo["servers"][str(server.id)]["current_exp"]
                users.append((userid, server_exp))
            except:
                pass

        sorted_list = sorted(users, key=operator.itemgetter(1), reverse=True)

        rank = 1
        for a_user in sorted_list:
            if a_user[0] == targetid:
                return rank
            rank += 1

    async def _find_server_rep_rank(self, user, server):
        targetid = str(user.id)
        users = []
        for userinfo in db.users.find({}):
            userid = userinfo["user_id"]
            if "servers" in userinfo and server.id in userinfo["servers"]:
                users.append((userinfo["user_id"], userinfo["rep"]))

        sorted_list = sorted(users, key=operator.itemgetter(1), reverse=True)

        rank = 1
        for a_user in sorted_list:
            if a_user[0] == targetid:
                return rank
            rank += 1

    async def _find_server_exp(self, user, server):
        server_exp = 0
        userinfo = db.users.find_one({"user_id": str(user.id)})

        try:
            for i in range(userinfo["servers"][str(server.id)]["level"]):
                server_exp += self._required_exp(i)
            server_exp += userinfo["servers"][str(server.id)]["current_exp"]
            return server_exp
        except:
            return server_exp

    async def _find_global_rank(self, user):
        users = []

        for userinfo in db.users.find({}):
            try:
                userid = userinfo["user_id"]
                users.append((userid, userinfo["total_exp"]))
            except KeyError:
                pass
        sorted_list = sorted(users, key=operator.itemgetter(1), reverse=True)

        rank = 1
        for stats in sorted_list:
            if stats[0] == str(user.id):
                return rank
            rank += 1

    async def _find_global_rep_rank(self, user):
        users = []

        for userinfo in db.users.find({}):
            try:
                userid = userinfo["user_id"]
                users.append((userid, userinfo["rep"]))
            except KeyError:
                pass
        sorted_list = sorted(users, key=operator.itemgetter(1), reverse=True)

        rank = 1
        for stats in sorted_list:
            if stats[0] == str(user.id):
                return rank
            rank += 1

    # handles user creation, adding new server, blocking
    async def _create_user(self, user, server):
        backgrounds = await self.get_backgrounds()
        default_profile = await self.config.default_profile()
        default_rank = await self.config.default_rank()
        default_levelup = await self.config.default_levelup()

        if user.bot:
            return
        try:
            userinfo = db.users.find_one({"user_id": str(user.id)})
            if not userinfo:
                new_account = {
                    "user_id": str(user.id),
                    "username": user.name,
                    "servers": {},
                    "total_exp": 0,
                    "profile_background": default_profile,
                    "rank_background": default_rank,
                    "levelup_background": default_levelup,
                    "title": "",
                    "info": "I am a mysterious person.",
                    "rep": 0,
                    "badges": {},
                    "active_badges": {},
                    "rep_color": [],
                    "badge_col_color": [],
                    "rep_block": 0,
                    "chat_block": 0,
                    "last_message": "",
                    "profile_block": 0,
                    "rank_block": 0,
                }
                db.users.insert_one(new_account)

            userinfo = db.users.find_one({"user_id": str(user.id)})

            if "username" not in userinfo or userinfo["username"] != user.name:
                db.users.update_one(
                    {"user_id": str(user.id)}, {"$set": {"username": user.name}}, upsert=True
                )

            if "servers" not in userinfo or str(server.id) not in userinfo["servers"]:
                db.users.update_one(
                    {"user_id": str(user.id)},
                    {
                        "$set": {
                            "servers.{}.level".format(server.id): 0,
                            "servers.{}.current_exp".format(server.id): 0,
                        }
                    },
                    upsert=True,
                )
        except AttributeError:
            pass

    def _truncate_text(self, text, max_length):
        if len(text) > max_length:
            if text.strip("$").isdigit():
                text = int(text.strip("$"))
                return "${:.2E}".format(text)
            return text[: max_length - 3] + "..."
        return text

    # finds the the pixel to center the text
    def _center(self, start, end, text, font):
        dist = end - start
        width = font.getsize(text)[0]
        start_pos = start + ((dist - width) / 2)
        return int(start_pos)

    # calculates required exp for next level
    def _required_exp(self, level: int):
        if level < 0:
            return 0
        return 139 * level + 65

    def _level_exp(self, level: int):
        return level * 65 + 139 * level * (level - 1) // 2

    def _find_level(self, total_exp):
        # this is specific to the function above
        return int((1 / 278) * (9 + math.sqrt(81 + 1112 * total_exp)))

    def char_in_font(self, unicode_char, font):
        for cmap in font["cmap"].tables:
            if cmap.isUnicode():
                if ord(unicode_char) in cmap.cmap:
                    return True
        return False

    @checks.is_owner()
    @sysadm.command()
    @commands.guild_only()
    async def mee6convertlevels(self, ctx, pages: int):
        """Importe les niveaux de l'API de Mee6 et les importe sur le robot."""
        if await self.config.guild(ctx.guild).mentions():
            msg = (
                "**{}, les mentions pour les montées de niveaux sont activées dans le serveur.**\n"
                "Red pingera tous les utilisateurs qui monteront de niveaux à travers ce processus.\n"
                "Répondez `yes` si voulez toutefois continuer la conversion.\n"
                "Sinon répondez `no` et utiliser la commande `{}sysadm mention` pour désactiver les mentions lors des montées de niveaux."
            ).format(ctx.author.display_name, ctx.prefix)
            await ctx.send(msg)
            pred = MessagePredicate.yes_or_no(ctx)
            try:
                await self.bot.wait_for("message", check=pred, timeout=15)
            except TimeoutError:
                return await ctx.send("**Aucune réponse n'a été donné dans le temps imparti, l'action a donc été annulé.**")
            if pred.result is False:
                return await ctx.send("**Action annulé.**")
        failed = 0
        for i in range(pages):
            async with self.session.get(
                f"https://mee6.xyz/api/plugins/levels/leaderboard/{ctx.guild.id}?page={i}&limit=999"
            ) as r:

                if r.status == 200:
                    data = await r.json()
                else:
                    return await ctx.send("Aucune trace de données du serveur n'a été trouvé dans l'API de Mee6.")

            for userdata in data["players"]:
                # _handle_levelup requires a Member
                user = ctx.guild.get_member(int(userdata["id"]))

                if not user:
                    failed += 1
                    continue

                level = userdata["level"]
                server = ctx.guild
                channel = ctx.channel

                # creates user if doesn't exist
                await self._create_user(user, server)
                userinfo = db.users.find_one({"user_id": str(user.id)})

                # get rid of old level exp
                old_server_exp = 0
                for i in range(userinfo["servers"][str(server.id)]["level"]):
                    old_server_exp += self._required_exp(i)
                userinfo["total_exp"] -= old_server_exp
                userinfo["total_exp"] -= userinfo["servers"][str(server.id)]["current_exp"]

                # add in new exp
                total_exp = self._level_exp(level)
                userinfo["servers"][str(server.id)]["current_exp"] = 0
                userinfo["servers"][str(server.id)]["level"] = level
                userinfo["total_exp"] += total_exp

                db.users.update_one(
                    {"user_id": str(user.id)},
                    {
                        "$set": {
                            "servers.{}.level".format(server.id): level,
                            "servers.{}.current_exp".format(server.id): 0,
                            "total_exp": userinfo["total_exp"],
                        }
                    },
                )
                await self._handle_levelup(user, userinfo, server, channel)
        await ctx.send(f"{failed} utilisateurs n'ont pas été trouvé et ont été skippé.")

    @checks.is_owner()
    @sysadm.command()
    @commands.guild_only()
    async def mee6convertranks(self, ctx):
        """Importe les rôles niveaux de l'API de Mee6 et les importe sur le robot Red."""
        async with self.session.get(
            f"https://mee6.xyz/api/plugins/levels/leaderboard/{ctx.guild.id}"
        ) as r:
            if r.status == 200:
                data = await r.json()
            else:
                return await ctx.send("Aucune trace de données de rôle niveau n'a été trouvé dans l'API de Mee6.")
        server = ctx.guild
        remove_role = None
        for role in data["role_rewards"]:
            role_name = role["role"]["name"]
            level = role["rank"]

            role_obj = discord.utils.find(lambda r: r.name == role_name, server.roles)
            if role_obj is None:
                await ctx.send("**Erreur. Assurez-vous que les rôles `{}` existent !**".format(role_name))
            else:
                server_roles = db.roles.find_one({"server_id": str(server.id)})
                if not server_roles:
                    new_server = {
                        "server_id": str(server.id),
                        "roles": {role_name: {"level": str(level), "remove_role": remove_role}},
                    }
                    db.roles.insert_one(new_server)
                else:
                    if role_name not in server_roles["roles"]:
                        server_roles["roles"][role_name] = {}

                    server_roles["roles"][role_name]["level"] = str(level)
                    server_roles["roles"][role_name]["remove_role"] = remove_role
                    db.roles.update_one(
                        {"server_id": str(server.id)}, {"$set": {"roles": server_roles["roles"]}}
                    )

                await ctx.send(
                    "**Le rôle {} a été lié au niveau {}**".format(role_name, level)
                )
