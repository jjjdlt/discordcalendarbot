# Discord Calendar Bot

A Discord bot that helps manage events and schedules within your server. Features include event creation, RSVP management, reminders, and an agenda view.

## Features

- Create events with titles, descriptions, and categories
- Set custom reminders for events
- RSVP system with reactions (attending/maybe/not attending)
- View upcoming events in list or agenda format
- Filter events by category
- Automatic conflict detection
- Automatic reminders before events

## Commands

- `!create_event "Title" YYYY-MM-DD HH:MM [options]` - Create a new event
  - Options:
    - `--desc "Description"`
    - `--cat Category`
    - `--remind 30,60` (minutes before event)
- `!list_events [category]` - Show all upcoming events
- `!agenda [days]` - Show upcoming events in an ASCII table format
- `!attendees <event_id>` - Show who's attending an event
- `!cancel_event <event_id>` - Cancel an event
- `!help_calendar` - Show help information

## Setup

1. Clone the repository
2. Install requirements: `pip install -r requirements.txt`
3. Create a `.env` file with your Discord bot token: DISCORD_TOKEN=your_token_here
4. Run the bot: `python bot.py`

## Required Permissions

The bot requires the following permissions:
- Read Messages/View Channels
- Send Messages
- Add Reactions
- Read Message History
- View Members