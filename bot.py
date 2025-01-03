import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import sqlite3
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
bot = commands.Bot(command_prefix='!', intents=intents)


def setup_database():
    conn = sqlite3.connect('calendar_events.db')
    c = conn.cursor()

    # Drop existing tables
    c.execute('DROP TABLE IF EXISTS rsvp')
    c.execute('DROP TABLE IF EXISTS reminders')
    c.execute('DROP TABLE IF EXISTS events')

    # Create tables with updated structure
    c.execute('''CREATE TABLE IF NOT EXISTS events
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  guild_id INTEGER,
                  channel_id INTEGER,
                  creator_id INTEGER,
                  title TEXT,
                  description TEXT,
                  event_time TIMESTAMP,
                  message_id INTEGER,
                  category TEXT DEFAULT 'general',
                  is_cancelled BOOLEAN DEFAULT 0)''')

    c.execute('''CREATE TABLE IF NOT EXISTS rsvp
                 (event_id INTEGER,
                  user_id INTEGER,
                  status TEXT,
                  FOREIGN KEY(event_id) REFERENCES events(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS reminders
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  event_id INTEGER,
                  reminder_time INTEGER,
                  notification_sent BOOLEAN DEFAULT 0,
                  FOREIGN KEY(event_id) REFERENCES events(id))''')
    conn.commit()
    conn.close()


@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    setup_database()
    check_reminders.start()


