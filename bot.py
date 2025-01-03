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
                description = args[1:-1]  # Remove first and last quotes
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


def create_ascii_table(events):
    """Create an ASCII table for events"""
    if not events:
        return "No upcoming events found!"

    # Define column widths
    id_width = 4
    time_width = 16
    title_width = 30
    category_width = 10
    attendees_width = 5
    total_width = id_width + time_width + title_width + category_width + attendees_width + 9  # 9 for borders

    # Create the header
    header = f"╔{'═' * id_width}╦{'═' * time_width}╦{'═' * title_width}╦{'═' * category_width}╦{'═' * attendees_width}╗"
    title_row = f"║ ID ║{'Date & Time'.center(time_width)}║{'Title'.center(title_width)}║{'Category'.center(category_width)}║{'👥'.center(attendees_width)}║"
    separator = f"╠{'═' * id_width}╬{'═' * time_width}╬{'═' * title_width}╬{'═' * category_width}╬{'═' * attendees_width}╣"
    bottom = f"╚{'═' * id_width}╩{'═' * time_width}╩{'═' * title_width}╩{'═' * category_width}╩{'═' * attendees_width}╝"

    # Start building the table
    table = [header, title_row, separator]

    # Add each event
    for event in events:
        event_id, title, event_time, category, attending_count = event

        # Format the event time
        event_time = datetime.strptime(event_time, "%Y-%m-%d %H:%M:%S")
        formatted_time = event_time.strftime("%Y-%m-%d %H:%M")

        # Truncate title if too long
        display_title = title[:title_width - 3] + "..." if len(title) > title_width else title

        # Create the row
        row = (f"║ {str(event_id).ljust(id_width - 1)}"
               f"║ {formatted_time.ljust(time_width - 1)}"
               f"║ {display_title.ljust(title_width - 1)}"
               f"║ {category.ljust(category_width - 1)}"
               f"║ {str(attending_count).center(attendees_width - 1)}║")
        table.append(row)

    # Add the bottom border
    table.append(bottom)

    # Join all parts with newlines
    return "```\n" + "\n".join(table) + "\n```"


@bot.command()
async def agenda(ctx, days: int = 7):
    """Show upcoming events in an ASCII table format for the next X days (default 7)"""
    conn = sqlite3.connect('calendar_events.db')
    c = conn.cursor()

    current_time = datetime.now()
    end_time = current_time + timedelta(days=days)

    # Get upcoming events within the specified time range
    c.execute("""
        SELECT e.id, e.title, e.event_time, e.category,
               (SELECT COUNT(*) FROM rsvp 
                WHERE event_id = e.id AND status = 'attending') as attending_count
        FROM events e
        WHERE e.guild_id = ?
        AND datetime(e.event_time) >= datetime(?)
        AND datetime(e.event_time) <= datetime(?)
        AND e.is_cancelled = 0
        ORDER BY e.event_time ASC
    """, (ctx.guild.id, current_time, end_time))

    events = c.fetchall()
    conn.close()

    # Create and send the ASCII table
    table = create_ascii_table(events)

    # Add header and footer messages
    header_msg = f"📅 **Agenda for the next {days} days**\n"
    if events:
        footer_msg = f"\nShowing {len(events)} upcoming event(s)"
    else:
        footer_msg = "\nNo upcoming events found"

    await ctx.send(f"{header_msg}{table}{footer_msg}")

