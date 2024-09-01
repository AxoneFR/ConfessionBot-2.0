"""
  Confessions Marketplace - Anonymous buying and selling of goods
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING
import disnake
from disnake.ext import commands

if TYPE_CHECKING:
  from main import MerelyBot
  from babel import Resolvable
  from configparser import SectionProxy

from overlay.extensions.confessions_common import ChannelType, get_guildchannels, ConfessionData


class ConfessionsMarketplace(commands.Cog):
  """ Enable anonymous trade """
  SCOPE = 'confessions'

  @property
  def config(self) -> SectionProxy:
    """ Shorthand for self.bot.config[scope] """
    return self.bot.config[self.SCOPE]

  def babel(self, target:Resolvable, key:str, **values: dict[str, str | bool]) -> str:
    """ Shorthand for self.bot.babel(scope, key, **values) """
    return self.bot.babel(target, self.SCOPE, key, **values)

  def __init__(self, bot:MerelyBot):
    self.bot = bot

    if 'confessions' not in bot.config['extensions']:
      raise Exception("Module `confessions` must be enabled!")

  # Modals

  class OfferModal(disnake.ui.Modal):
    """ Modal that appears when a user wants to make an offer on a listing """
    def __init__(
      self, parent:"ConfessionsMarketplace", origin:disnake.MessageInteraction
    ):
      self.parent = parent
      self.origin = origin
      super().__init__(
        title=parent.babel(origin, 'button_offer', listing=origin.message.embeds[0].title),
        custom_id="listing_offer",
        components=[
          disnake.ui.TextInput(
            label=parent.babel(origin, 'offer_price_label'),
            placeholder=parent.babel(origin, 'offer_price_example'),
            custom_id='offer_price',
            style=disnake.TextInputStyle.single_line,
            min_length=3,
            max_length=30
          ),
          disnake.ui.TextInput(
            label=parent.babel(origin, 'offer_method_label'),
            placeholder=parent.babel(origin, 'offer_method_example'),
            custom_id='offer_method',
            style=disnake.TextInputStyle.single_line,
            min_length=3,
            max_length=30
          )
        ]
      )

    async def callback(self, inter:disnake.ModalInteraction):
      guildchannels = get_guildchannels(self.parent.config, inter.guild_id)
      if (
        inter.channel_id not in guildchannels or
        guildchannels[inter.channel_id] != ChannelType.marketplace()
      ):
        await inter.send(self.parent.babel(inter, 'nosendchannel'), ephemeral=True)
        return

      embed = disnake.Embed(
        title=self.parent.babel(self.origin, 'offer_for', listing=self.origin.message.embeds[0].title)
      )
      embed.add_field('Offer price:', inter.text_values['offer_price'], inline=True)
      embed.add_field('Offer payment method:', inter.text_values['offer_method'], inline=True)
      embed.set_footer(text=self.parent.babel(inter, 'shop_disclaimer'))

      pendingconfession = ConfessionData(self.parent.bot.cogs['Confessions'])
      pendingconfession.create(inter.author, inter.channel, reference=self.origin.message)
      pendingconfession.set_content(embed=embed)
      pendingconfession.channeltype_flags = 2

      if vetting := await pendingconfession.check_vetting(inter):
        await self.parent.bot.cogs['ConfessionsModeration'].send_vetting(
          inter, pendingconfession, vetting
        )
        return
      if vetting is False:
        return
      await pendingconfession.send_confession(inter, True, webhook_override=False)

  # Events

  @commands.Cog.listener('on_button_click')
  async def check_button_click(self, inter:disnake.MessageInteraction):
    """ Check the button press events and handle relevant ones """
    if inter.data.custom_id.startswith('confessionmarketplace_offer'):
      return await self.on_create_offer(inter)
    if inter.data.custom_id.startswith('confessionmarketplace_accept'):
      return await self.on_accept_offer(inter)
    if inter.data.custom_id.startswith('confessionmarketplace_withdraw'):
      return await self.on_withdraw(inter)

  async def on_create_offer(self, inter:disnake.MessageInteraction):
    """ Open the offer form when a user wants to make an offer on a listing """
    if len(inter.message.embeds) == 0:
      await inter.send(self.babel(inter, 'error_embed_deleted'), ephemeral=True)
      return
    if len(inter.data.custom_id) < 30:
      await inter.send(self.babel(inter, 'error_old_offer'), ephemeral=True)
      return
    id_seller = inter.data.custom_id[28:]
    id_buyer = (
      self.bot.cogs['Confessions'].crypto.encrypt(inter.author.id.to_bytes(8, 'big')).decode('ascii')
    )
    if id_seller == id_buyer:
      await inter.send(self.babel(inter, 'error_self_offer'), ephemeral=True)
      return
    await inter.response.send_modal(self.OfferModal(self, inter))

  async def on_accept_offer(self, inter:disnake.MessageInteraction):
    listing = await inter.channel.fetch_message(inter.message.reference.message_id)
    if len(listing.embeds) == 0 or len(inter.message.embeds) == 0:
      await inter.send(self.babel(inter, 'error_embed_deleted'), ephemeral=True)
      return
    if len(inter.data.custom_id) < 31:
      await inter.send(self.babel(inter, 'error_old_offer'), ephemeral=True)
      return
    encrypted_data = inter.data.custom_id[29:].split('_')

    seller_id = int.from_bytes(self.bot.cogs['Confessions'].crypto.decrypt(encrypted_data[0]), 'big')
    buyer_id = int.from_bytes(self.bot.cogs['Confessions'].crypto.decrypt(encrypted_data[1]), 'big')
    if seller_id == inter.author.id:
      seller = inter.author
      buyer = await inter.guild.getch_member(buyer_id)
    else:
      await inter.send(self.babel(inter, 'error_wrong_person', buy=True), ephemeral=True)
      return
    receipts = [listing.embeds[0], inter.message.embeds[0]]
    await inter.response.defer()
    await seller.send(self.babel(
      inter, 'sale_complete',
      listing=listing.embeds[0].title,
      sell=True,
      other=buyer.mention
    ), embeds=receipts)
    await buyer.send(self.babel(
      inter, 'sale_complete',
      listing=listing.embeds[0].title,
      sell=True,
      other=seller.mention
    ), embeds=receipts)
    await inter.message.edit(content=self.babel(inter, 'offer_accepted'), components=None)

  async def on_withdraw(self, inter:disnake.MessageInteraction):
    encrypted_data = inter.data.custom_id[31:].split('_')
    owner_id = int.from_bytes(self.bot.cogs['Confessions'].crypto.decrypt(encrypted_data[-1]), 'big')
    if owner_id != inter.author.id:
      await inter.send(self.babel(inter, 'error_wrong_person', buy=False), ephemeral=True)
      return
    if len(encrypted_data) == 1: # listing
      await inter.message.edit(
        content=self.babel(inter, 'listing_withdrawn'),
        components=None
      )
    elif len(encrypted_data) == 2: # offer
      await inter.message.edit(
        content=self.babel(inter, 'offer_withdrawn'),
        components=None
      )
    else:
      raise Exception("Unknown state encountered!", len(encrypted_data))

  # Slash commands

  @commands.cooldown(1, 1, type=commands.BucketType.user)
  @commands.slash_command()
  async def sell(
    self,
    inter: disnake.GuildCommandInteraction,
    title: str = commands.Param(max_length=80),
    starting_price: str = commands.Param(max_length=10),
    payment_methods: str = commands.Param(min_length=3, max_length=60),
    description: Optional[str] = commands.Param(default=None, max_length=1000),
    image: Optional[disnake.Attachment] = None
  ):
    """
      Start an anonymous listing

      Parameters
      ----------
      title: A short summary of the item you are selling
      starting_price: The price you would like to start bidding at, in whatever currency you accept
      payment_methods: Payment methods you will accept, PayPal, Venmo, Crypto, etc.
      description: Further details about the item you are selling
      image: A picture of the item you are selling
    """
    guildchannels = get_guildchannels(self.config, inter.guild_id)
    if inter.channel_id not in guildchannels:
      await inter.send(self.babel(inter, 'nosendchannel'), ephemeral=True)
      return
    if guildchannels[inter.channel_id] != ChannelType.marketplace():
      await inter.send(self.babel(inter, 'wrongcommand', cmd='confess'), ephemeral=True)
      return

    clean_desc = description.replace('# ', '') if description else '' # TODO: do this with regex
    embed = disnake.Embed(title=title, description=clean_desc)
    embed.add_field('Starting price:', starting_price, inline=True)
    embed.add_field('Accepted payment methods:', payment_methods, inline=True)
    embed.set_footer(text=self.babel(inter, 'shop_disclaimer'))

    pendingconfession = ConfessionData(self.bot.cogs['Confessions'])
    pendingconfession.create(inter.author, inter.channel)
    pendingconfession.set_content(embed=embed)
    if image:
      await inter.response.defer(ephemeral=True)
      await pendingconfession.add_image(attachment=image)
    pendingconfession.channeltype_flags = 1

    if vetting := await pendingconfession.check_vetting(inter):
      await self.bot.cogs['ConfessionsModeration'].send_vetting(inter, pendingconfession, vetting)
      return
    if vetting is False:
      return
    await pendingconfession.send_confession(inter, True, webhook_override=False)

  # Special ChannelType code
  async def on_channeltype_send(
    self, inter:disnake.Interaction, data:ConfessionData
  ) -> dict[str] | bool:
    """ Add some custom buttons below messages headed for a marketplace channnel """
    if data.channeltype_flags == 1:
      id_seller = data.parent.crypto.encrypt(data.author.id.to_bytes(8, 'big')).decode('ascii')
      return {
        'use_webhook': False,
        'components': [disnake.ui.Button(
          label=self.babel(inter.guild, 'button_offer', listing=None),
          custom_id='confessionmarketplace_offer_'+id_seller,
          emoji='💵',
          style=disnake.ButtonStyle.blurple
        ), disnake.ui.Button(
          label=self.babel(inter.guild, 'button_withdraw', sell=True),
          custom_id='confessionmarketplace_withdraw_'+id_seller,
          style=disnake.ButtonStyle.grey
        )]
      }
    elif data.channeltype_flags == 2:
      listing = await data.targetchannel.fetch_message(data.reference_id)
      id_seller = listing.components[0].children[0].custom_id[28:]
      id_buyer = data.parent.crypto.encrypt(data.author.id.to_bytes(8, 'big')).decode('ascii')
      return {
        'use_webhook': False,
        'components': [disnake.ui.Button(
          label=self.babel(inter.guild, 'button_accept', listing=None),
          custom_id='confessionmarketplace_accept_'+id_seller+'_'+id_buyer,
          emoji='✅',
          style=disnake.ButtonStyle.gray
        ), disnake.ui.Button(
          label=self.babel(inter.guild, 'button_withdraw', sell=False),
          custom_id='confessionmarketplace_withdraw_'+id_buyer,
          style=disnake.ButtonStyle.grey
        )]
      }
    else:
      raise Exception("Unknown state encountered!", data.channeltype_flags)


def setup(bot:MerelyBot) -> None:
  """ Bind this cog to the bot """
  bot.add_cog(ConfessionsMarketplace(bot))