async def check_time_conflicts(event_time, guild_id, conn):
    """Check for existing events at the same time"""
    c = conn.cursor()
    # Check for events within 1 hour before or after the proposed time
    time_before = (datetime.strptime(event_time, "%Y-%m-%d %H:%M") - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    time_after = (datetime.strptime(event_time, "%Y-%m-%d %H:%M") + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")

    c.execute("""
        SELECT title, event_time
        FROM events
        WHERE guild_id = ?
        AND event_time BETWEEN ? AND ?
        AND is_cancelled = 0
    """, (guild_id, time_before, time_after))

    return c.fetchall()


@bot.command()
async def create_event(ctx, title: str, date: str, time: str, *, args: str = ""):
    """Create a new event with optional category and reminders"""
    try:
        # Parse arguments
        description = "No description provided"
        category = "general"
        reminders = [30]  # Default 30-minute reminder

        if args:
            if args.startswith('"') and args.endswith('"'):
                description = args.strip('"')
            else:
                parts = args.split('--')
                for part in parts:
                    part = part.strip()
                    if part.startswith('desc '):
                        description = part[5:]
                    elif part.startswith('cat '):
                        category = part[4:]
                    elif part.startswith('remind '):
                        try:
                            reminders = [int(x.strip()) for x in part[7:].split(',')]
                        except ValueError:
                            await ctx.send("Invalid reminder format. Using default 30-minute reminder.")
                            reminders = [30]

        event_datetime = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")

        # Check for conflicts
        conn = sqlite3.connect('calendar_events.db')
        conflicts = await check_time_conflicts(f"{date} {time}", ctx.guild.id, conn)

        if conflicts:
            conflict_msg = "⚠️ There are existing events around this time:\n"
            for event in conflicts:
                conflict_msg += f"- {event[0]} at {event[1]}\n"
            conflict_msg += "\nWould you like to schedule anyway?"

            confirm_msg = await ctx.send(conflict_msg)
            await confirm_msg.add_reaction('✅')
            await confirm_msg.add_reaction('❌')

            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ['✅',
                                                                      '❌'] and reaction.message.id == confirm_msg.id

            try:
                reaction, user = await bot.wait_for('reaction_add', timeout=30.0, check=check)
                if str(reaction.emoji) == '❌':
                    await ctx.send("Event creation cancelled.")
                    conn.close()
                    return
            except TimeoutError:
                await ctx.send("No confirmation received within 30 seconds. Event creation cancelled.")
                conn.close()
                return

        event_datetime = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")

        conn = sqlite3.connect('calendar_events.db')
        c = conn.cursor()

        # Create event
        c.execute('''INSERT INTO events (guild_id, channel_id, creator_id, title, description, event_time, category)
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (ctx.guild.id, ctx.channel.id, ctx.author.id, title, description, event_datetime, category))
        event_id = c.lastrowid

        # Add reminders
        for reminder in reminders:
            c.execute('INSERT INTO reminders (event_id, reminder_time) VALUES (?, ?)',
                      (event_id, reminder))

        conn.commit()

        embed = discord.Embed(title="Event Created", color=discord.Color.green())
        embed.add_field(name="Title", value=title)
        embed.add_field(name="Category", value=category)
        embed.add_field(name="Date & Time", value=event_datetime.strftime("%Y-%m-%d %H:%M"))
        embed.add_field(name="Description", value=description)
        embed.add_field(name="Reminders", value=f"{', '.join(f'{r} minutes' for r in reminders)} before event")
        embed.set_footer(text=f"Event ID: {event_id}\nReact with 👍 to attend, ❓ for maybe, 👎 for not attending")

        message = await ctx.send(embed=embed)

        # Save message ID
        c.execute('UPDATE events SET message_id = ? WHERE id = ?', (message.id, event_id))
        conn.commit()
        conn.close()

        # Add reaction options
        await message.add_reaction('👍')
        await message.add_reaction('❓')
        await message.add_reaction('👎')

    except ValueError:
        await ctx.send("Invalid date/time format. Please use: YYYY-MM-DD HH:MM")


@bot.command()
async def cancel_event(ctx, event_id: int):
    """Cancel an event"""
    conn = sqlite3.connect('calendar_events.db')
    c = conn.cursor()

    # Check if user is the event creator
    c.execute('SELECT creator_id, title, channel_id FROM events WHERE id = ?', (event_id,))
    event = c.fetchone()

    if not event:
        await ctx.send("Event not found!")
        return

    if event[0] != ctx.author.id:
        await ctx.send("Only the event creator can cancel this event!")
        return

    # Mark event as cancelled
    c.execute('UPDATE events SET is_cancelled = 1 WHERE id = ?', (event_id,))
    conn.commit()

    # Send cancellation notification
    channel = bot.get_channel(event[2])
    if channel:
        # Get all attendees
        c.execute('SELECT user_id FROM rsvp WHERE event_id = ? AND status = "attending"', (event_id,))
        attendees = c.fetchall()

        embed = discord.Embed(
            title="🚫 Event Cancelled",
            description=f"The event '{event[1]}' has been cancelled.",
            color=discord.Color.red()
        )

        # Notify attendees
        mention_str = ' '.join([f"<@{uid[0]}>" for uid in attendees])
        if mention_str:
            await channel.send(f"Attention {mention_str}, event cancelled:", embed=embed)
        else:
            await channel.send(embed=embed)

    conn.close()
    await ctx.send(f"Event {event_id} has been cancelled.")


@tasks.loop(minutes=1)
async def check_reminders():
    """Check for upcoming events and send reminders"""
    conn = sqlite3.connect('calendar_events.db')
    c = conn.cursor()

    current_time = datetime.now()

    # Get all active reminders
    c.execute("""
        SELECT e.id, e.title, e.channel_id, e.event_time, r.reminder_time, r.id
        FROM events e
        JOIN reminders r ON e.id = r.event_id
        WHERE e.is_cancelled = 0
        AND r.notification_sent = 0
        AND datetime(e.event_time, '-' || r.reminder_time || ' minutes') <= ?
    """, (current_time.strftime("%Y-%m-%d %H:%M:%S"),))

    reminders = c.fetchall()

    for event_id, title, channel_id, event_time, reminder_time, reminder_id in reminders:
        channel = bot.get_channel(channel_id)
        if channel:
            # Get attendees
            c.execute('SELECT user_id FROM rsvp WHERE event_id = ? AND status = "attending"', (event_id,))
            attendees = c.fetchall()

            embed = discord.Embed(
                title=f"⏰ Reminder: {title}",
                description=f"Event starting in {reminder_time} minutes!",
                color=discord.Color.orange()
            )

            # Ping attendees
            mention_str = ' '.join([f"<@{uid[0]}>" for uid in attendees])
            if mention_str:
                await channel.send(f"🔔 {mention_str}", embed=embed)
            else:
                await channel.send(embed=embed)

            # Mark reminder as sent
            c.execute("UPDATE reminders SET notification_sent = 1 WHERE id = ?", (reminder_id,))
            conn.commit()

    conn.close()


@bot.command()
async def help_calendar(ctx):
    """Show help information about calendar commands"""
    embed = discord.Embed(
        title="Calendar Bot Commands",
        description="Here are all available commands:",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="!create_event \"Title\" YYYY-MM-DD HH:MM [options]",
        value="Create a new event. Options:\n"
              "--desc Description\n"
              "--cat Category\n"
              "--remind 30,60 (minutes before event)\n"
              "Example: !create_event \"Team Meeting\" 2025-01-03 15:00 --desc \"Weekly sync\" --cat work --remind 15,30,60",
        inline=False
    )

    embed.add_field(
        name="!list_events",
        value="Show all upcoming events",
        inline=False
    )

    embed.add_field(
        name="!attendees <event_id>",
        value="Show who's attending an event",
        inline=False
    )

    embed.add_field(
        name="!cancel_event <event_id>",
        value="Cancel an event (only the creator can do this)",
        inline=False
    )

    embed.add_field(
        name="RSVP System",
        value="React to event messages with:\n👍 - Attending\n❓ - Maybe\n👎 - Not attending",
        inline=False
    )

    await ctx.send(embed=embed)


# Keep existing command implementations (ping, attendees, list_events, RSVP handling)
# [Previous implementations remain the same]

bot.run(TOKEN)