@bot.command()
async def list_events(ctx, category: str = None):
    """List all upcoming events, optionally filtered by category"""
    conn = sqlite3.connect('calendar_events.db')
    c = conn.cursor()

    current_time = datetime.now()

    if category:
        c.execute("""
            SELECT id, title, event_time, category, description 
            FROM events 
            WHERE guild_id = ? 
            AND datetime(event_time) >= datetime(?)
            AND is_cancelled = 0 
            AND category = ?
            ORDER BY event_time
        """, (ctx.guild.id, current_time, category))
    else:
        c.execute("""
            SELECT id, title, event_time, category, description 
            FROM events 
            WHERE guild_id = ? 
            AND datetime(event_time) >= datetime(?)
            AND is_cancelled = 0
            ORDER BY event_time
        """, (ctx.guild.id, current_time))

    events = c.fetchall()

    if not events:
        await ctx.send("No upcoming events found!")
        conn.close()
        return

    embed = discord.Embed(
        title="📅 Upcoming Events",
        color=discord.Color.blue()
    )

    for event in events:
        event_id, title, event_time, event_category, description = event
        # Get RSVP count
        c.execute("""
            SELECT COUNT(*) 
            FROM rsvp 
            WHERE event_id = ? AND status = 'attending'
        """, (event_id,))
        attending_count = c.fetchone()[0]

        event_time = datetime.strptime(event_time, "%Y-%m-%d %H:%M:%S")
        field_name = f"{title} (ID: {event_id})"
        field_value = (
            f"📆 {event_time.strftime('%Y-%m-%d %H:%M')}\n"
            f"📁 Category: {event_category}\n"
            f"👥 Attending: {attending_count}\n"
            f"📝 {description[:100]}{'...' if len(description) > 100 else ''}"
        )
        embed.add_field(name=field_name, value=field_value, inline=False)

    conn.close()
    await ctx.send(embed=embed)


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


@bot.command()
async def attendees(ctx, event_id: int):
    """Show who's attending an event"""
    conn = sqlite3.connect('calendar_events.db')
    c = conn.cursor()

    # Get event details
    c.execute("""
        SELECT title, event_time, is_cancelled 
        FROM events 
        WHERE id = ? AND guild_id = ?
    """, (event_id, ctx.guild.id))
    event = c.fetchone()

    if not event:
        await ctx.send("Event not found!")
        conn.close()
        return

    title, event_time, is_cancelled = event

    embed = discord.Embed(
        title=f"Attendees for: {title}",
        description=f"Event Time: {event_time}",
        color=discord.Color.red() if is_cancelled else discord.Color.blue()
    )

    # Get attendees by status
    for status in ['attending', 'maybe', 'not_attending']:
        c.execute("""
            SELECT user_id 
            FROM rsvp 
            WHERE event_id = ? AND status = ?
        """, (event_id, status))
        users = c.fetchall()

        # Convert user IDs to mentions
        user_list = []
        for user_id in users:
            member = ctx.guild.get_member(user_id[0])
            if member:
                user_list.append(member.mention)

        status_emoji = {
            'attending': '👍',
            'maybe': '❓',
            'not_attending': '👎'
        }

        embed.add_field(
            name=f"{status_emoji[status]} {status.replace('_', ' ').title()} ({len(user_list)})",
            value='\n'.join(user_list) if user_list else "None",
            inline=False
        )

    conn.close()
    await ctx.send(embed=embed)


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
        name="!list_events [category]",
        value="Show all upcoming events, optionally filtered by category",
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
        value="React to event messages with:\n👍 - Attending\n❓ - Maybe\n👎 - Not attending\n\nNote: You can also filter events by category using: !list_events [category]",
        inline=False
    )

    await ctx.send(embed=embed)


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


@bot.event
async def on_raw_reaction_add(payload):
    """Handle RSVP reactions"""
    if payload.user_id == bot.user.id:
        return

    conn = sqlite3.connect('calendar_events.db')
    c = conn.cursor()

    # Check if this is an event message
    c.execute('SELECT id FROM events WHERE message_id = ?', (payload.message_id,))
    event = c.fetchone()

    if not event:
        conn.close()
        return

    event_id = event[0]

    # Map reactions to RSVP status
    status_map = {
        '👍': 'attending',
        '❓': 'maybe',
        '👎': 'not_attending'
    }

    emoji = str(payload.emoji)
    if emoji not in status_map:
        conn.close()
        return

    # Update RSVP status
    c.execute("""
        INSERT OR REPLACE INTO rsvp (event_id, user_id, status)
        VALUES (?, ?, ?)
    """, (event_id, payload.user_id, status_map[emoji]))

    conn.commit()
    conn.close()


@bot.event
async def on_raw_reaction_remove(payload):
    """Handle RSVP reaction removals"""
    conn = sqlite3.connect('calendar_events.db')
    c = conn.cursor()

    # Check if this is an event message
    c.execute('SELECT id FROM events WHERE message_id = ?', (payload.message_id,))
    event = c.fetchone()

    if event:
        # Remove RSVP entry
        c.execute("""
            DELETE FROM rsvp 
            WHERE event_id = ? AND user_id = ?
        """, (event[0], payload.user_id))
        conn.commit()

    conn.close()


bot.run(TOKEN)