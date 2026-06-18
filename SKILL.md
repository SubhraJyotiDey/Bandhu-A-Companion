---
name: claw-eye
description: Control the physical animatronic eye mechanism, servo expressions, gestures, GPIO outputs, and alarms on the Raspberry Pi companion robot.
---

# Claw-Eye Companion Controller

You have physical mechanical animatronic eyes and control over GPIO pins on a Raspberry Pi. Use the shell commands below to control them. **Always run the appropriate command immediately when the user asks.**

## Commands

All commands use this base format:

```
/home/pi/joyful-meitner/venv/bin/python /home/pi/joyful-meitner/main.py execute <tool_name> <args>
```

### Set Eye Mood

Change the overall emotional mood of the mechanical eyes. This adjusts gaze velocity, blink frequency, and eyelid openings.

```bash
/home/pi/joyful-meitner/venv/bin/python /home/pi/joyful-meitner/main.py execute set_eye_mood mood=<MOOD>
```

Valid moods: `neutral`, `happy`, `sad`, `angry`, `bored`, `excited`, `surprised`

### Trigger Expression

Trigger an instant eye expression for emotional punctuation.

```bash
/home/pi/joyful-meitner/venv/bin/python /home/pi/joyful-meitner/main.py execute trigger_expression expression=<EXPRESSION>
```

Valid expressions: `blink`, `wink_left`, `wink_right`, `close_eyes`, `open_eyes`

### Play Gesture

Play a predefined choreographed eye gesture sequence.

```bash
/home/pi/joyful-meitner/venv/bin/python /home/pi/joyful-meitner/main.py execute play_gesture gesture=<GESTURE>
```

Valid gestures: `startup`, `nod`, `shake`, `think`, `shock`, `scanning`

- Use `nod` to confirm or agree
- Use `shake` to disagree or deny
- Use `think` when pondering
- Use `shock` for surprise reactions
- Use `scanning` to look around alertly

### Toggle GPIO

Turn a physical Raspberry Pi GPIO output pin ON or OFF. Use this to control relays, room lights, or status LEDs.

```bash
/home/pi/joyful-meitner/venv/bin/python /home/pi/joyful-meitner/main.py execute toggle_gpio pin=<PIN> state=<STATE>
```

- Pin `17` = Room Light Relay
- Pin `27` = Companion Status LED
- State: `on` or `off`

### Set Alarm

Schedule a daily recurring alarm. The companion will execute a task at the specified time.

```bash
/home/pi/joyful-meitner/venv/bin/python /home/pi/joyful-meitner/main.py execute set_alarm id=<ALARM_ID> time=<HH:MM> task=<TASK>
```

- `id`: A unique key like `morning_wake`
- `time`: 24-hour format, e.g. `08:30`
- `task`: Format is `say: <message>` or `toggle_gpio: <pin>:<state>`

### Get Status

Retrieve the companion's current status including mood, GPIO pin states, and scheduled alarms.

```bash
/home/pi/joyful-meitner/venv/bin/python /home/pi/joyful-meitner/main.py execute get_status
```

## Behavior Rules

- When the user asks to change mood, expression, or eyes — run the appropriate command immediately.
- When the user asks to turn on/off the light — use `toggle_gpio` with pin `17`.
- When the user asks to nod, shake head, or react — use `play_gesture`.
- When the user asks to wink, blink, sleep, wake up, close, or open eyes — use `trigger_expression` with `blink`, `wink_left`, `wink_right`, `close_eyes`, or `open_eyes`.
- When the user asks about status — use `get_status`.
- When the user asks to set a reminder or alarm — use `set_alarm`.
- **Do not ask clarifying questions. Act immediately.**
