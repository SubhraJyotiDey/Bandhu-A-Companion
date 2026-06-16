---
name: claw-eye
description: Control the physical animatronic eye mechanism servos, expressions, and room lights via GPIO.
version: 1.0.0
author: Subhr
license: MIT
category: hardware
tags:
  - servos
  - gpio
  - companion
  - eyes
permissions:
  - execute
---

# Claw-Eye Skill

This skill allows the agent to control the physical animatronic eyes and GPIO outputs on the Raspberry Pi.

## Tools

*   `set_eye_mood`: Call this to adjust the overall emotional look and speed of the mechanical eyes.
*   `trigger_expression`: Call this to trigger a quick blink or wink.
*   `toggle_gpio`: Call this to turn on/off relays (e.g. lights) or status LEDs.
*   `set_alarm`: Call this to schedule daily reminders or actions.

## Usage Guidelines

You have physical mechanical eyes and control over GPIO pins.
Whenever the user asks you to change your eye expression, look around differently, wink, blink, or toggle the lights, call the corresponding tools immediately!
